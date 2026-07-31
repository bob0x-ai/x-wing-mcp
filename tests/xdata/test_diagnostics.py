from xdata.contracts import Post, ProviderResult
from xdata.diagnostics import (
    doctor_summary_from_healthcheck,
    doctor_summary_from_status,
    provider_health_report,
    provider_status_report,
    task_coverage_summary,
)
from xdata.providers.stub import StubProvider


class _Provider:
    def __init__(self, name: str, result: ProviderResult, *, status: dict | None = None):
        self.name = name
        self.result = result
        self._status = status or {
            "auth_required": True,
            "auth_present": True,
            "supports_tasks": ["read_user_posts_recent", "search_posts"],
        }

    def status(self):
        return dict(self._status)

    def read_user_posts(self, user, *, limit=20):
        del user, limit
        return self.result

    def search_posts(self, query, *, limit=20):
        del query, limit
        return self.result


def test_provider_status_report_interprets_auth_and_warnings():
    provider = _Provider(
        "socialdata",
        ProviderResult.ok(provider="socialdata", items=[Post(id="1", text="ok")]),
        status={
            "auth_required": True,
            "auth_present": False,
            "supports_tasks": ["search_posts"],
            "cooldown_active": True,
        },
    )

    report = provider_status_report("socialdata", provider)

    assert report["auth_present"] is False
    assert report["auth_valid"] is False
    assert "missing_credentials" in report["warnings"]
    assert "cooldown_active" in report["warnings"]


def test_provider_health_report_runs_live_probe():
    provider = _Provider(
        "socialdata",
        ProviderResult.ok(provider="socialdata", items=[Post(id="1", text="ok")]),
    )

    report = provider_health_report("socialdata", provider, mode="live")

    assert report["probe_ok"] is True
    assert report["usable"] is True
    assert report["probes"][0]["task"] == "read_user_posts"
    assert report["probes"][0]["item_count"] == 1


def test_provider_health_report_classifies_probe_failures():
    provider = _Provider(
        "twikit",
        ProviderResult.unavailable(provider="twikit", reason="session_invalid"),
        status={
            "auth_required": True,
            "session_file_exists": True,
            "supports_tasks": ["read_user_posts_recent", "search_posts"],
        },
    )

    report = provider_health_report("twikit", provider, mode="live")

    assert report["probe_ok"] is False
    assert report["auth_valid"] is False
    assert report["probes"][0]["failure_class"] == "session_invalid"
    assert report["usable"] is False


def test_task_coverage_summary_prefers_healthy_candidates():
    coverage = task_coverage_summary(
        {
            "search_posts": ["socialdata", "twikit", "stub"],
            "read_thread": ["stub"],
        },
        {
            "socialdata": {
                "supports_tasks": ["search_posts"],
                "usable": False,
            },
            "twikit": {
                "supports_tasks": ["search_posts"],
                "usable": True,
            },
            "stub": {
                "supports_tasks": [],
                "usable": False,
            },
        },
    )

    assert coverage["search_posts"]["healthy_candidates"] == ["twikit"]
    assert coverage["search_posts"]["preferred_provider"] == "twikit"
    assert coverage["read_thread"]["available"] is False


def test_stub_provider_is_not_usable():
    report = provider_health_report("xpoz", StubProvider("xpoz"), mode="live")

    assert report["implemented"] is False
    assert report["usable"] is False


def test_doctor_summary_from_status_prioritizes_blockers():
    summary = doctor_summary_from_status(
        {
            "providers": {
                "twikit": {
                    "usable": True,
                    "warnings": [],
                    "auth_required": True,
                    "auth_present": True,
                    "auth_valid": True,
                    "status": {
                        "cookie_expiry": {
                            "auth_token": {"days_remaining": 23.1},
                        }
                    },
                }
            },
            "task_coverage": {
                "search_posts": {"available": True, "preferred_provider": "twikit"},
                "read_thread": {"available": False, "configured_candidates": ["twikit"]},
            },
        }
    )

    assert summary["overall"] == "degraded"
    assert "read_thread: no healthy provider" in summary["blockers"]
    assert "twikit: cookie expires in 23.1 days" in summary["provider_notes"]


def test_doctor_summary_ignores_disabled_or_stub_providers_in_overall_health():
    summary = doctor_summary_from_status(
        {
            "providers": {
                "syndication": {
                    "implemented": True,
                    "enabled": True,
                    "usable": True,
                    "warnings": [],
                    "auth_required": False,
                    "auth_present": None,
                    "auth_valid": True,
                    "status": {},
                },
                "xpoz": {
                    "implemented": False,
                    "enabled": False,
                    "usable": False,
                    "warnings": [],
                    "auth_required": False,
                    "auth_present": None,
                    "auth_valid": True,
                    "status": {},
                },
            },
            "task_coverage": {
                "fetch_urls": {"available": True, "preferred_provider": "syndication"},
            },
        }
    )

    assert summary["overall"] == "healthy"
    assert summary["degraded_providers"] == []


def test_doctor_summary_from_healthcheck_summarizes_probe_failures():
    summary = doctor_summary_from_healthcheck(
        {
            "providers": {
                "twikit": {
                    "usable": False,
                    "probe_ok": False,
                    "warnings": ["session_invalid"],
                    "probes": [
                        {
                            "name": "recent_user_posts",
                            "ok": False,
                            "reason": "session_invalid",
                            "failure_class": "session_invalid",
                        }
                    ],
                }
            },
            "task_coverage": {
                "read_user_posts_recent": {
                    "available": False,
                    "configured_candidates": ["twikit"],
                    "preferred_provider": None,
                }
            },
        }
    )

    assert summary["overall"] == "blocked"
    assert summary["failed_probes"] == 1
    assert summary["provider_summaries"]["twikit"]["failed_probes"][0]["reason"] == "session_invalid"

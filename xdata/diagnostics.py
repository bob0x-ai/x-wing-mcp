"""Provider diagnostics and active health checks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from xdata.contracts import ProviderResult
from xdata.providers.stub import StubProvider


DEFAULT_PROBE_USER = "@OpenAI"
DEFAULT_PROBE_QUERY = "from:OpenAI"


@dataclass(frozen=True)
class ProbeSpec:
    name: str
    method_name: str
    kwargs: dict[str, Any]
    ok_statuses: tuple[str, ...] = ("ok", "empty")


PROVIDER_PROBES: dict[str, tuple[ProbeSpec, ...]] = {
    "syndication": (
        ProbeSpec(
            name="recent_user_posts",
            method_name="read_user_posts",
            kwargs={"user": DEFAULT_PROBE_USER, "limit": 1},
            ok_statuses=("ok", "empty"),
        ),
    ),
    "socialdata": (
        ProbeSpec(
            name="recent_user_posts",
            method_name="read_user_posts",
            kwargs={"user": DEFAULT_PROBE_USER, "limit": 1},
            ok_statuses=("ok", "empty"),
        ),
        ProbeSpec(
            name="search_posts",
            method_name="search_posts",
            kwargs={"query": DEFAULT_PROBE_QUERY, "limit": 1},
            ok_statuses=("ok", "empty"),
        ),
    ),
    "official_x": (
        ProbeSpec(
            name="owned_timeline",
            method_name="read_owned_timeline",
            kwargs={"limit": 1},
            ok_statuses=("ok", "empty"),
        ),
    ),
    "twikit": (
        ProbeSpec(
            name="recent_user_posts",
            method_name="read_user_posts",
            kwargs={"user": DEFAULT_PROBE_USER, "limit": 1},
            ok_statuses=("ok", "empty"),
        ),
        ProbeSpec(
            name="search_posts",
            method_name="search_posts",
            kwargs={"query": DEFAULT_PROBE_QUERY, "limit": 1},
            ok_statuses=("ok", "empty"),
        ),
    ),
}

TASK_METHODS: dict[str, tuple[str, ...]] = {
    "fetch_urls": ("fetch_urls",),
    "read_user_posts_recent": ("read_user_posts",),
    "search_posts": ("search_posts", "search_recent"),
    "read_owned_timeline": ("read_owned_timeline",),
    "read_mentions": ("read_mentions",),
    "read_thread": ("read_thread",),
    "read_replies": ("read_replies",),
    "read_quotes": ("read_quotes",),
    "read_follow_graph": ("read_follow_graph",),
    "read_article": ("read_article",),
    "collect_posts": ("collect_posts",),
}


def provider_status_report(name: str, provider: Any) -> dict[str, Any]:
    implemented = not isinstance(provider, StubProvider)
    status_method = getattr(provider, "status", None)
    raw_status = status_method() if callable(status_method) else {}
    auth_required = bool(raw_status.get("auth_required", False))
    auth_present = _infer_auth_present(name, raw_status)
    auth_valid = _infer_auth_valid(raw_status, auth_required=auth_required, auth_present=auth_present)
    warnings = list(raw_status.get("expiry_warnings") or [])
    if raw_status.get("cooldown_active"):
        warnings.append("cooldown_active")
    if auth_required and auth_present is False:
        warnings.append("missing_credentials")
    supports_tasks = list(raw_status.get("supports_tasks") or _infer_supported_tasks(provider))
    report = {
        "provider": name,
        "implemented": implemented,
        "enabled": bool(raw_status.get("enabled", implemented)),
        "configured": bool(raw_status.get("configured", implemented)),
        "class": provider.__class__.__name__,
        "auth_required": auth_required,
        "auth_present": auth_present,
        "auth_valid": auth_valid,
        "supports_tasks": supports_tasks,
        "cooldown_active": bool(raw_status.get("cooldown_active", False)),
        "warnings": sorted(set(str(item) for item in warnings)),
        "status": raw_status,
    }
    report["usable"] = _usable_from_report(report)
    return report


def provider_health_report(name: str, provider: Any, *, mode: str = "live") -> dict[str, Any]:
    report = provider_status_report(name, provider)
    report["probe_mode"] = mode
    report["probes"] = []
    report["probe_supported"] = bool(PROVIDER_PROBES.get(name))
    report["probe_ok"] = None

    if mode == "basic" or not report["implemented"] or not report["probe_supported"]:
        report["usable"] = _usable_from_report(report)
        return report

    specs = list(PROVIDER_PROBES.get(name) or [])
    if mode == "live":
        specs = specs[:1]

    probes = [_run_probe(provider, spec) for spec in specs]
    report["probes"] = probes
    report["probe_ok"] = all(probe["ok"] for probe in probes) if probes else None
    report["warnings"] = sorted(
        set([*report["warnings"], *[warning for probe in probes for warning in probe["warnings"]]])
    )
    if report["auth_required"]:
        report["auth_valid"] = _merge_auth_valid(report["auth_valid"], probes)
    report["usable"] = _usable_from_report(report)
    return report


def task_coverage_summary(
    routes: dict[str, list[str]],
    provider_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for task, route in routes.items():
        candidates: list[str] = []
        usable: list[str] = []
        for provider_name in route:
            report = provider_reports.get(provider_name)
            if not report:
                continue
            if task not in list(report.get("supports_tasks") or []):
                continue
            candidates.append(provider_name)
            if report.get("usable") is True:
                usable.append(provider_name)
        coverage[task] = {
            "configured_candidates": candidates,
            "healthy_candidates": usable,
            "available": bool(usable),
            "preferred_provider": usable[0] if usable else None,
        }
    return coverage


def doctor_summary_from_status(status_payload: dict[str, Any]) -> dict[str, Any]:
    providers = status_payload.get("providers") or {}
    task_coverage = status_payload.get("task_coverage") or {}
    blockers: list[str] = []
    warnings: list[str] = []
    healthy_tasks: list[str] = []
    degraded_tasks: list[str] = []
    unavailable_tasks: list[str] = []

    for task, coverage in task_coverage.items():
        if coverage.get("available"):
            healthy_tasks.append(task)
        elif coverage.get("configured_candidates"):
            degraded_tasks.append(task)
            blockers.append(f"{task}: no healthy provider")
        else:
            unavailable_tasks.append(task)
            blockers.append(f"{task}: no implemented provider")

    provider_notes = []
    healthy_providers = []
    degraded_providers = []
    for name, report in providers.items():
        if not report.get("enabled", True) or not report.get("implemented", True):
            continue
        if report.get("usable"):
            healthy_providers.append(name)
        else:
            degraded_providers.append(name)
        for warning in report.get("warnings") or []:
            warnings.append(f"{name}: {warning}")
        auth_required = report.get("auth_required")
        auth_present = report.get("auth_present")
        auth_valid = report.get("auth_valid")
        if auth_required and auth_present is False:
            provider_notes.append(f"{name}: missing credentials")
        elif auth_required and auth_valid is False:
            provider_notes.append(f"{name}: credentials invalid")
        expiry = (report.get("status") or {}).get("cookie_expiry") or {}
        auth_cookie = expiry.get("auth_token")
        if isinstance(auth_cookie, dict) and isinstance(auth_cookie.get("days_remaining"), (int, float)):
            provider_notes.append(f"{name}: cookie expires in {auth_cookie['days_remaining']} days")

    overall = "healthy"
    if unavailable_tasks:
        overall = "blocked"
    elif degraded_tasks or degraded_providers:
        overall = "degraded"

    return {
        "overall": overall,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "healthy_tasks": sorted(healthy_tasks),
        "degraded_tasks": sorted(degraded_tasks),
        "unavailable_tasks": sorted(unavailable_tasks),
        "healthy_providers": sorted(healthy_providers),
        "degraded_providers": sorted(degraded_providers),
        "provider_notes": sorted(set(provider_notes)),
        "task_recommendations": {
            task: coverage.get("preferred_provider")
            for task, coverage in sorted(task_coverage.items())
            if coverage.get("preferred_provider")
        },
    }


def doctor_summary_from_healthcheck(health_payload: dict[str, Any]) -> dict[str, Any]:
    providers = health_payload.get("providers") or {}
    task_coverage = health_payload.get("task_coverage") or {}
    blockers: list[str] = []
    warnings: list[str] = []
    successful_probes = 0
    failed_probes = 0

    for task, coverage in task_coverage.items():
        if not coverage.get("available"):
            candidates = coverage.get("configured_candidates") or []
            reason = "no healthy provider" if candidates else "no implemented provider"
            blockers.append(f"{task}: {reason}")

    provider_summaries = {}
    for name, report in providers.items():
        if not report.get("enabled", True) or not report.get("implemented", True):
            continue
        probes = report.get("probes") or []
        ok_count = sum(1 for probe in probes if probe.get("ok"))
        failed = [probe for probe in probes if not probe.get("ok")]
        successful_probes += ok_count
        failed_probes += len(failed)
        provider_summaries[name] = {
            "usable": report.get("usable"),
            "probe_ok": report.get("probe_ok"),
            "warnings": report.get("warnings") or [],
            "failed_probes": [
                {
                    "name": probe.get("name"),
                    "reason": probe.get("reason"),
                    "failure_class": probe.get("failure_class"),
                }
                for probe in failed
            ],
        }
        for warning in report.get("warnings") or []:
            warnings.append(f"{name}: {warning}")

    overall = "healthy"
    if blockers:
        overall = "blocked"
    elif failed_probes:
        overall = "degraded"

    return {
        "overall": overall,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "successful_probes": successful_probes,
        "failed_probes": failed_probes,
        "provider_summaries": provider_summaries,
        "task_recommendations": {
            task: coverage.get("preferred_provider")
            for task, coverage in sorted(task_coverage.items())
            if coverage.get("preferred_provider")
        },
    }


def _run_probe(provider: Any, spec: ProbeSpec) -> dict[str, Any]:
    method = getattr(provider, spec.method_name, None)
    if method is None:
        return {
            "name": spec.name,
            "task": spec.method_name,
            "ok": False,
            "status": "unavailable",
            "reason": "probe_not_supported",
            "warnings": ["probe_not_supported"],
            "latency_ms": None,
            "item_count": 0,
            "details": {},
            "failure_class": "probe_not_supported",
        }
    started = time.perf_counter()
    try:
        result = method(**spec.kwargs)
    except Exception as exc:
        return {
            "name": spec.name,
            "task": spec.method_name,
            "ok": False,
            "status": "error",
            "reason": "probe_exception",
            "warnings": [str(exc)],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "item_count": 0,
            "details": {},
            "failure_class": "provider_exception",
        }
    assert isinstance(result, ProviderResult)
    ok = result.status in spec.ok_statuses
    return {
        "name": spec.name,
        "task": spec.method_name,
        "ok": ok,
        "status": result.status,
        "reason": result.reason,
        "warnings": list(result.warnings),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "item_count": len(result.items),
        "details": dict(result.metadata),
        "failure_class": None if ok else _classify_failure(result),
    }


def _classify_failure(result: ProviderResult) -> str:
    if result.reason in {
        "auth_required",
        "session_invalid",
        "session_file_missing",
        "sdk_missing",
        "missing_credentials",
    }:
        return str(result.reason)
    if result.reason in {"cooldown_active", "rate_limited"}:
        return str(result.reason)
    if result.reason in {"client_init_failed", "provider_exception", "api_error"}:
        return "provider_bug_or_upstream_error"
    if result.reason:
        return str(result.reason)
    return f"status:{result.status}"


def _infer_auth_present(name: str, raw_status: dict[str, Any]) -> bool | None:
    if "auth_present" in raw_status:
        return bool(raw_status.get("auth_present"))
    if raw_status.get("auth_required") is False:
        return None
    if name == "twikit":
        if raw_status.get("session_file_exists") is False:
            return False
        expiry = raw_status.get("cookie_expiry") or {}
        auth_token = expiry.get("auth_token", {})
        ct0 = expiry.get("ct0", {})
        if auth_token or ct0:
            return bool(auth_token.get("present")) and bool(ct0.get("present"))
        return bool(raw_status.get("session_file_exists"))
    return None


def _infer_auth_valid(
    raw_status: dict[str, Any],
    *,
    auth_required: bool,
    auth_present: bool | None,
) -> bool | None:
    if not auth_required:
        return True
    if auth_present is False:
        return False
    if raw_status.get("last_session_error") == "session_invalid":
        return False
    expiry = raw_status.get("cookie_expiry") or {}
    if expiry:
        for key in ("auth_token", "ct0"):
            cookie = expiry.get(key)
            if not isinstance(cookie, dict):
                continue
            days = cookie.get("days_remaining")
            if isinstance(days, (int, float)) and days <= 0:
                return False
    return None


def _merge_auth_valid(current: bool | None, probes: list[dict[str, Any]]) -> bool | None:
    if current is False:
        return False
    if any(probe.get("reason") in {"auth_required", "session_invalid", "session_file_missing"} for probe in probes):
        return False
    if probes and all(probe.get("ok") for probe in probes):
        return True
    return current


def _usable_from_report(report: dict[str, Any]) -> bool:
    if not report.get("implemented"):
        return False
    if report.get("auth_required") and report.get("auth_present") is False:
        return False
    if report.get("auth_valid") is False:
        return False
    probe_ok = report.get("probe_ok")
    if probe_ok is False:
        return False
    return True


def _infer_supported_tasks(provider: Any) -> list[str]:
    supported: list[str] = []
    for task, method_names in TASK_METHODS.items():
        if any(callable(getattr(provider, method_name, None)) for method_name in method_names):
            supported.append(task)
    return supported

"""Privacy-preserving, append-only audit ledger for X-Wing usage and cost estimates."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "x_usage.jsonl"
SAFE_RATE_HEADERS = ("x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset")


def _log_path() -> Path:
    value = os.getenv("X_WING_USAGE_LOG_PATH")
    return Path(value).expanduser() if value else DEFAULT_LOG_PATH


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile() -> str | None:
    return os.getenv("HERMES_PROFILE") or os.getenv("HERMES_ACTIVE_PROFILE")


def _normalize_path(url: str) -> str:
    path = url.split("?", 1)[0]
    return re.sub(r"(?<=/)\d{5,}(?=/|$)", ":id", path)


def _resource_count(response: Any) -> int | None:
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or "data" not in payload:
        return 0
    data = payload["data"]
    return len(data) if isinstance(data, list) else int(data is not None)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def record(event_type: str, **fields: Any) -> None:
    """Append one durable event. Ledger failures must never break an X operation."""
    event = {
        "timestamp": _now().isoformat(),
        "event_type": event_type,
        "pid": os.getpid(),
        "hermes_profile": _profile(),
        "hermes_session_id": os.getenv("HERMES_SESSION_ID"),
        **fields,
    }
    try:
        path = _log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            handle.write(json.dumps(_json_safe(event), sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        os.chmod(path, 0o600)
    except OSError:
        return


def attach_xdk_response_logging(client: Any) -> Any:
    """Log every native X HTTP response without recording credentials or payloads."""
    session = getattr(client, "session", None)
    if session is None or getattr(session, "_x_wing_usage_hook", False):
        return client

    def log_response(response: Any, *args: Any, **kwargs: Any) -> Any:
        request = getattr(response, "request", None)
        headers = getattr(response, "headers", {}) or {}
        url = str(getattr(request, "url", ""))
        record(
            "official_x_http",
            provider="official_x",
            method=getattr(request, "method", None),
            endpoint=_normalize_path(url),
            query_parameter_names=sorted(
                part.split("=", 1)[0] for part in url.split("?", 1)[1].split("&") if part
            ) if "?" in url else [],
            http_status=getattr(response, "status_code", None),
            resource_count=_resource_count(response),
            x_request_id=headers.get("x-request-id") or headers.get("x-transaction-id"),
            rate_limits={key: headers.get(key) for key in SAFE_RATE_HEADERS if headers.get(key) is not None},
        )
        return response

    session.hooks.setdefault("response", []).append(log_response)
    session._x_wing_usage_hook = True
    return client


def _parse_since(hours: int | float | None) -> datetime:
    try:
        value = float(hours if hours is not None else 24)
    except (TypeError, ValueError):
        value = 24
    return _now() - timedelta(hours=max(1, min(value, 24 * 365)))


def _read_events(since: datetime) -> list[dict[str, Any]]:
    path = _log_path()
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
            timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if timestamp >= since:
            events.append(event)
    return events


def statistics(*, hours: int | float | None = 24, detail: str = "summary", limit: int = 100) -> dict[str, Any]:
    """Summarize requests and locally estimated cost without double-counting HTTP events."""
    since = _parse_since(hours)
    events = _read_events(since)
    http_events = [event for event in events if event.get("event_type") == "official_x_http"]
    attempts = [event for event in events if event.get("event_type") == "provider_attempt"]
    by_endpoint: dict[str, Counter[str]] = defaultdict(Counter)
    by_provider: dict[str, Counter[str]] = defaultdict(Counter)
    profiles: Counter[str] = Counter()
    total_estimated_cost = 0.0

    for event in http_events:
        endpoint = str(event.get("endpoint") or "unknown")
        by_endpoint[endpoint]["requests"] += 1
        by_endpoint[endpoint][f"http_{event.get('http_status', 'unknown')}"] += 1
        resource_count = event.get("resource_count")
        if isinstance(resource_count, int):
            by_endpoint[endpoint]["resources_returned"] += resource_count
        profiles[str(event.get("hermes_profile") or "unknown")] += 1

    for event in attempts:
        provider = str(event.get("provider") or "unknown")
        by_provider[provider]["attempts"] += 1
        by_provider[provider][str(event.get("status") or "unknown")] += 1
        item_count = event.get("item_count")
        if isinstance(item_count, int):
            by_provider[provider]["items_returned"] += item_count
        cost = event.get("actual_estimated_cost_usd")
        if isinstance(cost, (int, float)):
            by_provider[provider]["actual_estimated_cost_usd"] += cost
            total_estimated_cost += cost

    result: dict[str, Any] = {
        "status": "ok",
        "ledger_path": str(_log_path()),
        "window": {"from": since.isoformat(), "to": _now().isoformat(), "hours": hours or 24},
        "official_x_http_requests": len(http_events),
        "provider_attempts": len(attempts),
        "locally_estimated_cost_usd": round(total_estimated_cost, 6),
        "cost_method": "sum of successful provider-result estimates; HTTP request events are excluded to avoid double counting",
        "by_endpoint": {key: dict(value) for key, value in sorted(by_endpoint.items())},
        "by_provider": {key: dict(value) for key, value in sorted(by_provider.items())},
        "requests_by_hermes_profile": dict(sorted(profiles.items())),
    }
    if detail == "recent":
        result["recent_events"] = events[-max(1, min(int(limit), 500)) :]
    return result

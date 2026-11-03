"""Explicit interest-only requests, independent from the daily-date ledger."""

from datetime import datetime, timezone
from pathlib import Path
import re

from . import ai, article_analysis
from .daily import interest_issue_ids
from .live_pipeline import run_collection
from .pipeline import read_json, verify, write_json

REQUEST_ID = re.compile(r"(?:issue|dispatch)-[1-9][0-9]{0,18}")
STATUSES = {"success", "partial_success", "failed", "blocked", "cached", "skipped"}


def _load(path):
    if not path.exists():
        return {"schema_version": "1.0", "requests": {}}
    value = read_json(path)
    if not isinstance(value, dict) or set(value) != {"schema_version", "requests"} or value["schema_version"] != "1.0" or not isinstance(value["requests"], dict):
        raise ValueError("Invalid interest request history")
    for key, record in value["requests"].items():
        fields = {"request_id", "request_url", "started_at", "completed_at", "status", "stages", "matched_count"}
        if (not isinstance(key, str) or not REQUEST_ID.fullmatch(key) or not isinstance(record, dict)
                or set(record) != fields or record["request_id"] != key or record["status"] not in STATUSES
                or type(record["matched_count"]) is not int or not 0 <= record["matched_count"] <= 100000):
            raise ValueError("Invalid saved interest request")
        expected = "https://github.com/iesilder/kw-plan-weekly-trend/issues/" + key.removeprefix("issue-") if key.startswith("issue-") else None
        if (record["request_url"] != expected or ai._valid_time(record["completed_at"]) < ai._valid_time(record["started_at"])
                or not isinstance(record["stages"], dict) or set(record["stages"]) != {"collection", "titles", "articles"}):
            raise ValueError("Invalid saved interest request details")
        for stage in record["stages"].values():
            if (not isinstance(stage, dict) or set(stage) != {"status", "error_code"} or stage["status"] not in STATUSES
                    or (stage["error_code"] is not None and not re.fullmatch(r"[a-z_]{1,80}", stage["error_code"]))):
                raise ValueError("Invalid saved interest request stage")
    return value


def run(root, request_id, request_url=None, environ=None, now=None, registry_sync=None,
        collector=None, title_runner=None, article_runner=None, verifier=None):
    root = Path(root)
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise ValueError("Invalid interest request identity")
    expected_url = ("https://github.com/iesilder/kw-plan-weekly-trend/issues/" + request_id.removeprefix("issue-")) if request_id.startswith("issue-") else None
    if request_url != expected_url:
        raise ValueError("Interest request link does not match its identity")
    path = root / "data/interests-requests.json"
    history = _load(path)
    if request_id in history["requests"]:
        return {"ok": True, "skipped": True, "reason_code": "request_already_saved", "record": history["requests"][request_id]}
    moment = now or datetime.now(timezone.utc)
    stamp = ai._stamp(moment)
    from .interests import sync_from_github, prepare
    registry = (registry_sync or sync_from_github)(root, environ=environ, now=moment)
    stages = {name: {"status": "skipped", "error_code": None} for name in ("collection", "titles", "articles")}
    matched_count = 0
    if not registry.get("ok"):
        stages["collection"] = {"status": "blocked", "error_code": "registry_sync_failed"}
    else:
        try:
            result = (collector or run_collection)(root, now=moment, interests_only=True, request_url=request_url)
            stages["collection"] = {"status": result["collection_status"], "error_code": None}
        except Exception:
            stages["collection"] = {"status": "failed", "error_code": "collection_step_failed"}
        if stages["collection"]["error_code"] is None:
            scoped = sorted(interest_issue_ids(root, moment))
            matched_count = len(scoped)
            for name, runner in (("titles", title_runner or ai.run), ("articles", article_runner or article_analysis.run)):
                try:
                    result = runner(root, environ=environ, now=moment, automatic=True, issue_ids=scoped)
                    attempt = result["last_attempt"]
                    stages[name] = {"status": attempt["status"], "error_code": attempt["error_code"]}
                    if result.get("suppressed_count") and attempt["status"] in {"success", "cached"}:
                        stages[name] = {"status": "partial_success", "error_code": "unchanged_invalid_output"}
                except Exception:
                    stages[name] = {"status": "failed", "error_code": "analysis_step_failed"}
    prepare(root, now=moment)
    (verifier or verify)(root)
    status = ("failed" if stages["collection"]["status"] in {"failed", "blocked"} else
              "partial_success" if any(stage["status"] not in {"success", "cached"} for stage in stages.values()) else "success")
    record = {"request_id": request_id, "request_url": request_url, "started_at": stamp,
              "completed_at": ai._stamp(now), "status": status, "stages": stages, "matched_count": matched_count}
    history["requests"][request_id] = record
    write_json(path, history)
    _load(path)
    return {"ok": True, "skipped": False, "reason_code": None, "record": record, "registry": registry}

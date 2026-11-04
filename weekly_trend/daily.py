"""One bounded daily collection/AI pass; report decisions remain human."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ai, article_analysis
from .live_pipeline import run_collection
from .pipeline import read_json, verify, write_json

STAGES = ("collection", "titles", "articles")
STATUSES = {"success", "partial_success", "failed", "blocked", "cached", "skipped"}


def _previous(path):
    if not path.exists():
        return None
    value = read_json(path)
    try:
        if (set(value) != {"schema_version", "date_kst", "started_at", "completed_at", "status", "stages"}
                or value["schema_version"] != "1.0" or value["status"] not in STATUSES
                or datetime.strptime(value["date_kst"], "%Y-%m-%d").strftime("%Y-%m-%d") != value["date_kst"]
                or ai._valid_time(value["completed_at"]) < ai._valid_time(value["started_at"])
                or set(value["stages"]) != set(STAGES)):
            raise ValueError
        for stage in value["stages"].values():
            if (set(stage) != {"status", "error_code"} or stage["status"] not in STATUSES
                    or (stage["error_code"] is not None and
                        (not isinstance(stage["error_code"], str) or not stage["error_code"].replace("_", "").isalnum() or len(stage["error_code"]) > 80))):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid saved daily run status") from None
    return value


def interest_issue_ids(root, now=None):
    root = Path(root)
    if not (root / "site/data/latest.json").exists():
        return set()
    from .interests import build_public
    latest, details = ai._snapshot(root)
    public = build_public(root, latest, details, now=now)
    return {match["issue_id"] for interest in public["interests"] if interest["state"] == "active"
            for match in interest["matches"]}


def run(root, environ=None, now=None, collector=None, title_runner=None,
        article_runner=None, verifier=None, registry_sync=None):
    root = Path(root)
    settings = read_json(root / "config/pipeline.json")
    if settings.get("schedule_enabled") is not True:
        raise ValueError("The daily pipeline is disabled")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("A timezone-aware run time is required")
    date = moment.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    path = root / "data/daily-run.json"
    previous = _previous(path)
    if previous and previous["date_kst"] >= date:
        return {"ok": True, "skipped": True, "reason_code": "already_ran_today", "record": previous}
    stamp = ai._stamp(moment)
    registry = None
    env = environ if environ is not None else __import__("os").environ
    if registry_sync is not None or env.get("GITHUB_TOKEN"):
        from .interests import sync_from_github
        try:
            registry = (registry_sync or sync_from_github)(root, environ=env, now=moment)
        except Exception:
            # Registry availability must not stop independent source collection.
            # Never publish exception text (it may contain tokens or payloads).
            registry = {"ok": False, "status": "failed", "error_code": "registry_step_failed"}
    stages = {name: {"status": "skipped", "error_code": None} for name in STAGES}
    try:
        collection = (collector or run_collection)(root, now=moment)
        stages["collection"] = {"status": collection["collection_status"], "error_code": None}
    except Exception:
        # Never log exceptions that may contain a URL, response, or credential.
        stages["collection"] = {"status": "failed", "error_code": "collection_step_failed"}
    if stages["collection"]["error_code"] is None:
        for name, runner in (("titles", title_runner or ai.run),
                             ("articles", article_runner or article_analysis.run)):
            try:
                priority = interest_issue_ids(root, moment)
                options = {"priority_issue_ids": sorted(priority)} if priority else {}
                result = runner(root, environ=environ, now=moment, automatic=True, **options)
                attempt = result["last_attempt"]
                stages[name] = {"status": attempt["status"], "error_code": attempt["error_code"]}
                counts = result.get("counts", {})
                if attempt["status"] in {"cached", "success"} and any(
                        counts.get(key, 0) for key in ("failed", "unavailable", "pending", "not_analyzed", "stale")):
                    stages[name] = {"status": "partial_success", "error_code": "analysis_incomplete"}
                if result.get("suppressed_count", 0) and attempt["status"] in {"cached", "success"}:
                    stages[name] = {"status": "partial_success", "error_code": "unchanged_invalid_output"}
            except Exception:
                stages[name] = {"status": "failed", "error_code": "analysis_step_failed"}
    # Corrupt or inconsistent data must never become publishable just because an
    # optional analysis stage was isolated above.
    (verifier or verify)(root)
    status = ("failed" if stages["collection"]["status"] == "failed" else
              "partial_success" if (registry is not None and not registry.get("ok")) or any(stage["status"] not in {"success", "cached"} for stage in stages.values())
              else "success")
    record = {"schema_version": "1.0", "date_kst": date, "started_at": stamp,
              "completed_at": ai._stamp(now), "status": status, "stages": stages}
    write_json(path, record)
    _previous(path)
    return {"ok": True, "skipped": False, "reason_code": None, "record": record, "registry": registry}

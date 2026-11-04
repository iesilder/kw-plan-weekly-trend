"""One bounded, user-requested summary pass for existing FSC press releases.

This is a deployment migration, not an hourly AI schedule. Normal daily budgets
are unchanged; only the selected FSC items enter these separate bounded calls.
"""

import os
import re
from pathlib import Path

from . import ai, article_analysis
from .pipeline import read_json, verify, write_json

VERSION = "fsc-press-summary-v1"
MAX_TARGETS = 10
CHUNK_SIZE = 5
STAGE_NAMES = {"titles", "articles_1", "articles_2"}
STATUSES = {"running", "success", "partial_success", "failed"}
STAGE_STATUSES = {"cached", "success", "partial_success", "failed", "blocked", "skipped"}


def _fsc_items(root, now):
    latest, details = ai._snapshot(root)
    public, inputs = article_analysis._public(root, latest, details, article_analysis.load_state(root), now)
    return {issue_id: public["items"][issue_id]["status"]
            for issue_id, data in inputs.items()
            if data["reason"] is None and data["source"].get("adapter") == "fsc_html"}


def _previous(path):
    if not path.exists():
        return None
    value = read_json(path)
    try:
        if (set(value) != {"schema_version", "backfill_version", "target_issue_ids", "started_at", "completed_at",
                           "status", "stages", "completed_count", "pending_count", "untargeted_count"}
                or value["schema_version"] != "1.0" or value["backfill_version"] != VERSION
                or value["status"] not in STATUSES or not isinstance(value["target_issue_ids"], list)
                or not 0 <= len(value["target_issue_ids"]) <= MAX_TARGETS
                or len(set(value["target_issue_ids"])) != len(value["target_issue_ids"])
                or any(not ai.ISSUE_ID.fullmatch(item) for item in value["target_issue_ids"])
                or not isinstance(value["stages"], dict) or not set(value["stages"]) <= STAGE_NAMES):
            raise ValueError
        start = ai._valid_time(value["started_at"])
        if value["completed_at"] is not None and ai._valid_time(value["completed_at"]) < start:
            raise ValueError
        if (value["status"] == "running") != (value["completed_at"] is None):
            raise ValueError
        for name in ("completed_count", "pending_count", "untargeted_count"):
            if type(value[name]) is not int or value[name] < 0:
                raise ValueError
        if value["completed_count"] + value["pending_count"] != len(value["target_issue_ids"]):
            raise ValueError
        for name, stage in value["stages"].items():
            cap = MAX_TARGETS if name == "titles" else CHUNK_SIZE
            partition = (value["target_issue_ids"] if name == "titles" else
                         value["target_issue_ids"][:CHUNK_SIZE] if name == "articles_1" else
                         value["target_issue_ids"][CHUNK_SIZE:MAX_TARGETS])
            if (set(stage) != {"status", "error_code", "request_count", "fetch_count", "completed_count", "issue_ids"}
                    or stage["status"] not in STAGE_STATUSES or not isinstance(stage["issue_ids"], list)
                    or not set(stage["issue_ids"]) <= set(partition) or len(stage["issue_ids"]) > cap
                    or len(set(stage["issue_ids"])) != len(stage["issue_ids"])):
                raise ValueError
            for key, maximum in (("request_count", 1), ("fetch_count", 0 if name == "titles" else CHUNK_SIZE),
                                 ("completed_count", cap)):
                if stage[key] is None and stage["error_code"] == "analysis_step_failed":
                    continue
                if type(stage[key]) is not int or not 0 <= stage[key] <= maximum:
                    raise ValueError
            if stage["error_code"] is not None and not re.fullmatch(r"[a-z_]{1,80}", stage["error_code"]):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid FSC summary migration checkpoint") from None
    return value


def _stage(runner, root, env, now, issue_ids):
    if not issue_ids:
        return {"status": "skipped", "error_code": None, "request_count": 0,
                "fetch_count": 0, "completed_count": 0, "issue_ids": []}
    try:
        result = runner(root, environ=env, now=now, automatic=True, issue_ids=issue_ids)
        attempt = result["last_attempt"]
        return {"status": attempt["status"], "error_code": attempt["error_code"],
                "request_count": attempt["request_count"], "fetch_count": attempt.get("fetch_count", 0),
                "completed_count": attempt["completed_count"], "issue_ids": issue_ids}
    except Exception:
        # Provider errors or malformed output must never leak response data.
        return {"status": "failed", "error_code": "analysis_step_failed", "request_count": None,
                "fetch_count": None, "completed_count": None, "issue_ids": issue_ids}


def run(root, *, environ=None, now=None, title_runner=None, article_runner=None, verifier=None):
    root = Path(root)
    path = root / "data/fsc-ai-backfill.json"
    previous = _previous(path)
    if previous and previous["status"] != "running":
        return {"ok": True, "skipped": True, "reason_code": "already_attempted", "record": previous}
    env = os.environ if environ is None else environ
    key = env.get("GEMINI_API_KEY", "")
    if env.get("AI_FREE_PROJECT_CONFIRMED") != "true" or not isinstance(key, str) or not key.strip() or len(key) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in key.strip()):
        return {"ok": True, "skipped": True, "reason_code": "ai_configuration_blocked", "record": previous}
    current = _fsc_items(root, now)
    pending = sorted(issue_id for issue_id, status in current.items() if status != "completed")
    if not previous and not pending:
        record = {"schema_version": "1.0", "backfill_version": VERSION, "target_issue_ids": [],
                  "started_at": ai._stamp(now), "completed_at": ai._stamp(now), "status": "success",
                  "stages": {}, "completed_count": 0, "pending_count": 0, "untargeted_count": 0}
        (verifier or verify)(root)
        write_json(path, record)
        _previous(path)
        return {"ok": True, "skipped": True, "reason_code": "no_pending_fsc", "record": record}
    record = previous or {"schema_version": "1.0", "backfill_version": VERSION,
        "target_issue_ids": pending[:MAX_TARGETS], "started_at": ai._stamp(now), "completed_at": None,
        "status": "running", "stages": {}, "completed_count": 0,
        "pending_count": min(len(pending), MAX_TARGETS), "untargeted_count": max(0, len(pending) - MAX_TARGETS)}
    write_json(path, record)
    targets = record["target_issue_ids"]
    stages = [("titles", title_runner or ai.run, targets)]
    stages += [(f"articles_{index + 1}", article_runner or article_analysis.run, targets[start:start + CHUNK_SIZE])
               for index, start in enumerate(range(0, len(targets), CHUNK_SIZE))]
    for name, runner, scope in stages:
        if name in record["stages"]:
            continue
        # Targets are frozen and chunks disjoint. A failed first body pass is
        # never selected again by the second pass or a later deployment.
        selected = [issue_id for issue_id in scope if issue_id in current]
        record["stages"][name] = _stage(runner, root, env, now, selected)
        write_json(path, record)
        _previous(path)
    final = _fsc_items(root, now)
    record["completed_count"] = sum(final.get(issue_id) == "completed" for issue_id in targets)
    record["pending_count"] = len(targets) - record["completed_count"]
    record["completed_at"] = ai._stamp(now)
    any_failed_stage = any(stage["status"] not in {"success", "cached", "skipped"} for stage in record["stages"].values())
    record["status"] = ("success" if record["pending_count"] == 0 and not any_failed_stage else
                        "partial_success" if record["completed_count"] else "failed")
    (verifier or verify)(root)
    write_json(path, record)
    _previous(path)
    return {"ok": True, "skipped": False, "reason_code": None, "record": record}

"""One corrective pass for FSC outputs rejected by the strict format guards.

This user-requested migration uses the revised FSC input instructions. It does
not relax validation, retry ordinary news, or alter the original migration log.
"""

import os
import re
from pathlib import Path

from . import ai, article_analysis
from .fsc_backfill import STAGE_STATUSES, STATUSES, _previous as _original_migration, _stage
from .pipeline import read_json, verify, write_json

VERSION = "fsc-format-repair-v1"
LIMITS = {"titles": 6, "articles": 2}


def _snapshot(root, now):
    latest, details = ai._snapshot(root)
    body_state = article_analysis.load_state(root)
    body_public, body_inputs = article_analysis._public(root, latest, details, body_state, now)
    title_state, title_public, title_inputs, _ = ai.build_public(root, latest, details, now=now)
    fsc_ids = {issue_id for issue_id, data in body_inputs.items()
               if data["reason"] is None and data["source"].get("adapter") == "fsc_html"}
    # New title guidance changes the current input fingerprint. Preserve the
    # recorded failed issue identities when selecting this finite correction.
    title_failures = {row["issue_id"] for row in title_state.get("failures", {}).values()
                      if row["error_code"] == "invalid_output"}
    migration = _original_migration(Path(root) / "data/fsc-ai-backfill.json")
    failed_titles = (migration or {}).get("stages", {}).get("titles", {})
    original_title_ids = (set(failed_titles.get("issue_ids", []))
                          if failed_titles.get("error_code") == "invalid_output" else set())
    title_candidates = sorted(issue_id for issue_id in fsc_ids & title_failures & original_title_ids
                              if title_inputs[issue_id]["reason"] is None and
                              title_public["items"][issue_id]["status"] != "completed")
    article_candidates = sorted(issue_id for issue_id in fsc_ids
                                if body_state["checks"].get(issue_id, {}).get("status") == "failed" and
                                body_state["checks"][issue_id].get("reason_code") == "output_number_mismatch" and
                                body_state["checks"][issue_id].get("metadata_fingerprint") == body_inputs[issue_id]["metadata_fingerprint"] and
                                body_public["items"][issue_id]["status"] != "completed")
    return {"titles": title_candidates, "articles": article_candidates,
            "title_states": {key: title_public["items"][key]["status"] for key in fsc_ids},
            "article_states": {key: body_public["items"][key]["status"] for key in fsc_ids}}


def _previous(path):
    if not path.exists():
        return None
    value = read_json(path)
    try:
        if (set(value) != {"schema_version", "repair_version", "started_at", "completed_at", "status",
                           "title_issue_ids", "article_issue_ids", "stages", "completed_titles", "remaining_titles",
                           "completed_articles", "remaining_articles", "untargeted_titles", "untargeted_articles"}
                or value["schema_version"] != "1.0" or value["repair_version"] != VERSION
                or value["status"] not in STATUSES or not isinstance(value["stages"], dict)
                or not set(value["stages"]) <= set(LIMITS)):
            raise ValueError
        start = ai._valid_time(value["started_at"])
        if value["completed_at"] is not None and ai._valid_time(value["completed_at"]) < start:
            raise ValueError
        if (value["status"] == "running") != (value["completed_at"] is None):
            raise ValueError
        for kind, cap in LIMITS.items():
            ids = value["title_issue_ids" if kind == "titles" else "article_issue_ids"]
            if (not isinstance(ids, list) or len(ids) > cap or len(ids) != len(set(ids)) or
                    any(not isinstance(item, str) or not ai.ISSUE_ID.fullmatch(item) for item in ids)):
                raise ValueError
            for prefix in ("completed", "remaining", "untargeted"):
                count = value[f"{prefix}_{kind}"]
                if type(count) is not int or count < 0:
                    raise ValueError
            if value[f"completed_{kind}"] + value[f"remaining_{kind}"] != len(ids):
                raise ValueError
            if kind not in value["stages"]:
                continue
            stage = value["stages"][kind]
            if (set(stage) != {"status", "error_code", "request_count", "fetch_count", "completed_count", "issue_ids"}
                    or stage["status"] not in STAGE_STATUSES or not isinstance(stage["issue_ids"], list)
                    or len(stage["issue_ids"]) != len(set(stage["issue_ids"]))
                    or not set(stage["issue_ids"]) <= set(ids)):
                raise ValueError
            for key, maximum in (("request_count", 1), ("fetch_count", 0 if kind == "titles" else cap),
                                 ("completed_count", cap)):
                if stage[key] is None and stage["error_code"] == "analysis_step_failed":
                    continue
                if type(stage[key]) is not int or not 0 <= stage[key] <= maximum:
                    raise ValueError
            if stage["error_code"] is not None and not re.fullmatch(r"[a-z_]{1,80}", stage["error_code"]):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid FSC format repair checkpoint") from None
    return value


def run(root, *, environ=None, now=None, title_runner=None, article_runner=None, verifier=None):
    root = Path(root)
    path = root / "data/fsc-ai-format-repair.json"
    previous = _previous(path)
    if previous and previous["status"] != "running":
        return {"ok": True, "skipped": True, "reason_code": "already_attempted", "record": previous}
    env = os.environ if environ is None else environ
    key = env.get("GEMINI_API_KEY", "")
    if env.get("AI_FREE_PROJECT_CONFIRMED") != "true" or not isinstance(key, str) or not key.strip() or len(key) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in key.strip()):
        return {"ok": True, "skipped": True, "reason_code": "ai_configuration_blocked", "record": previous}
    current = _snapshot(root, now)
    record = previous or {"schema_version": "1.0", "repair_version": VERSION,
        "started_at": ai._stamp(now), "completed_at": None, "status": "running", "stages": {},
        "title_issue_ids": current["titles"][:LIMITS["titles"]],
        "article_issue_ids": current["articles"][:LIMITS["articles"]],
        "completed_titles": 0, "remaining_titles": min(len(current["titles"]), LIMITS["titles"]),
        "completed_articles": 0, "remaining_articles": min(len(current["articles"]), LIMITS["articles"]),
        "untargeted_titles": max(0, len(current["titles"]) - LIMITS["titles"]),
        "untargeted_articles": max(0, len(current["articles"]) - LIMITS["articles"])}
    write_json(path, record)
    for kind, runner in (("titles", title_runner or ai.run), ("articles", article_runner or article_analysis.run)):
        if kind in record["stages"]:
            continue
        ids = record["title_issue_ids" if kind == "titles" else "article_issue_ids"]
        # On resumed work, completed or no-longer-eligible items are omitted.
        # Automatic quarantine remains active for every ordinary pipeline call;
        # only this exact failed FSC scope receives the revised-input correction.
        selected = [issue_id for issue_id in ids if issue_id in current[kind]]
        record["stages"][kind] = _stage(runner, root, env, now, selected, automatic=False)
        write_json(path, record)
        _previous(path)
    final = _snapshot(root, now)
    for kind in LIMITS:
        ids = record["title_issue_ids" if kind == "titles" else "article_issue_ids"]
        states = final["title_states" if kind == "titles" else "article_states"]
        record[f"completed_{kind}"] = sum(states.get(issue_id) == "completed" for issue_id in ids)
        record[f"remaining_{kind}"] = len(ids) - record[f"completed_{kind}"]
    remaining = record["remaining_titles"] + record["remaining_articles"]
    completed = record["completed_titles"] + record["completed_articles"]
    bad_stage = any(stage["status"] not in {"success", "cached", "skipped"} for stage in record["stages"].values())
    record["status"] = "success" if not remaining and not bad_stage else "partial_success" if completed else "failed"
    record["completed_at"] = ai._stamp(now)
    (verifier or verify)(root)
    write_json(path, record)
    _previous(path)
    return {"ok": True, "skipped": False, "reason_code": None, "record": record}

"""Human review choices, separate from article facts and delivered-report history.

The repository is the shared durable store. Browser changes arrive as a small,
revision-checked patch; no supplied titles, URLs, notes, or report claims are kept.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"
STATUSES = {"accepted", "held", "excluded"}
PATCH_STATUSES = STATUSES | {"unreviewed"}
ISSUE_ID = re.compile(r"issue-[a-f0-9]{20}\Z")
INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))\Z")
MAX_PATCH_BYTES = 60_000
MAX_CHANGES = 200


def empty_reviews():
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "updated_at": None, "decisions": {}}


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label} has missing or unexpected fields")


def _revision(value):
    if type(value) is not int or value < 0 or value > 9_007_199_254_740_991:
        raise ValueError("Review revision must be a nonnegative safe integer")


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Review timestamp must be an ISO datetime with timezone")
    match = INSTANT.fullmatch(value)
    if match is None or (match[1] != "Z" and (int(match[2]) > 23 or int(match[3]) > 59)):
        raise ValueError("Review timestamp must use a full ISO datetime with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Review timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("Review timestamp must contain a timezone")
    return parsed


def _issue_id(value):
    if not isinstance(value, str) or not ISSUE_ID.fullmatch(value):
        raise ValueError("Invalid review issue_id")


def validate_reviews(value):
    _keys(value, {"schema_version", "revision", "updated_at", "decisions"}, "Review store")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported review store schema")
    _revision(value["revision"])
    if not isinstance(value["decisions"], dict):
        raise ValueError("Review decisions must be an object")
    if value["revision"] == 0:
        if value["updated_at"] is not None or value["decisions"]:
            raise ValueError("Revision zero must be an empty review store")
        return value
    last_update = _timestamp(value["updated_at"])
    for issue_id, decision in value["decisions"].items():
        _issue_id(issue_id)
        _keys(decision, {"status", "updated_at"}, "Review decision")
        if not isinstance(decision["status"], str) or decision["status"] not in STATUSES:
            raise ValueError("Unsupported review status")
        if _timestamp(decision["updated_at"]) > last_update:
            raise ValueError("Decision timestamp exceeds store timestamp")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field in review data")
        result[key] = value
    return result


def parse_json(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Review data is not valid JSON") from error


def load_reviews(root):
    path = Path(root) / "reports/reviews.json"
    if not path.exists():
        return empty_reviews()
    return validate_reviews(parse_json(path.read_text(encoding="utf-8")))


def validate_mirror(root):
    """A missing mirror is supported only before review storage is introduced."""
    root = Path(root)
    current = load_reviews(root)
    mirror = root / "site/data/reviews.json"
    if not mirror.exists():
        if (root / "reports/reviews.json").exists():
            raise ValueError("Public review mirror is missing")
        return current
    if validate_reviews(parse_json(mirror.read_text(encoding="utf-8"))) != current:
        raise ValueError("Repository review choices and public mirror differ")
    return current


def validate_patch(patch):
    _keys(patch, {"schema_version", "base_revision", "changes"}, "Review patch")
    if patch["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported review patch schema")
    _revision(patch["base_revision"])
    changes = patch["changes"]
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        raise ValueError(f"Review patch must contain at most {MAX_CHANGES} changes")
    seen = set()
    for change in changes:
        _keys(change, {"issue_id", "status"}, "Review change")
        _issue_id(change["issue_id"])
        if change["issue_id"] in seen:
            raise ValueError("Review patch repeats an issue_id")
        seen.add(change["issue_id"])
        if not isinstance(change["status"], str) or change["status"] not in PATCH_STATUSES:
            raise ValueError("Unsupported review status")
    return patch


def parse_patch(text):
    if len(text.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ValueError("Review patch is too large")
    return validate_patch(parse_json(text))


def _write_pair(root, value):
    """Stage both files, then replace together; rollback on ordinary I/O failure.

    GitHub's shared publish concurrency and main-SHA lease provide the process
    boundary. Only the resulting commit is public, never a partial working tree.
    """
    root = Path(root)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    paths = [root / "reports/reviews.json", root / "site/data/reviews.json"]
    with tempfile.TemporaryDirectory(prefix="weekly-reviews-", dir=root) as temporary:
        stage = Path(temporary)
        originals = []
        replaced = []
        for index, path in enumerate(paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            originals.append(path.read_bytes() if path.exists() else None)
            (stage / str(index)).write_text(encoded, encoding="utf-8")
        try:
            for index, path in enumerate(paths):
                os.replace(stage / str(index), path)
                replaced.append(index)
        except OSError:
            for index in reversed(replaced):
                if originals[index] is None:
                    paths[index].unlink()
                else:
                    rollback = stage / f"restore-{index}"
                    rollback.write_bytes(originals[index])
                    os.replace(rollback, paths[index])
            raise


def apply_patch(root, patch, now=None):
    root = Path(root).resolve()
    validate_patch(patch)
    current = validate_mirror(root)
    if patch["base_revision"] != current["revision"]:
        raise ValueError("Review revision conflict: reload the website and review the latest choices")
    latest = parse_json((root / "site/data/latest.json").read_text(encoding="utf-8"))
    if latest.get("is_demo") is not False or not isinstance(latest.get("candidates"), list):
        raise ValueError("Review choices require a published real-data snapshot")
    active_ids = {item["issue_id"] for item in latest["candidates"] if isinstance(item, dict) and item.get("is_demo") is False}
    if any(change["issue_id"] not in active_ids for change in patch["changes"]):
        raise ValueError("A review change refers to an unavailable or non-live issue; reload the website")
    changed = [change for change in patch["changes"]
               if current["decisions"].get(change["issue_id"], {}).get("status", "unreviewed") != change["status"]]
    if not changed:
        return {"ok": True, "changed": False, "revision": current["revision"], "change_count": 0}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Review save time must contain a timezone")
    if current["updated_at"] and now < _timestamp(current["updated_at"]):
        raise ValueError("Review save time precedes the current revision")
    stamp = now.astimezone(timezone.utc).isoformat(timespec="microseconds")
    decisions = {key: dict(value) for key, value in current["decisions"].items()}
    for change in changed:
        if change["status"] == "unreviewed":
            decisions.pop(change["issue_id"], None)
        else:
            decisions[change["issue_id"]] = {"status": change["status"], "updated_at": stamp}
    next_value = {"schema_version": SCHEMA_VERSION, "revision": current["revision"] + 1,
                  "updated_at": stamp, "decisions": decisions}
    validate_reviews(next_value)
    _write_pair(root, next_value)
    return {"ok": True, "changed": True, "revision": next_value["revision"], "change_count": len(changed)}

"""Hourly feed capture and a bounded catch-up for the Korean daily batch."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import daily
from .live_pipeline import run_collection
from .pipeline import read_json, verify


def run(root, *, bootstrap=False, environ=None, now=None, daily_runner=None,
        collector=None, registry_sync=None, verifier=None):
    """Poll RSS without AI; catch up a missing daily pass once after 11 KST.

    A source/code deployment captures all configured public metadata once so a
    newly enabled list/API source need not wait for the following daily batch.
    The daily ledger remains the sole guard for automatic AI calls.
    """
    root = Path(root)
    if read_json(root / "config/pipeline.json").get("schedule_enabled") is not True:
        raise ValueError("The automatic collection pipeline is disabled")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("A timezone-aware run time is required")
    local = moment.astimezone(ZoneInfo("Asia/Seoul"))
    previous = daily._previous(root / "data/daily-run.json")
    if local.hour >= 11 and (previous is None or previous["date_kst"] < local.date().isoformat()):
        result = (daily_runner or daily.run)(root, environ=environ, now=now)
        return {"ok": True, "mode": "daily_catchup", "daily": result,
                "collection_failed": result["record"]["stages"]["collection"]["status"] == "failed"}

    env = environ if environ is not None else __import__("os").environ
    registry = None
    if registry_sync is not None or env.get("GITHUB_TOKEN"):
        from .interests import sync_from_github
        registry = (registry_sync or sync_from_github)(root, environ=env, now=moment)
    source_mode = "all" if bootstrap else "rss"
    result = (collector or run_collection)(root, now=moment, source_mode=source_mode)
    (verifier or verify)(root)
    return {"ok": True, "mode": "bootstrap_metadata" if bootstrap else "rss_metadata",
            "collection_failed": result["collection_status"] == "failed",
            "collection": result, "registry": registry}

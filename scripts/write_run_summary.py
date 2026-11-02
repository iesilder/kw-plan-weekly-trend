"""Describe the persisted demo snapshot in the GitHub Actions run summary."""
import json
import os
from pathlib import Path

status = json.loads(Path("site/data/status.json").read_text(encoding="utf-8"))
lines = ["## Fictional demo snapshot", "", "No live news collection, AI calls, or schedule.", "",
         "| Item | Value |", "| --- | --- |"]
for key in ("generation_id", "article_count", "version_count", "issue_count", "run_count",
            "report_count", "interests_count"):
    lines.append(f"| {key} | {status[key]} |")
summary = "\n".join(lines) + "\n"
print(summary)
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
        handle.write(summary)


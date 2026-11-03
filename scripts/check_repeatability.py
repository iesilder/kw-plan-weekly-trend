"""Two separate Python processes must preserve persistent article/version counts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="weekly-repeat-", dir=REPO) as directory:
        root = Path(directory)
        for name in ("config", "reports", "tests/fixtures"):
            shutil.copytree(REPO / name, root / name)
        (root / "config/interests.json").write_text(json.dumps({"schema_version": "1.0", "interests": []}), encoding="utf-8")
        settings_path = root / "config/pipeline.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings.update(mode="demo", real_collection_enabled=False, schedule_enabled=False,
                        planned_collection_frequency="manual")
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        def execute(command):
            output = subprocess.check_output(
                [sys.executable, "-m", "weekly_trend", command, "--root", str(root)],
                cwd=REPO, text=True, encoding="utf-8",
            )
            return json.loads(output)

        first = execute("demo")
        second = execute("demo")
        result = execute("verify")
        for key in ("article_count", "version_count", "issue_count"):
            if first[key] != second[key] or second[key] != result[key]:
                raise RuntimeError(f"Repeated run changed {key}")
        if second["run_count"] != first["run_count"] + 1:
            raise RuntimeError("Existing collection history was not retained")
        if second["inserted_versions"] != 0:
            raise RuntimeError("An identical rerun created new article versions")
        report = {"ok": True, "first_run": {k: first[k] for k in
                  ("article_count", "version_count", "issue_count", "run_count")},
                  "second_run": result, "new_versions_on_repeat": second["inserted_versions"]}
        print(json.dumps(report, indent=2))
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
                handle.write("## Independent-process persistence check\n\n```json\n" +
                             json.dumps(report, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()


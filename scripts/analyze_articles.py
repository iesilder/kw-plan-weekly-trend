"""Explicit manual article-body inference or offline sidecar preparation/check."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weekly_trend.article_analysis import check, prepare, run


def main():
    parser = argparse.ArgumentParser(description="Manual article briefs; full source bodies are not persisted")
    parser.add_argument("--root", type=Path, default=Path("."))
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        result = (prepare if args.prepare else check if args.check else run)(args.root)
    except (ValueError, OSError):
        parser.exit(1, "Article sidecar validation failed. Check configuration and saved source snapshot.\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

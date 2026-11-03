"""Apply a revision-checked review patch from stdin, never collect or call AI."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weekly_trend.reviews import MAX_PATCH_BYTES, apply_patch, parse_patch


def main():
    parser = argparse.ArgumentParser(description="Save human review choices without recording a delivered report")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--check", action="store_true", help="Validate the existing repository/public review pair only")
    args = parser.parse_args()
    try:
        if args.check:
            from weekly_trend.reviews import validate_mirror
            result = validate_mirror(args.root)
            result = {"ok": True, "revision": result["revision"], "decision_count": len(result["decisions"])}
        else:
            raw = sys.stdin.buffer.read(MAX_PATCH_BYTES + 1)
            if len(raw) > MAX_PATCH_BYTES:
                raise ValueError("Review patch is too large")
            patch = parse_patch(raw.decode("utf-8"))
            result = apply_patch(args.root, patch)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Review save failed: {error}\n")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()

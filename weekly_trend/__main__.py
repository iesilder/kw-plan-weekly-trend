import argparse
import json
from pathlib import Path

from .pipeline import run_demo, verify


def main():
    parser = argparse.ArgumentParser(description="Offline demo or explicitly configured public metadata collection. No AI calls.")
    parser.add_argument("command", choices=["demo", "collect", "run", "verify"])
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        if args.command in {"collect", "run"}:
            from .live_pipeline import run_collection
            result = run_collection(args.root)
        else:
            result = run_demo(args.root) if args.command == "demo" else verify(args.root)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Validation failed: {error}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.command in {"collect", "run"} and not result["collection_ok"]:
        parser.exit(1, "All sources failed; failure status and any previous live snapshot have been saved.\n")


if __name__ == "__main__":
    main()

import argparse
import json
from pathlib import Path

from .pipeline import run_demo, verify


def main():
    parser = argparse.ArgumentParser(description="Offline demo only; no network or AI calls.")
    parser.add_argument("command", choices=["demo", "verify"])
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        result = run_demo(args.root) if args.command == "demo" else verify(args.root)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Validation failed: {error}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

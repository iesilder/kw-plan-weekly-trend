"""Run scheduled public-feed capture without printing response payloads."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend.ingestion import run


def main():
    parser = argparse.ArgumentParser(description="RSS metadata capture and once-daily catch-up")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.root, bootstrap=args.bootstrap)
    except Exception:
        parser.exit(1, "Ingestion validation failed. No unverified snapshot should be published.\n")
    # The collector returns operational counts and policies, never raw responses.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            handle.write(f"collection_failed={str(result['collection_failed']).lower()}\n")
            handle.write(f"mode={result['mode']}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        labels = {"rss_metadata": "RSS 메타데이터 수집 · AI 호출 없음",
                  "bootstrap_metadata": "수집원 변경 후 전체 메타데이터 최초 반영 · AI 호출 없음",
                  "daily_catchup": "오늘 11시 일일 배치 미저장분 1회 보충"}
        lines = ["## 원자료 수집", "", labels[result["mode"]], "",
                 "RSS는 발행처의 최신 목록을 조회합니다. 원자료 메타데이터를 저장한 뒤 데이터마트를 분류합니다.",
                 "일일 AI 호출 상한과 한국 날짜 중복 방지는 기존 일일 배치와 공유합니다.", ""]
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


if __name__ == "__main__":
    main()

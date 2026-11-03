"""Run the scheduled pipeline without exposing source/provider payloads."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend.daily import run


def main():
    parser = argparse.ArgumentParser(description="Daily collection, news-only AI, and validated persistence")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        result = run(args.root)
    except Exception:
        parser.exit(1, "Daily pipeline validation failed. No unverified snapshot should be published.\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        failed = result["record"]["stages"]["collection"]["status"] == "failed"
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            handle.write(f"collection_failed={str(failed).lower()}\n")
            handle.write(f"skipped={str(result['skipped']).lower()}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        record = result["record"]
        lines = ["## 일일 자동 갱신", "", f"한국 날짜: {record['date_kst']}", "",
                 "오늘 저장된 실행이 있어 재호출하지 않았습니다." if result["skipped"] else
                 "수집·기사 분석 결과를 각각 확인합니다. 사이트 게시 성공과 모든 기사 분석 성공은 다릅니다.",
                 "", "| 처리 | 상태 | 사유 |", "|---|---|---|"]
        for stage, label in (("collection", "수집"), ("titles", "기사 제목 분류"), ("articles", "기사 본문 요약")):
            value = record["stages"][stage]
            lines.append(f"| {label} | {value['status']} | {value['error_code'] or 'none'} |")
        lines += ["", "DART는 AI에 전송하지 않으며 채택·실제 보고 여부는 자동 결정하지 않습니다.", ""]
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


if __name__ == "__main__":
    main()

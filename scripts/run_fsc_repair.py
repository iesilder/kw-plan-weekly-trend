"""Run one FSC-only corrective pass using the revised format instructions."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend.fsc_repair import run


def main():
    parser = argparse.ArgumentParser(description="One FSC format repair, retaining strict validators")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        result = run(args.root)
    except Exception:
        parser.exit(1, "FSC format repair validation failed. No unverified snapshot should be published.\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        record = result["record"]
        lines = ["## 금융위원회 출력 형식 보완", "",
                 "수치·문단 근거 검증을 유지하고 보완된 입력 지침으로 실패 자료만 한 번 처리합니다.",
                 "금융위 제목 최대 6건·요청 1회, 본문 수치 검증 실패 최대 2건·요청 1회입니다. 완료 요약·일반 기사·DART는 재요청하지 않습니다.", ""]
        if record:
            lines += [f"상태: {record['status']}",
                      f"제목 완료 {record['completed_titles']}건 · 미완료 {record['remaining_titles']}건",
                      f"본문 완료 {record['completed_articles']}건 · 미완료 {record['remaining_articles']}건", ""]
        else:
            lines += [f"실행 상태: {result['reason_code']}", ""]
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


if __name__ == "__main__":
    main()

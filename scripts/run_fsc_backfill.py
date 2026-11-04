"""Run the explicitly requested, one-time FSC summary migration."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend.fsc_backfill import run


def main():
    parser = argparse.ArgumentParser(description="One-time bounded FSC title/body analysis")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        result = run(args.root)
    except Exception:
        parser.exit(1, "FSC summary migration validation failed. No unverified snapshot should be published.\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        record = result["record"]
        lines = ["## 금융위원회 기존 자료 AI 요약", "",
                 "사용자 요청에 따른 최초 전환 작업입니다. 시간별 RSS 수집은 AI를 호출하지 않습니다.",
                 "제목 최대 1회, 본문은 서로 다른 자료를 5건씩 최대 2회 처리합니다. 일일 정기 처리의 상한은 그대로 유지합니다.", ""]
        if record:
            lines += [f"상태: {record['status']}", f"대상: {len(record['target_issue_ids'])}건 · 요약 완료: {record['completed_count']}건 · 미완료: {record['pending_count']}건",
                      f"최초 전환 상한 밖 자료: {record['untargeted_count']}건", ""]
        else:
            lines += [f"실행 상태: {result['reason_code']}", ""]
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


if __name__ == "__main__":
    main()

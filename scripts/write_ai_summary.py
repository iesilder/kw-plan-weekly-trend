"""Write bounded operational counts, never provider responses or credentials."""

import json
import os
from pathlib import Path


def main():
    data = json.loads(Path("site/data/ai-analysis.json").read_text(encoding="utf-8"))
    counts = data["counts"]
    attempt = data.get("last_attempt") or {}
    lines = ["## 수동 AI 검토 보조", "",
             "워크플로 성공은 상태 저장·배포 성공입니다. 실제 AI 완료 건수는 아래에서 확인합니다.", "",
             "| 항목 | 값 |", "|---|---|",
             f"| 분석 대상 | {counts['eligible']} |",
             f"| 출처 이용 제한 | {counts['blocked']} |",
             f"| 현재 입력 분석 완료 | {counts['completed']} |",
             f"| 재분석 필요 | {counts['stale']} |",
             f"| 아직 분석하지 않음 | {counts['not_analyzed']} |",
             f"| 실패 | {counts['failed']} |",
             f"| 이번 실행 요청 수 | {attempt.get('request_count', 0)} |",
             f"| 이번 실행 캐시 재사용 | {attempt.get('cached_count', 0)} |",
             f"| 마지막 시도 상태 | {attempt.get('status', 'not_run')} |",
             f"| 상태 코드 | {attempt.get('error_code') or 'none'} |", "",
             "DART 사실형 메타데이터만 처리하며 본문·재무 수치 분석 결과가 아닙니다.",
             "[설정과 범위](https://github.com/iesilder/kw-plan-weekly-trend/blob/main/docs/ai.md)", ""]
    summary = "\n".join(lines)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    else:
        print(summary)


if __name__ == "__main__":
    main()

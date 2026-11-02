# 검증 기록

## 2단계 실제 수집 — 개발 실행 환경에서 수동 2회 검증

기존 GitHub SQLite를 읽어 검증용 사본에서 실행했습니다. 사용자 PC나 AI는 사용하지 않았습니다. 아래 결과는 **GitHub Actions 실행 결과가 아닌 개발 실행 환경의 실제 DART 수집 결과**입니다. 검증된 DB·상태·마트와 구현을 [baaf8be8](https://github.com/iesilder/kw-plan-weekly-trend/commit/baaf8be8c37e6b9b7c4f730b38a27c2c74ce2e0c)에 함께 반영했습니다.

| 항목 | 첫 실행 | 두 번째 실행 |
|---|---:|---:|
| 실행 시각(UTC) | 2026-11-02T23:47:11.275040+00:00 | 2026-11-02T23:48:53.288213+00:00 |
| 정상 출처 | 10/10 | 10/10 |
| 확인한 공시목록 | 814 | 33 |
| 이번 범위의 주제 일치 자료 | 22 | 1 |
| 누적 자료 | 22 | 22 |
| 누적 버전 | 22 | 22 |
| 새 버전 | 22 | 0 |

두 번째 실행은 48시간 겹침 구간을 조회하므로 입력 건수가 적습니다. 기존 22건이 유지됐고 재수집된 같은 자료는 중복으로 증가하지 않았습니다. 실제 공시가 추가·정정되는 경우에는 새 버전이 생길 수 있습니다.

- 기존 가상 기사 7건·버전 8건·실행 이력 9회도 SQLite에 그대로 보존됐습니다. 실제 화면에는 섞이지 않습니다.
- 전체 58개 테스트, 독립 프로세스 데모 반복 실행, JavaScript 구문 검사가 통과했습니다.
- 실제 22건 JSON으로 날짜 전용 표시, 안전한 원문 링크, 검색·회사·주제 필터, 자료 정보 복사, 출처 10개 상태의 DOM 통합검증이 통과했습니다.
- AI·정기 실행이 false이며 실제 관심사·보고 이력은 비어 있습니다. 본문·첨부·제출인 개인정보가 저장되지 않았음을 검사했습니다.
- 사전 점검에서 발견한 DART 정상 0건 응답, 지분 공시 분류, 날짜 전용 UI 표시 문제를 수정한 뒤 위 두 실행을 새로 수행했습니다.

## GitHub Actions·Pages

[Collection checks 실행](https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/41549017372)이 위 구현 커밋에서 성공했습니다. 58개 오프라인 테스트, JavaScript 구문 검사, 독립 프로세스 반복 저장 검사가 통과했습니다. 이 검사는 실제 수집·배포를 실행하지 않습니다.

2단계용 `Manual collection and Pages`의 새 수동 실행과 실제 Pages 화면 검증은 아직 완료하지 않았습니다. 이전 실행의 재실행은 과거 워크플로 정의를 사용하므로 새 `Run workflow`가 필요합니다. 코드·DB 검증과 외부 배포 검증을 혼동하지 않습니다. 연결 도구에는 새 workflow_dispatch 시작 기능이 없고 작업 브라우저는 로그아웃 상태이므로, [수동 실행 화면](https://github.com/iesilder/kw-plan-weekly-trend/actions/workflows/pipeline.yml)에서 main의 새 Run workflow가 필요합니다.

1단계/색상 변경의 기존 성공 실행: https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/41544024109

세부 수치는 [phase2-validation.json](phase2-validation.json)에 있습니다.

# 검증 기록

2026-11-02 기준 1단계 가상 데이터 데모의 구현, GitHub Actions 수동 실행, Pages 배포 및 공개 화면을 확인했습니다.

| 항목 | 결과 |
|---|---|
| 로컬 핵심 테스트 | 10개 통과: 중복·수정·복수 근거·시간대·실패 보존·세대/상태 일치·가상 표시·live 기능 차단 |
| 독립 Python 프로세스 2회 실행 | 통과: 기사/버전/이슈 7/8/6 유지, 실행 수 1→2, 새 버전 0 |
| JavaScript 구문 검사 | 통과 |
| GitHub 테스트 CI | 성공: [Demo checks #2](https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/41459780096) |
| GitHub 수동 워크플로 | 성공: [Manual demo and Pages #1](https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/41461017107), 이벤트 workflow_dispatch |
| Pages 배포 | build·deploy 모두 성공, [공개 사이트](https://iesilder.github.io/kw-plan-weekly-trend/) 접속 확인 |
| 공개 화면 동작 | 주제·회사 필터, 검색, 근거 펼치기, 보고 이력 빈 상태, 필터 초기화 확인 |
| 브라우저 실행 오류 | 확인 시 console error/warn 없음 |

## 수동 실행 증거

- 실행 시작 커밋: `f7a18da4995ff21e8e9adcdbef83089b9eb12e32`
- 데이터 저장 커밋: [fb5b6b1](https://github.com/iesilder/kw-plan-weekly-trend/commit/fb5b6b1c6337d5ed8191a9f73f1d22c6938ed2d7)
- 데이터 생성 ID: `demo-711c88ac4298e3bd681e`
- 저장 시각: 2026-11-02 23:20:40 한국시간
- 기사 7개·버전 8개·이슈 6개 유지, 새 버전 0개, 입력 중복 9개
- 기존 SQLite를 이어 사용해 누적 실행 수가 3→4로 증가
- SQLite·state·정적 JSON을 Actions 봇이 저장소에 커밋한 후 사이트를 배포
- latest/status/state의 생성 ID 일치
- 실제 수집·AI·정기 실행 모두 비활성, 경영진 관심사와 실제 보고 이력 모두 0건

누적 실행 수에는 로컬 데모 실행도 포함됩니다. 이번 검증에서 GitHub 수동 워크플로는 1회 실행했으며, 두 독립 프로세스의 반복 실행 검사는 별도로 통과했습니다.

## 공개 화면 확인

- 다섯 개 합의 주제 및 가상 데이터 안내 표시
- 디지털·AI·신사업 선택 시 후보 1건
- 검색어 `주문` 입력 시 주문 서비스 장애 복구 후보 1건
- 회사 `가상 A증권` 선택 시 후보 2건
- AI 도우미 이슈의 가상 원자료 및 수정 시나리오 표시
- 실제 보고 이력 창에 등록된 보고 없음 표시
- 초기화 시 전체 후보 6건 복구

복사·정렬·모바일 화면은 이번 브라우저 점검 범위에 포함하지 않았습니다. 실제 출처 수집, AI 호출, 정기 실행의 정상 동작을 검증한 결과가 아닙니다.

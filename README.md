# 주간 경쟁사 동향 · 2단계 실제 수집

[사이트 열기](https://iesilder.github.io/kw-plan-weekly-trend/) · [수동 수집 워크플로](https://github.com/iesilder/kw-plan-weekly-trend/actions/workflows/pipeline.yml)

기존 경쟁사 10곳의 DART 공시 식별 메타데이터를 수동 수집해 SQLite에 보존하고, 합의한 5개 주제에 해당하는 자료를 정적 화면에서 확인합니다. **AI 호출과 정기 실행은 연결하지 않았습니다.** 경영진 관심사와 실제 보고 이력은 빈 값입니다.

## 수집·저장 범위

- 주제: 실적·자본·주주정책 / 디지털·AI·신사업 / 제휴·인수·해외사업 / 조직·경영전략 / 규제·위험·운영.
- 회사: 미래에셋증권, 한국투자증권, NH투자증권, 삼성증권, KB증권, 신한투자증권, 하나증권, 메리츠증권, 토스증권, 카카오페이증권.
- 출처: DART 회사별 공식 공개목록. 최초 최근 30일, 이후 마지막 성공 범위와 48시간을 겹쳐 조회합니다. 회사별 최대 3페이지입니다.
- 공개 보존: 회사명·법정 공시서식명·접수일·접수번호/원문 링크, 출처와 자체 수집·분류 이력. **공시 본문·첨부·제출인 개인정보는 수집하지 않습니다.**
- 제목 키워드에 따른 분류이며 AI의 중요도 평가·보고 초안이 아닙니다. 회사 경영실적과 상품의 증권발행실적보고서를 구분합니다.
- URL 중복 제거, 변경 버전 보존, 출처별 수집 실패 및 범위 제한 표시를 지원합니다.

공시 외 언론 기사와 공시에 나오지 않는 신사업 소식까지 모두 포함하지 않습니다. 자료가 없다는 사실을 해당 회사에 변화가 없다는 의미로 해석하지 않습니다. 자세한 조건은 [출처·저장 기준](docs/sources.md)에서 확인합니다.

## GitHub에서 수동 실행

1. **Actions → Manual collection and Pages → Run workflow → main → Run workflow**를 선택합니다.
2. `build`는 오프라인 테스트, 실제 공시목록 수집, 데이터 검증, SQLite·상태·마트 커밋을 수행합니다.
3. `deploy`가 같은 검증 결과를 Pages에 배포합니다. `github-pages`의 주소를 엽니다.
4. 같은 워크플로를 다시 실행해 기존 데이터와 실행 이력이 유지되는지 확인합니다. 변경이 없는 같은 입력은 버전을 추가하지 않습니다. 두 실행 사이 새 공시나 정정이 생기면 건수가 증가할 수 있습니다.

배포 워크플로의 유일한 시작 조건은 `workflow_dispatch`입니다. **Collection checks**는 코드 변경/PR에 오프라인 테스트만 실행합니다. API 키·PAT 등록은 필요 없으며 GitHub 제공 토큰으로 같은 저장소와 Pages에만 기록합니다.

일부 출처 실패는 성공한 자료와 이전 자료를 보존하고 화면에 표시합니다. 전체 수집 실패도 무결성 검증을 통과한 이전 화면과 실패 상태를 보존하며 워크플로는 실패로 표시합니다. DB/마트 검증 실패 또는 실행 중 main 변경 시에는 강제 덮어쓰기·배포하지 않습니다.

## 개발 검증

운영은 GitHub에서 하며 사용자 PC를 요구하지 않습니다. 개발 환경은 Python 3.12 표준 라이브러리와 JavaScript 구문 검사만 필요합니다.

```sh
python -m unittest discover -s tests -v
python scripts/check_repeatability.py
node --check site/app.js
python -m weekly_trend collect --root .
python -m weekly_trend verify --root .
```

`collect`/`run`만 실제 출처를 요청합니다. `demo`는 여전히 가상 입력 전용이며 실행 설정을 demo로 바꾼 별도 시험 폴더에서 사용합니다. 운영 config를 복사한 테스트는 시험 폴더만 demo 모드로 바꿉니다.

## 구성

| 경로 | 역할 |
|---|---|
| `config/` | 기존 10사·5주제, 출처별 저장 범위, 빈 관심사 |
| `weekly_trend/collectors.py` | 제한된 HTTP/RSS/공개목록 어댑터 |
| `weekly_trend/classification.py` | 제목 기준의 회사·주제 분류 |
| `weekly_trend/live_storage.py` | 실제 자료 전용 SQLite 테이블과 이력 |
| `weekly_trend/live_pipeline.py` | 수집·상태·마트·무결성 검증 |
| `data/raw.sqlite3` | 원자료 메타데이터와 실행 이력. 기존 가상 테이블도 보존 |
| `state/`, `site/data/` | 미분석 상태, 출처별 성공/실패, 화면 JSON |
| `reports/history.json` | 빈 실제 보고 이력 |
| `tests/fixtures/` | 오프라인 테스트용 가상 자료 |

저장소와 Pages는 공개입니다. 비밀키·내부자료를 넣지 않습니다. 원문 전체의 자유로운 재배포 권한을 주장하지 않으며, 출처 범위 확대 전 이용조건을 다시 확인합니다.

## 디자인 색상

| 색상 | 용도 |
|---|---|
| `#0B1D39` | 기본 글자, 메뉴 배경 |
| `#123A63` | 주요 버튼, 선택 표시 |
| `#2F5D8A` | 보조 글자, 아이콘 |
| `#9CC7E6` | 강조 배경, 경계선 |
| `#F2E9DA` | 어두운 영역의 글자 |
| `#FFFFFF` | 페이지, 카드, 입력창, 대화상자 배경 |

같은 색의 투명도를 사용해 상태와 표면을 구분합니다.


[개발 범위](docs/scope.md) · [출처·저장 기준](docs/sources.md) · [검증 기록](docs/verification.md)

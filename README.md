# 주간 경쟁사 동향 · 1단계 최소 데모

**[배포된 데모 열기](https://iesilder.github.io/kw-plan-weekly-trend/)** · [수동 실행 결과](https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/41461017107)

가상 기사로 `수집 결과 → 중복·변경 처리 → SQLite → 이슈 후보 JSON → 정적 화면`을 확인하는 프로젝트입니다. **실제 기사 수집, AI 호출, 정기 실행은 연결하지 않았습니다.**

모든 기사는 가상 A~E 증권을 대상으로 만든 시나리오입니다. 최근 경영진 관심사와 실제 보고 이력은 비어 있으며, 보고 상태는 **등록된 실제 보고 이력 없음**으로 표시합니다.

## 데모 기능

- 합의한 5개 주제만 표시: 실적·자본·주주정책 / 디지털·AI·신사업 / 제휴·인수·해외사업 / 조직·경영전략 / 규제·위험·운영
- 가상 기사 7개, 기사 버전 8개, 이슈 6개. 중복 URL, 같은 기사 내용 수정, 동일 사건의 복수 근거를 포함합니다.
- 주제·회사 필터, 검색, 정렬, 근거와 변경 내용 확인, 보고 초안 복사
- SQLite 영속 저장, 같은 입력의 재실행 시 기사·버전 중복 생성 방지
- 데이터 세대 일치·근거 참조 검증을 통과한 결과만 반영

## 디자인 색상

| 색상 | 용도 |
|---|---|
| `#0B1D39` | 기본 글자, 메뉴 배경 |
| `#123A63` | 주요 버튼, 선택 표시 |
| `#2F5D8A` | 보조 글자, 아이콘 |
| `#9CC7E6` | 강조 배경, 경계선 |
| `#F2E9DA` | 페이지 배경, 어두운 영역의 글자 |

같은 색의 투명도를 사용해 상태와 표면을 구분합니다.

## 로컬 실행

Python 3.12 이상이 필요합니다. 외부 Python 패키지나 API 키는 필요 없습니다.

```sh
python -m weekly_trend demo --root .
python -m weekly_trend verify --root .
python -m unittest discover -s tests -v
python scripts/check_repeatability.py
node --check site/app.js
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

브라우저에서 `http://localhost:8000`에 접속합니다. `index.html`을 파일로 직접 열면 JSON 요청이 차단될 수 있습니다. Node는 JavaScript 구문 검사에만 사용합니다.

## GitHub Actions와 Pages

1. 저장소 **Settings → Pages → Build and deployment → Source**에서 **GitHub Actions**를 선택합니다.
2. **Actions → Manual demo and Pages → Run workflow → main**을 실행합니다.
3. `build`는 테스트, 가상 데이터 처리·검증, DB/상태/JSON 커밋, 정적 사이트 업로드를 수행합니다.
4. `deploy`는 같은 결과물을 Pages에 배포합니다. 완료 시 환경 `github-pages`의 URL을 엽니다.
5. 같은 워크플로를 다시 실행합니다. 기사 7개·버전 8개·이슈 6개가 유지되고 실행 횟수만 증가해야 합니다.

배포 워크플로에는 `workflow_dispatch`만 있습니다. 별도 **Demo checks**는 코드 변경 시 테스트만 실행하며 기사 처리 결과를 저장하거나 사이트를 배포하지 않습니다. `schedule`/cron, 외부 데이터 호출, AI 호출은 없습니다.

GitHub가 제공하는 `GITHUB_TOKEN`으로 데이터 커밋과 Pages 배포를 수행합니다. 사용자가 등록할 API 키나 PAT는 없습니다. 토큰은 생성 JSON과 사이트에 저장하지 않습니다. 저장소 규칙이 Actions의 `main` 쓰기를 막는 경우 데이터 저장 단계가 실패하며, 해당 실패 상태에서 배포하지 않습니다.

동시 수동 실행은 직렬화합니다. 실행 중 `main`이 바뀌면 강제 덮어쓰기를 하지 않고 중단합니다. 배포 실패 후에도 성공적으로 커밋된 DB는 유지되므로 원인 해결 후 수동 재실행할 수 있습니다.

## 구성

| 경로 | 역할 |
|---|---|
| `config/` | 5개 주제, 경쟁사 초안, 비활성 출처 후보, 빈 관심사 |
| `tests/fixtures/demo_articles.json` | 전적으로 가상인 입력 데이터 |
| `weekly_trend/` | 표준 라이브러리 기반 처리·저장·검증 |
| `data/raw.sqlite3` | 기사, 버전, 수동 실행 이력의 저장 원본 |
| `state/` | 이슈·가상 규칙 분석·실행 상태 |
| `reports/history.json` | 빈 실제 보고 이력 |
| `site/` | Pages에 공개하는 정적 UI와 JSON |
| `.github/workflows/pipeline.yml` | 수동 처리 및 Pages 배포 |
| `.github/workflows/ci.yml` | 테스트 전용 CI |

현재는 SQLite를 Git에 저장하는 소규모 데모 구조입니다. 실제 데이터 연결 전 저장 용량·보관 정책·동시 쓰기 방식은 다시 결정해야 합니다. Pages는 공개 화면이며 로그인 기능은 없습니다. 실제 기사 전문, 사내 관심사, 실제 보고 내용, 인증정보를 공개 저장소에 넣지 마세요.

합의한 범위와 다음 단계는 [개발 범위](docs/scope.md), 실행 증거는 [검증 기록](docs/verification.md)에 정리합니다.


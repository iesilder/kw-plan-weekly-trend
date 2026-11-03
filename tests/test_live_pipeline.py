import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from weekly_trend.classification import classify, classify_for_source, earnings_form, safe_https_url, source_policy
from weekly_trend.live_pipeline import load_live_inputs, run_collection
from weekly_trend.live_storage import connect as connect_live, ingest, json_text, timestamp
from weekly_trend.pipeline import read_json, run_demo, verify, write_json
from weekly_trend.storage import digest

PROJECT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)


def source(source_id="test-a", **changes):
    return {"id": source_id, "name": "테스트 메타데이터 출처", "url": "https://news.example.com/feed.xml",
            "type": "rss", "enabled": True, "retention": "metadata_only", "policy_status": "verified",
            "retention_basis": "Test fixture metadata permission", "retention_url": "https://news.example.com/policy",
            "allowed_hosts": ["news.example.com"], "scope": "company", "company_id": "company-1", **changes}


def record(uri="https://news.example.com/article?id=1", title="미래에셋증권 영업이익 실적 발표", **changes):
    return {"uri": uri, "title": title, "published_at": "2026-11-01T12:00:00+09:00",
            "source_updated_at": None, "content_level": "metadata_only", "content_text": "", **changes}


def success(records, **changes):
    return {"status": "success", "records": records, "fetched_count": len(records), "etag": '"v1"',
            "last_modified": None, **changes}


class LivePipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="weekly-live-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ("config", "reports", "tests/fixtures"):
            shutil.copytree(PROJECT / directory, self.root / directory)
        write_json(self.root / "config/pipeline.json", {"real_collection_enabled": True, "ai_enabled": False,
                    "schedule_enabled": False, "initial_lookback_days": 30, "overlap_hours": 48})
        write_json(self.root / "config/interests.json", {"interests": []})
        write_json(self.root / "reports/history.json", {"reports": []})
        self.sources([source()])

    def sources(self, sources):
        write_json(self.root / "config/sources.json", {"sources": sources})

    def run_records(self, records, now=NOW, **kwargs):
        return run_collection(self.root, collector=lambda *args, **kw: success(records, **kwargs), now=now)

    def rows(self, query):
        with closing(sqlite3.connect(self.root / "data/raw.sqlite3")) as db:
            return db.execute(query).fetchall()

    def latest(self):
        return read_json(self.root / "site/data/latest.json")

    def detail(self, index=0):
        return read_json(self.root / "site" / self.latest()["candidates"][index]["detail_path"])

    def dart_source(self, **changes):
        return source(type="dart_html", adapter="dart_html", content_filter="earnings_only",
                      content_policy_version="dart-earnings-v1", **changes)

    def seed_legacy_metadata(self, records):
        """Model previously accepted metadata without weakening today's filters."""
        self.run_records([])
        origin = source()
        with closing(connect_live(self.root / "data/raw.sqlite3")) as db:
            payloads = []
            route = {"company": "미래에셋증권", "company_ids": ["company-1"], "companies": ["미래에셋증권"],
                     "topic_id": "performance", "topic": "이전 주제", "matched_keywords": ["이전 규칙"],
                     "matched_topic_ids": ["performance"]}
            for value in records:
                payload = {**value, "published_at": timestamp(value.get("published_at")),
                           "source_updated_at": timestamp(value.get("source_updated_at")),
                           "published_date": value.get("published_date"), "source_id": origin["id"],
                           "source_name": origin["name"], "retention_basis": origin["retention_basis"],
                           "retention_url": origin["retention_url"], **route}
                payloads.append(payload)
            ingest(db, payloads, timestamp(NOW.isoformat()))
            for payload in payloads:
                db.execute("INSERT INTO live_article_policy VALUES(?,?,?,?,?,?)",
                           ("article-" + digest(payload["uri"]), 1, "topics", "legacy-news-v0", None, json_text(route)))
            db.commit()

    def test_dart_complete_earnings_form_allowlist_and_non_announcements(self):
        allowed = ["영업(잠정)실적(공정공시)", "연결재무제표기준영업(잠정)실적(공정공시)",
                   "별도재무제표기준영업(잠정)실적(공정공시)", "[기재정정] 영업(잠정)실적(공정공시)",
                   "사업보고서 (2025.12)", "[기재정정]반기보고서(2026.06)", "분기보고서 (2026.09)",
                   "감사보고서", "연결감사보고서", "감사보고서제출", "연결감사보고서(2025.12)",
                   "매출액또는손익구조30%(대규모법인은15%)이상변경", "매출액또는손익구조변경"]
        denied = ["임원ㆍ주요주주특정증권등소유상황보고서", "주식등의대량보유상황보고서",
                  "계열금융회사의약관에의한금융거래", "자기주식취득결정", "현금ㆍ현물배당결정",
                  "특수관계인으로부터주식의취득", "결산실적공시예고", "기업설명회(IR)개최(안내공시)",
                  "영업실적등에대한전망(공정공시)", "장래사업ㆍ경영계획(공정공시)",
                  "영업(잠정)실적(공정공시)예고", "사업보고서제출기한연장신고서", "감사보고서제출예정",
                  "증권발행실적보고서", "투자설명서", "주요사항보고서(영업양수결정)",
                  "실적발표예정", "사업보고서관련보도해명", "[기재정정]결산실적공시예고"]
        for title in allowed:
            self.assertIsNotNone(earnings_form(title), title)
        for title in denied:
            self.assertIsNone(earnings_form(title), title)
        self.sources([self.dart_source()])
        result = self.run_records([record(uri=f"https://news.example.com/{i}", title=title)
                                   for i, title in enumerate(allowed + denied)])
        self.assertEqual(result["article_count"], len(allowed))
        self.assertEqual(result["sources"][0]["outside_content_filter_count"], len(denied))
        self.assertEqual({item["topic_id"] for item in self.latest()["candidates"]}, {"performance"})
        self.assertTrue(verify(self.root)["ok"])

    def test_news_uses_five_topics_and_distinguishes_new_business_from_permission(self):
        self.sources([source(scope="general")])
        titles = ["미래에셋증권 영업이익 실적 발표", "삼성증권 AI 서비스 출시", "NH투자증권 해외 제휴 확대",
                  "한국투자증권 임원 인사 조직 개편", "KB증권 내부통제 운영 개선",
                  "카카오페이증권 인수업 인가 후 신규 IB 플랫폼 출시",
                  "신한투자증권 배당 수령 고객 집계 발표", "신한투자증권 고객 배당 실적 증가",
                  "카카오페이증권 인수업 인가 획득"]
        result = self.run_records([record(uri=f"https://news.example.com/news-{i}", title=title) for i, title in enumerate(titles)])
        self.assertEqual(result["article_count"], 7)
        self.assertEqual({item["topic_id"] for item in self.latest()["candidates"]}, {"performance", "digital", "expansion", "strategy", "risk"})
        underwriting = next(item for item in self.latest()["candidates"] if "플랫폼" in item["title"])
        self.assertEqual(underwriting["topic_id"], "digital")
        self.assertNotIn("expansion", underwriting["matched_topic_ids"])
        permission = next(item for item in self.latest()["candidates"] if "인가 획득" in item["title"])
        self.assertEqual(permission["topic_id"], "risk")

    def test_legacy_articles_are_archived_on_policy_change_even_when_all_sources_fail(self):
        titles = ["영업(잠정)실적(공정공시)", "임원ㆍ주요주주특정증권등소유상황보고서", "계열금융회사의약관에의한금융거래"]
        self.seed_legacy_metadata([record(uri=f"https://news.example.com/old-{i}", title=title) for i, title in enumerate(titles)])
        versions = self.rows("SELECT * FROM live_article_versions ORDER BY version_id")
        # Simulate the pre-policy schema and old source state, without editing any
        # actual project database or losing the earlier collection history.
        with closing(sqlite3.connect(self.root / "data/raw.sqlite3")) as db:
            db.execute("DROP TABLE live_article_policy")
            state = json.loads(db.execute("SELECT state_json FROM live_source_state").fetchone()[0])
            for key in list(state):
                if "policy" in key or key in {"content_filter", "completed_content_filter"}:
                    state.pop(key)
            db.execute("UPDATE live_source_state SET state_json=?", (json.dumps(state),))
            db.commit()
        self.sources([self.dart_source()])
        result = run_collection(self.root, collector=lambda *a, **kw: {"status": "failed", "error_code": "timeout"}, now=NOW + timedelta(days=1))
        self.assertFalse(result["collection_ok"])
        self.assertEqual(result["publication_status"], "filtered")
        self.assertTrue(result["policy_filtered"])
        self.assertEqual((result["article_count"], result["archived_article_count"], result["total_article_count"]), (1, 2, 3))
        self.assertEqual((result["version_count"], result["total_version_count"]), (1, 3))
        self.assertEqual(versions, self.rows("SELECT * FROM live_article_versions ORDER BY version_id"))
        self.assertEqual([item["title"] for item in self.latest()["candidates"]], [titles[0]])
        self.assertEqual(len(list((self.root / "site/data/issues").glob("*.json"))), 1)
        self.assertTrue(verify(self.root)["ok"])
        again = run_collection(self.root, collector=lambda *a, **kw: {"status": "failed", "error_code": "timeout"}, now=NOW + timedelta(days=2))
        self.assertEqual(again["publication_status"], "retained")
        self.assertEqual(again["article_count"], 1)
        self.assertTrue(verify(self.root)["ok"])

    def test_policy_change_backfills_30_days_once_without_inventing_source_revisions(self):
        old = record(title="영업(잠정)실적(공정공시)", published_at="2026-10-20T00:00:00Z")
        self.seed_legacy_metadata([old])
        version = self.rows("SELECT version_id FROM live_article_versions")[0][0]
        self.sources([self.dart_source()])
        calls = []
        def collect(src, **kw):
            calls.append((src, kw["previous_state"]))
            return success([old])
        first = run_collection(self.root, collector=collect, now=NOW + timedelta(days=1))
        self.assertEqual(datetime.fromisoformat(calls[0][0]["collection_start"]), NOW + timedelta(days=1) - timedelta(days=30))
        self.assertIsNone(calls[0][1]["etag"])
        self.assertEqual(first["inserted_versions"], 0)
        self.assertEqual(first["version_count"], 1)
        self.assertEqual(version, self.rows("SELECT version_id FROM live_article_versions")[0][0])
        self.assertFalse(first["sources"][0]["policy_backfill_pending"])
        run_collection(self.root, collector=collect, now=NOW + timedelta(days=2))
        self.assertEqual(datetime.fromisoformat(calls[1][0]["collection_start"]), NOW + timedelta(days=1) - timedelta(hours=48))
        self.assertTrue(verify(self.root)["ok"])

    def test_policy_backfill_survives_failure_truncation_and_unexpected_304(self):
        self.seed_legacy_metadata([record(title="영업(잠정)실적(공정공시)")])
        self.sources([self.dart_source()])
        starts = []
        responses = [{"status": "failed", "error_code": "timeout"},
                     success([], truncated=True, total_count=500),
                     {"status": "not_modified", "records": [], "fetched_count": 0}, success([]), success([])]
        def collect(src, **kw):
            starts.append(src["collection_start"])
            return responses.pop(0)
        for day in range(1, 5):
            result = run_collection(self.root, collector=collect, now=NOW + timedelta(days=day))
            self.assertEqual(result["sources"][0]["policy_backfill_pending"], day < 4)
        run_collection(self.root, collector=collect, now=NOW + timedelta(days=5))
        self.assertEqual(len(set(starts[:4])), 1)
        self.assertEqual(datetime.fromisoformat(starts[0]), NOW + timedelta(days=1) - timedelta(days=30))
        self.assertEqual(datetime.fromisoformat(starts[4]), NOW + timedelta(days=4) - timedelta(hours=48))
        self.assertTrue(verify(self.root)["ok"])

    def test_verification_rejects_old_or_manually_reactivated_archived_scope(self):
        self.seed_legacy_metadata([record(title="현금ㆍ현물배당결정")])
        self.sources([self.dart_source()])
        with self.assertRaises(ValueError):
            verify(self.root)
        self.run_records([])
        self.assertEqual(self.latest()["candidates"], [])
        with closing(sqlite3.connect(self.root / "data/raw.sqlite3")) as db:
            db.execute("UPDATE live_article_policy SET active=1")
            db.commit()
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_repeat_is_idempotent_and_identity_parameters_survive(self):
        records = [record(), record(uri="https://news.example.com/article?id=1&utm_source=x#top"),
                   record(uri="https://news.example.com/article?id=2")]
        first = self.run_records(records)
        second = self.run_records(records, NOW + timedelta(days=1))
        self.assertEqual((first["article_count"], first["version_count"], first["issue_count"]), (2, 2, 2))
        self.assertEqual((second["article_count"], second["version_count"], second["issue_count"]), (2, 2, 2))
        self.assertEqual(second["inserted_versions"], 0)
        self.assertEqual(second["duplicate_count"], 3)
        self.assertEqual(second["run_count"], 2)
        self.assertTrue(verify(self.root)["ok"])

    def test_revisions_retain_history_and_chronology_across_timezones(self):
        old = record(source_updated_at="2026-11-02T10:00:00+09:00")
        revised = record(title="미래에셋증권 영업이익 정정 실적", source_updated_at="2026-11-02T02:00:00Z")
        self.run_records([old])
        initial_id = self.latest()["candidates"][0]["issue_id"]
        result = self.run_records([revised, old], NOW + timedelta(hours=1))
        self.assertEqual((result["article_count"], result["version_count"], result["issue_count"]), (1, 2, 1))
        self.assertEqual(initial_id, self.latest()["candidates"][0]["issue_id"])
        self.assertEqual(self.detail()["title"], revised["title"])
        self.assertIn("2개 버전", self.detail()["change_summary"])
        self.assertTrue(verify(self.root)["ok"])

    def test_live_never_publishes_body_ai_priority_or_fabricated_dates(self):
        result = self.run_records([record(title='<b>미래에셋증권</b> AI 서비스 출시<script>alert(1)</script>',
                         content_text="기사 본문은 보존하면 안 됩니다", content_level="full_text",
                         published_at=None, published_date="2026-11-01")])
        candidate, detail = self.latest()["candidates"][0], self.detail()
        self.assertEqual(candidate["title"], "미래에셋증권 AI 서비스 출시")
        self.assertEqual(candidate["priority"], "unranked")
        self.assertEqual(candidate["analysis_status"], "pending_ai")
        self.assertEqual(candidate["bullets"], [])
        self.assertEqual(detail["analysis_status"], "pending_ai")
        self.assertIsNone(detail["last_report"])
        self.assertEqual(detail["evidence"][0]["content_text"], "")
        self.assertEqual(detail["evidence"][0]["content_level"], "metadata_only")
        self.assertIsNone(detail["evidence"][0]["published_at"])
        self.assertEqual(detail["evidence"][0]["published_date"], "2026-11-01")
        self.assertNotIn("기사 본문", self.rows("SELECT payload_json FROM live_article_versions")[0][0])
        self.assertFalse(result["ai_enabled"])
        self.assertEqual((result["report_count"], result["interests_count"]), (0, 0))

    def test_partial_failure_keeps_other_source_articles_and_last_success(self):
        self.sources([source(), source("test-b")])
        def collect_first(src, **kw):
            return success([record(uri="https://news.example.com/" + src["id"])])
        run_collection(self.root, collector=collect_first, now=NOW)
        def collect_second(src, **kw):
            if src["id"] == "test-b":
                return {"status": "failed", "error_code": "http_error", "error_message": "secret-token-123"}
            return success([record(uri="https://news.example.com/test-a", title="미래에셋증권 AI 전략 발표")])
        result = run_collection(self.root, collector=collect_second, now=NOW + timedelta(hours=1))
        self.assertEqual(result["collection_status"], "partial_success")
        self.assertEqual((result["article_count"], result["issue_count"]), (2, 2))
        failed = result["sources"][1]
        self.assertEqual(failed["last_success_at"], NOW.isoformat(timespec="microseconds"))
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM live_source_runs")[0][0], 4)
        self.assertTrue(verify(self.root)["ok"])

    def test_all_failed_retains_published_snapshot_but_updates_failure_history(self):
        first = self.run_records([record()])
        before = {str(path.relative_to(self.root / "site/data")): path.read_bytes()
                  for path in (self.root / "site/data").rglob("*.json") if path.name != "status.json"}
        result = run_collection(self.root, collector=lambda *a, **kw: {"status": "failed", "error_code": "timeout"}, now=NOW + timedelta(days=1))
        after = {str(path.relative_to(self.root / "site/data")): path.read_bytes()
                 for path in (self.root / "site/data").rglob("*.json") if path.name != "status.json"}
        self.assertEqual(before, after)
        self.assertFalse(result["collection_ok"])
        self.assertEqual(result["publication_status"], "retained")
        self.assertEqual(result["generation_id"], first["generation_id"])
        self.assertEqual(result["run_count"], 2)
        self.assertTrue(verify(self.root)["ok"])
        self.assertFalse(verify(self.root)["collection_ok"])

    def test_initial_all_failed_is_unavailable_not_healthy_empty(self):
        result = run_collection(self.root, collector=lambda *a, **kw: {"status": "failed", "error_code": "timeout"}, now=NOW)
        self.assertFalse(result["collection_ok"])
        self.assertEqual(result["publication_status"], "unavailable")
        self.assertIsNone(result["published_at"])
        self.assertEqual(self.latest()["candidates"], [])
        self.assertTrue(verify(self.root)["ok"])
        again = run_collection(self.root, collector=lambda *a, **kw: {"status": "failed", "error_code": "timeout"}, now=NOW + timedelta(days=1))
        self.assertEqual(again["publication_status"], "unavailable")
        self.assertIsNone(again["published_at"])
        self.assertTrue(verify(self.root)["ok"])

    def test_live_preserves_demo_database_without_mixing_fictional_candidates(self):
        settings = read_json(self.root / "config/pipeline.json")
        write_json(self.root / "config/pipeline.json", {**settings, "real_collection_enabled": False})
        run_demo(self.root)
        before = self.rows("SELECT * FROM articles ORDER BY article_id")
        write_json(self.root / "config/pipeline.json", settings)
        result = self.run_records([record()])
        self.assertEqual(result["article_count"], 1)
        self.assertEqual(before, self.rows("SELECT * FROM articles ORDER BY article_id"))
        self.assertFalse(self.latest()["is_demo"])
        self.assertTrue(all(not item["is_demo"] and not item["company"].startswith("가상") for item in self.latest()["candidates"]))
        self.assertTrue(verify(self.root)["ok"])

    def test_only_requested_companies_and_topics_with_company_source_context(self):
        self.sources([source(scope="general")])
        result = self.run_records([record(), record(uri="https://news.example.com/other", title="무관회사 AI 사업 발표"),
                      record(uri="https://news.example.com/gift", title="미래에셋증권 경품 이벤트 실시")])
        self.assertEqual(result["article_count"], 1)
        self.assertEqual(result["sources"][0]["outside_scope_count"], 2)
        self.sources([source(scope="general"), self.dart_source(source_id="test-dart")])
        result = self.run_records([record(uri="https://news.example.com/filing", title="분기보고서")])
        self.assertEqual(result["article_count"], 2)
        self.assertEqual({item["company"] for item in self.latest()["candidates"]}, {"미래에셋증권"})

    def test_company_source_issuer_takes_precedence_over_company_in_title(self):
        self.run_records([record(title="한국투자증권과 해외사업 제휴")])
        self.assertEqual(self.latest()["candidates"][0]["company"], "미래에셋증권")
        self.assertEqual(self.latest()["candidates"][0]["company_ids"], ["company-1"])

    def test_dart_product_forms_and_non_earnings_filings_stay_outside_scope(self):
        topics = {item["id"]: item["name"] for item in read_json(self.root / "config/topics.json")["topics"]}
        companies = read_json(self.root / "config/companies.json")["companies"]
        for name in ("증권발행실적보고서", "투자설명서", "일괄신고추가서류", "파생결합증권 투자 실적", "파생결합사채 발행실적"):
            self.assertIsNone(classify("미래에셋증권 " + name, companies, topics), name)
        self.assertEqual(classify_for_source("영업(잠정)실적(공정공시)", self.dart_source(), companies, topics)["topic_id"], "performance")
        for title in ("주요사항보고서(자기주식취득결정)", "특수관계인으로부터주식의취득", "계열금융회사의약관에의한금융거래"):
            self.assertIsNone(classify_for_source(title, self.dart_source(), companies, topics))

    def test_known_ownership_forms_are_excluded_from_news_and_not_strategy(self):
        topics = {item["id"]: item["name"] for item in read_json(self.root / "config/topics.json")["topics"]}
        companies = read_json(self.root / "config/companies.json")["companies"]
        for title in ("임원ㆍ주요주주특정증권등소유상황보고서", "[기재정정]임원·주요주주 특정증권등 소유상황보고서",
                      "주식등의대량보유상황보고서(일반)"):
            route = classify("미래에셋증권 " + title, companies, topics)
            self.assertIsNone(route, title)
        self.assertEqual(classify("미래에셋증권 임원 인사 및 조직 개편", companies, topics)["topic_id"], "strategy")
        result = self.run_records([record(title="임원ㆍ주요주주특정증권등소유상황보고서")])
        self.assertEqual(self.latest()["candidates"], [])
        self.assertEqual(result["analysis_mode"], "pending_ai")

    def test_initial_window_overlap_unknown_date_and_feed_coverage_are_explicit(self):
        calls = []
        def collect(src, **kw):
            calls.append((src, kw["previous_state"]))
            return success([record(uri="https://news.example.com/old", published_at="2026-09-01T00:00:00Z"),
                            record(uri="https://news.example.com/unknown", published_at=None)])
        first = run_collection(self.root, collector=collect, now=NOW)
        run_collection(self.root, collector=collect, now=NOW + timedelta(days=1))
        self.assertEqual(datetime.fromisoformat(calls[0][0]["collection_start"]), NOW - timedelta(days=30))
        self.assertEqual(datetime.fromisoformat(calls[1][0]["collection_start"]), NOW - timedelta(hours=48))
        self.assertEqual(calls[1][1]["etag"], '"v1"')
        self.assertEqual(first["article_count"], 1)
        self.assertEqual(first["sources"][0]["outside_window_count"], 1)
        self.assertIn("전체 수집을 보장하지", first["coverage_note"])
        self.assertIsNone(self.detail()["evidence"][0]["published_at"])

    def test_truncated_list_does_not_advance_collection_start(self):
        calls = []
        def collect(src, **kw):
            calls.append(src["collection_start"])
            return success([record()], truncated=True, total_count=600)
        first = run_collection(self.root, collector=collect, now=NOW)
        second = run_collection(self.root, collector=collect, now=NOW + timedelta(days=1))
        self.assertEqual(calls[0], calls[1])
        self.assertIsNone(second["sources"][0]["coverage_watermark_at"])
        self.assertEqual(second["sources"][0]["last_success_at"], (NOW + timedelta(days=1)).isoformat(timespec="microseconds"))
        self.assertTrue(first["sources"][0]["truncated"])
        self.assertIn("누락 가능성", first["sources"][0]["coverage_note"])

    def test_not_modified_retains_records_and_preserves_conditional_headers(self):
        self.run_records([record()])
        result = run_collection(self.root, collector=lambda *a, **kw: {"status": "not_modified", "records": [], "fetched_count": 0}, now=NOW + timedelta(days=1))
        self.assertEqual(result["article_count"], 1)
        self.assertEqual(result["version_count"], 1)
        self.assertEqual(result["sources"][0]["status"], "not_modified")
        state = json.loads(self.rows("SELECT state_json FROM live_source_state")[0][0])
        self.assertEqual(state["etag"], '"v1"')
        self.assertNotIn("etag", result["sources"][0])
        self.assertTrue(verify(self.root)["ok"])

    def test_new_representation_without_validators_clears_old_headers(self):
        self.run_records([record()])
        self.run_records([record()], NOW + timedelta(hours=1), etag=None, last_modified=None)
        state = json.loads(self.rows("SELECT state_json FROM live_source_state")[0][0])
        self.assertIsNone(state["etag"])
        self.assertIsNone(state["last_modified"])

    def test_unsafe_urls_and_off_domain_records_are_rejected(self):
        bad = ["javascript:alert(1)", "data:text/html,abc", "http://news.example.com/a", "https://user:secret@news.example.com/a",
               "https://127.0.0.1/a", "https://localhost/a", "https://news.example.com\\@evil.example.com/a",
               "https://news.example.com/a\nX-Token:secret", "https://evil.example.com/a", "https://sub.news.example.com/a"]
        result = self.run_records([record(uri=uri) for uri in bad] + [record()])
        self.assertEqual(result["article_count"], 1)
        self.assertEqual(result["sources"][0]["rejected_count"], len(bad))
        for uri in bad[:-2]:
            with self.assertRaises(ValueError, msg=uri):
                safe_https_url(uri)
        self.assertTrue(verify(self.root)["ok"])

    def test_invalid_source_policy_or_fabricated_interest_fails_before_mutation(self):
        self.run_records([record()])
        before = (self.root / "data/raw.sqlite3").read_bytes()
        self.sources([source(policy_status="unverified")])
        with self.assertRaises(ValueError):
            self.run_records([record()])
        self.assertEqual(before, (self.root / "data/raw.sqlite3").read_bytes())
        self.sources([source()])
        write_json(self.root / "config/interests.json", {"interests": ["invented"]})
        with self.assertRaises(ValueError):
            self.run_records([record()])

    def test_undeclared_schedule_is_rejected_before_collection_or_mutation(self):
        self.run_records([record()])
        before = (self.root / "data/raw.sqlite3").read_bytes()
        path = self.root / "config/pipeline.json"
        settings = read_json(path)
        write_json(path, {**settings, "schedule_enabled": True})
        with self.assertRaises(ValueError):
            self.run_records([record()])
        self.assertEqual(before, (self.root / "data/raw.sqlite3").read_bytes())
        write_json(path, settings)
        status_path = self.root / "site/data/status.json"
        status = read_json(status_path)
        write_json(status_path, {**status, "schedule_enabled": True})
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_declared_daily_schedule_preserves_raw_ai_boundary_and_run_evidence(self):
        path = self.root / "config/pipeline.json"
        settings = read_json(path)
        write_json(path, {**settings, "schedule_enabled": True,
                          "planned_collection_frequency": "daily_0800_kst", "timezone": "Asia/Seoul"})
        with patch("weekly_trend.ai.request_once", side_effect=AssertionError("collection must not call AI")), \
                patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("collection must not fetch bodies")):
            result = self.run_records([record()])
        self.assertTrue(result["schedule_enabled"])
        self.assertFalse(result["ai_enabled"])
        self.assertEqual(result["planned_collection_frequency"], "daily_0800_kst")
        self.assertEqual(result["timezone"], "Asia/Seoul")
        self.assertEqual(result["last_successful_run_at"], NOW.isoformat(timespec="microseconds"))
        self.assertTrue(verify(self.root)["ok"])
        status_path = self.root / "site/data/status.json"
        status = read_json(status_path)
        write_json(status_path, {**status, "planned_collection_frequency": "hourly"})
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_schedule_configuration_rejects_wrong_time_zone_and_nonboolean_flags(self):
        self.run_records([record()])
        before = (self.root / "data/raw.sqlite3").read_bytes()
        path = self.root / "config/pipeline.json"
        settings = read_json(path)
        for changes in [
            {"schedule_enabled": "true"}, {"schedule_enabled": 1}, {"schedule_enabled": None},
            {"schedule_enabled": True, "planned_collection_frequency": "daily_0800_kst", "timezone": "UTC"},
            {"schedule_enabled": True, "planned_collection_frequency": "daily_0900_kst", "timezone": "Asia/Seoul"},
            {"schedule_enabled": False, "planned_collection_frequency": "daily_0800_kst", "timezone": "Asia/Seoul"},
        ]:
            write_json(path, {**settings, **changes})
            with self.assertRaises(ValueError):
                self.run_records([record()])
            self.assertEqual(before, (self.root / "data/raw.sqlite3").read_bytes())

    def test_old_manual_snapshot_can_be_verified_after_schedule_is_configured(self):
        self.run_records([record()])
        for path in (self.root / "site/data/status.json", self.root / "state/run_status.json"):
            status = read_json(path)
            status.pop("planned_collection_frequency", None)
            status.pop("timezone", None)
            write_json(path, status)
        config_path = self.root / "config/pipeline.json"
        write_json(config_path, {**read_json(config_path), "schedule_enabled": True,
                                 "planned_collection_frequency": "daily_0800_kst", "timezone": "Asia/Seoul"})
        self.assertTrue(verify(self.root)["ok"])

    def test_future_date_only_rejected_using_korean_calendar_day(self):
        # 18:00 UTC is the following calendar day in Korea: Nov 4 is tolerated,
        # Nov 5 is beyond the one-day clock tolerance. No timestamp is invented.
        now = NOW.replace(hour=18)
        result = self.run_records([
            record(uri="https://news.example.com/allowed", published_at=None, published_date="2026-11-04"),
            record(uri="https://news.example.com/future", published_at=None, published_date="2026-11-05"),
        ], now)
        self.assertEqual(result["article_count"], 1)
        self.assertEqual(result["sources"][0]["rejected_count"], 1)
        self.assertEqual(self.detail()["evidence"][0]["published_date"], "2026-11-04")
        self.assertIsNone(self.detail()["evidence"][0]["published_at"])

    def test_report_week_uses_korean_monday_boundary(self):
        self.run_records([record()], NOW - timedelta(hours=14))
        self.assertEqual(self.latest()["report_week"], "2026-W45")

    def test_news_scope_includes_added_companies_related_group_and_industry_changes(self):
        self.sources([source(scope="competitors_and_industry", content_policy_version="news-events-v2")])
        titles = ["다올투자증권 AI 업무 플랫폼 도입", "교보증권 해외 자회사 설립 추진",
                  "한화투자증권 자본 확충 결정", "금융위 증권사 내부통제 가이드라인 개정 추진",
                  "KB금융 증권사업 경영전략 재편 추진"]
        articles = [record(uri=f"https://news.example.com/synthetic-{index}", title=title)
                    for index, title in enumerate(titles)]
        first = self.run_records(articles)
        repeated = self.run_records(articles, NOW + timedelta(hours=1))
        self.assertEqual((first["article_count"], repeated["article_count"]), (5, 5))
        self.assertEqual(repeated["inserted_versions"], 0)
        self.assertEqual(repeated["duplicate_count"], 5)
        candidates = {item["title"]: item for item in self.latest()["candidates"]}
        for index, company_id in enumerate(("company-11", "company-12", "company-13")):
            self.assertEqual(candidates[titles[index]]["company_ids"], [company_id])
        self.assertEqual(candidates[titles[3]]["company_ids"], [])
        self.assertEqual(candidates[titles[4]]["company_ids"], [])
        self.assertIn("AI", candidates[titles[0]]["tags"])
        for candidate in candidates.values():
            self.assertIsInstance(candidate["event_stage"], str)
            self.assertIsInstance(candidate["tags"], list)
            self.assertEqual(candidate["analysis_status"], "pending_ai")
            self.assertEqual(candidate["priority"], "unranked")
            detail = read_json(self.root / "site" / candidate["detail_path"])
            self.assertEqual(detail["tags"], candidate["tags"])
            self.assertEqual(detail["event_stage"], candidate["event_stage"])
            self.assertEqual(detail["classification_rules_version"], "news-events-v2")
            self.assertEqual(detail["evidence"][0]["content_text"], "")
        self.assertEqual(self.latest()["topics"], [{"id": item["id"], "name": item["name"]}
                         for item in read_json(self.root / "config/topics.json")["topics"]])
        self.assertFalse(repeated["ai_enabled"])
        self.assertEqual((repeated["report_count"], repeated["interests_count"]), (0, 0))
        self.assertTrue(verify(self.root)["ok"])

    def test_rules_edit_reclassifies_on_failure_and_backfills_without_metadata_revision(self):
        self.sources([source(scope="competitors_and_industry", content_policy_version="news-events-v2")])
        article = record(title="미래에셋증권 합성전환 AI 업무 플랫폼 도입", published_at="2026-10-20T00:00:00Z")
        first = self.run_records([article])
        versions = self.rows("SELECT * FROM live_article_versions ORDER BY version_id")
        rules_path = self.root / "config/classification_rules.json"
        rules = read_json(rules_path)
        rules["tag_keywords"]["합성분류태그"] = ["합성전환"]
        write_json(rules_path, rules)  # No manual version bump: fingerprint must notice.
        topics_path = self.root / "config/topics.json"
        topics = read_json(topics_path)
        next(item for item in topics["topics"] if item["id"] == "digital")["name"] = "테스트 디지털 주제"
        write_json(topics_path, topics)
        calls = []
        def failed(src, **kwargs):
            calls.append((src, kwargs.get("previous_state")))
            return {"status": "failed", "error_code": "timeout"}
        changed = run_collection(self.root, collector=failed, now=NOW + timedelta(days=1))
        self.assertEqual(changed["publication_status"], "filtered")
        self.assertFalse(changed["collection_ok"])
        self.assertNotEqual(first["sources"][0]["content_policy_version"], changed["sources"][0]["content_policy_version"])
        self.assertTrue(changed["sources"][0]["policy_backfill_pending"])
        self.assertEqual(datetime.fromisoformat(calls[0][0]["collection_start"]), NOW + timedelta(days=1) - timedelta(days=30))
        self.assertIsNone(calls[0][1]["etag"])
        self.assertIn("합성분류태그", self.latest()["candidates"][0]["tags"])
        self.assertEqual(self.latest()["candidates"][0]["topic"], "테스트 디지털 주제")
        self.assertEqual(versions, self.rows("SELECT * FROM live_article_versions ORDER BY version_id"))
        self.assertNotIn("_classification_rules", json.dumps(changed))
        self.assertTrue(verify(self.root)["ok"])
        repeated_failure = run_collection(self.root, collector=failed, now=NOW + timedelta(days=2))
        self.assertEqual(repeated_failure["publication_status"], "retained")
        self.assertEqual(calls[0][0]["collection_start"], calls[1][0]["collection_start"])
        resumed = self.run_records([article], NOW + timedelta(days=3))
        self.assertEqual(resumed["inserted_versions"], 0)
        self.assertEqual(resumed["duplicate_count"], 1)
        self.assertFalse(resumed["sources"][0]["policy_backfill_pending"])
        self.assertEqual(versions, self.rows("SELECT * FROM live_article_versions ORDER BY version_id"))
        self.assertTrue(verify(self.root)["ok"])

    def test_policy_fingerprint_covers_rules_company_alias_activity_scope_and_labels(self):
        self.sources([source("news", scope="competitors_and_industry", content_policy_version="news-events-v2"),
                      self.dart_source(source_id="dart")])
        def versions():
            return {item["id"]: source_policy(item)[1] for item in load_live_inputs(self.root)[2]}
        original = versions()
        self.assertEqual(original, versions())
        rules_path = self.root / "config/classification_rules.json"
        rules = read_json(rules_path)
        rules["tag_keywords"]["합성태그"] = ["합성키워드"]
        write_json(rules_path, rules)
        altered = versions()
        self.assertNotEqual(original["news"], altered["news"])
        self.assertEqual(original["dart"], altered["dart"])
        company_path = self.root / "config/companies.json"
        companies = read_json(company_path)
        newcomer = next(item for item in companies["companies"] if item["id"] == "company-13")
        newcomer.setdefault("aliases", []).append("합성증권별칭")
        write_json(company_path, companies)
        with_alias = versions()
        self.assertNotEqual(altered["news"], with_alias["news"])
        self.assertEqual(altered["dart"], with_alias["dart"])
        newcomer["enabled"] = False
        write_json(company_path, companies)
        inactive = versions()
        self.assertNotEqual(with_alias["news"], inactive["news"])
        self.assertEqual(with_alias["dart"], inactive["dart"])
        sources = read_json(self.root / "config/sources.json")
        sources["sources"][0]["scope"] = "general"
        write_json(self.root / "config/sources.json", sources)
        scoped = versions()
        self.assertNotEqual(inactive["news"], scoped["news"])
        topics_path = self.root / "config/topics.json"
        topics = read_json(topics_path)
        next(item for item in topics["topics"] if item["id"] == "performance")["name"] = "합성 실적 주제"
        write_json(topics_path, topics)
        renamed = versions()
        self.assertNotEqual(scoped["news"], renamed["news"])
        self.assertNotEqual(scoped["dart"], renamed["dart"])

    def test_company_ids_remain_unique_and_original_ten_cannot_be_removed(self):
        self.run_records([record()])
        before = (self.root / "data/raw.sqlite3").read_bytes()
        path = self.root / "config/companies.json"
        original = read_json(path)
        for replacement in ("company-2", "company-999"):
            config = json.loads(json.dumps(original))
            config["companies"][0]["id"] = replacement
            write_json(path, config)
            with self.assertRaises(ValueError):
                self.run_records([record()])
            self.assertEqual(before, (self.root / "data/raw.sqlite3").read_bytes())

    def test_empty_published_snapshot_updates_topic_labels_on_failed_collection(self):
        self.run_records([])
        path = self.root / "config/topics.json"
        topics = read_json(path)
        topics["topics"][0]["name"] = "변경된 합성 주제"
        write_json(path, topics)
        result = run_collection(self.root, collector=lambda *args, **kw: {"status": "failed", "error_code": "timeout"}, now=NOW + timedelta(days=1))
        self.assertEqual(result["publication_status"], "filtered")
        self.assertEqual(self.latest()["topics"][0]["name"], "변경된 합성 주제")
        self.assertEqual(self.latest()["candidates"], [])
        self.assertTrue(verify(self.root)["ok"])
        self.assertTrue(verify(self.root)["ok"])

    def test_verifier_rejects_missing_state_modified_metadata_and_false_success(self):
        self.run_records([record()])
        path = self.root / "state/issues.json"
        state = read_json(path)
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            verify(self.root)
        write_json(path, state)
        issue_path = self.root / "site" / self.latest()["candidates"][0]["detail_path"]
        detail = read_json(issue_path)
        detail["evidence"][0]["content_text"] = "Should never be published"
        write_json(issue_path, detail)
        with self.assertRaises(ValueError):
            verify(self.root)
        self.run_records([record()], NOW + timedelta(hours=1))
        status_path = self.root / "site/data/status.json"
        status = read_json(status_path)
        status["collection_status"] = "failed"
        write_json(status_path, status)
        with self.assertRaises(ValueError):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()

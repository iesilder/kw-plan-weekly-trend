import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from weekly_trend.classification import classify, safe_https_url
from weekly_trend.live_pipeline import run_collection
from weekly_trend.pipeline import read_json, run_demo, verify, write_json

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
        result = self.run_records([record(title='<b>미래에셋증권</b> AI 서비스<script>alert(1)</script>',
                         content_text="기사 본문은 보존하면 안 됩니다", content_level="full_text",
                         published_at=None, published_date="2026-11-01")])
        candidate, detail = self.latest()["candidates"][0], self.detail()
        self.assertEqual(candidate["title"], "미래에셋증권 AI 서비스")
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
        self.sources([source()])
        result = self.run_records([record(uri="https://news.example.com/filing", title="분기보고서")])
        self.assertEqual(result["article_count"], 2)
        self.assertEqual({item["company"] for item in self.latest()["candidates"]}, {"미래에셋증권"})

    def test_company_source_issuer_takes_precedence_over_company_in_title(self):
        self.run_records([record(title="한국투자증권과 해외사업 제휴")])
        self.assertEqual(self.latest()["candidates"][0]["company"], "미래에셋증권")
        self.assertEqual(self.latest()["candidates"][0]["company_ids"], ["company-1"])

    def test_dart_product_forms_are_excluded_but_relevant_filings_route(self):
        topics = {item["id"]: item["name"] for item in read_json(self.root / "config/topics.json")["topics"]}
        companies = read_json(self.root / "config/companies.json")["companies"]
        for name in ("증권발행실적보고서", "투자설명서", "일괄신고추가서류", "파생결합증권 투자 실적", "파생결합사채 발행실적"):
            self.assertIsNone(classify("미래에셋증권 " + name, companies, topics), name)
        for title, topic in (("영업(잠정)실적(공정공시)", "performance"), ("주요사항보고서(자기주식취득결정)", "performance"),
                             ("특수관계인으로부터주식의취득", "expansion"), ("계열금융회사의약관에의한금융거래", "risk")):
            self.assertEqual(classify("미래에셋증권 " + title, companies, topics)["topic_id"], topic)

    def test_known_ownership_forms_are_not_executive_appointment_strategy(self):
        topics = {item["id"]: item["name"] for item in read_json(self.root / "config/topics.json")["topics"]}
        companies = read_json(self.root / "config/companies.json")["companies"]
        for title in ("임원ㆍ주요주주특정증권등소유상황보고서", "[기재정정]임원·주요주주 특정증권등 소유상황보고서",
                      "주식등의대량보유상황보고서(일반)"):
            route = classify("미래에셋증권 " + title, companies, topics)
            self.assertEqual(route["topic_id"], "performance", title)
            self.assertTrue(set(route["matched_keywords"]) <= {"주요주주", "소유상황", "대량보유"})
        self.assertEqual(classify("미래에셋증권 임원 인사 및 조직 개편", companies, topics)["topic_id"], "strategy")
        self.run_records([record(title="임원ㆍ주요주주특정증권등소유상황보고서")])
        self.assertEqual(self.latest()["candidates"][0]["topic_id"], "performance")
        self.assertEqual(self.latest()["candidates"][0]["analysis_status"], "pending_ai")

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

    def test_schedule_is_rejected_before_collection_or_mutation(self):
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

"""Raw capture survives downstream filters and can rebuild without network work."""

from contextlib import closing
from datetime import timedelta
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_live_pipeline as fixtures
from test_live_pipeline import NOW, record, source
from test_interest_collection import interest
from weekly_trend.live_pipeline import run_collection
from weekly_trend.pipeline import read_json, verify, write_json


class RawCaptureTests(unittest.TestCase):
    setUp = fixtures.LivePipelineTests.setUp
    sources = fixtures.LivePipelineTests.sources
    run_records = fixtures.LivePipelineTests.run_records
    rows = fixtures.LivePipelineTests.rows
    latest = fixtures.LivePipelineTests.latest

    def test_all_safe_metadata_is_saved_before_scope_noise_and_initial_date_filters(self):
        self.sources([source(scope="general")])
        records = [record(), record(uri="https://news.example.com/unrelated", title="무관회사 AI 사업 발표"),
                   record(uri="https://news.example.com/noise", title="미래에셋증권 경품 이벤트 실시",
                          content_text="본문은 저장하지 않습니다", description="설명문도 저장하지 않습니다"),
                   record(uri="https://news.example.com/old", published_at="2020-01-01T00:00:00Z"),
                   record(uri="https://evil.example.com/unsafe")]
        first = self.run_records(records)
        self.assertEqual((first["fetched_count"], first["accepted_count"], first["inserted_versions"]), (5, 4, 4))
        self.assertEqual((first["raw_article_count"], first["raw_version_count"], first["mart_article_count"], first["mart_excluded_count"]), (4, 4, 1, 3))
        self.assertEqual(first["mart_exclusions"], {"outside_scope": 2, "outside_initial_window": 1})
        state = first["sources"][0]
        self.assertEqual((state["raw_saved_count"], state["mart_included_count"], state["mart_excluded_count"], state["rejected_count"]), (4, 1, 3, 1))
        payloads = self.rows("SELECT payload_json FROM live_article_versions")
        self.assertNotIn("본문은", json.dumps(payloads, ensure_ascii=False))
        self.assertNotIn("description", json.dumps(payloads))
        self.assertNotIn("topic_id", json.dumps(payloads))
        again = self.run_records(records, NOW + timedelta(hours=1))
        self.assertEqual((again["inserted_versions"], again["duplicate_count"], again["raw_version_count"]), (0, 4, 4))
        self.assertTrue(verify(self.root)["ok"])

    def test_interest_registration_rebuilds_previously_unmatched_raw_without_fetch_ai_or_new_versions(self):
        self.sources([source(scope="competitors_and_industry", adapter="einfomax_rss")])
        target = record(title="미래에셋증권 반려금융 전용채널 구축")
        first = self.run_records([target])
        self.assertEqual((first["raw_article_count"], first["mart_article_count"]), (1, 0))
        before = self.rows("SELECT source_id,state_json FROM live_source_state")
        versions = self.rows("SELECT * FROM live_article_versions")
        book = (self.root / "data/interests-collection.json").read_bytes()
        reviews = (self.root / "reports/reviews.json").read_bytes()
        write_json(self.root / "config/interests.json", {"schema_version": "1.0", "interests": [interest()]})
        with patch("weekly_trend.collectors.collect_source", side_effect=AssertionError("no source request")), \
                patch("weekly_trend.ai.request_once", side_effect=AssertionError("no AI")), \
                patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("no article body")):
            changed = run_collection(self.root, now=NOW + timedelta(days=2), rebuild_only=True)
        self.assertEqual(changed["execution_mode"], "mart_rebuild")
        self.assertFalse(changed["collection_performed"])
        self.assertEqual(changed["sources"], [])
        self.assertEqual(changed["mart_article_count"], 1)
        self.assertEqual(changed["source_inventory"], first["sources"])
        self.assertEqual(self.latest()["candidates"][0]["title"], target["title"])
        self.assertEqual(before, self.rows("SELECT source_id,state_json FROM live_source_state"))
        self.assertEqual(versions, self.rows("SELECT * FROM live_article_versions"))
        self.assertEqual(book, (self.root / "data/interests-collection.json").read_bytes())
        self.assertEqual(reviews, (self.root / "reports/reviews.json").read_bytes())
        self.assertTrue(verify(self.root)["ok"])

    def test_manual_interest_edit_reuses_raw_cursor_without_another_thirty_day_scan(self):
        self.sources([source(scope="competitors_and_industry", adapter="einfomax_rss")])
        write_json(self.root / "config/interests.json", {"schema_version": "1.0", "interests": [interest()]})
        self.run_records([])
        first = run_collection(self.root, collector=lambda *args, **kwargs: {"status": "success", "records": [], "fetched_count": 0},
                               now=NOW + timedelta(hours=1), interests_only=True)
        write_json(self.root / "config/interests.json", {"schema_version": "1.0", "interests": [interest(keywords=["반려금융", "추가단서"])]})
        calls = []
        def collect(item, **kwargs):
            calls.append(item["collection_start"])
            return {"status": "success", "records": [], "fetched_count": 0}
        run_collection(self.root, collector=collect, now=NOW + timedelta(hours=2), interests_only=True)
        self.assertEqual(calls, [first["sources"][0]["next_collection_start"]])
        self.assertTrue(verify(self.root)["ok"])

    def test_rss_scope_retains_other_source_inventory_and_daily_cursor(self):
        self.sources([source("rss", adapter="einfomax_rss"),
                      source("api", type="dart_html", adapter="dart_html", content_filter="earnings_only")])
        first = self.run_records([])
        before = dict(self.rows("SELECT source_id,state_json FROM live_source_state"))["api"]
        called = []
        def collect(item, **kwargs):
            called.append(item["id"])
            return {"status": "success", "records": [], "fetched_count": 0}
        hourly = run_collection(self.root, collector=collect, now=NOW + timedelta(hours=1), source_mode="rss")
        self.assertEqual(called, ["rss"])
        self.assertEqual([item["source_id"] for item in hourly["source_inventory"]], ["rss", "api"])
        self.assertEqual(hourly["source_inventory"][1], first["sources"][1])
        self.assertEqual(dict(self.rows("SELECT source_id,state_json FROM live_source_state"))["api"], before)
        self.assertTrue(verify(self.root)["ok"])

    def test_raw_store_migration_backfills_once_but_rule_edits_reuse_capture_cursor(self):
        self.run_records([record()])
        with closing(sqlite3.connect(self.root / "data/raw.sqlite3")) as db:
            state = json.loads(db.execute("SELECT state_json FROM live_source_state").fetchone()[0])
            state.pop("completed_capture_version")
            db.execute("UPDATE live_source_state SET state_json=?", (json.dumps(state),))
            db.commit()
        calls = []
        def collect(item, previous_state=None, **kwargs):
            calls.append((item["collection_start"], previous_state))
            return {"status": "success", "records": [], "fetched_count": 0, "etag": '"raw-v1"'}
        migrated = run_collection(self.root, collector=collect, now=NOW + timedelta(days=1))
        self.assertEqual(calls[0][0], (NOW + timedelta(days=1) - timedelta(days=30)).isoformat(timespec="microseconds"))
        self.assertIsNone(calls[0][1]["etag"])
        rules_path = self.root / "config/classification_rules.json"
        rules = read_json(rules_path)
        rules["tag_keywords"]["newtag"] = ["영업이익"]
        write_json(rules_path, rules)
        next_run = run_collection(self.root, collector=collect, now=NOW + timedelta(days=2))
        self.assertEqual(calls[1][0], migrated["sources"][0]["next_collection_start"])
        self.assertEqual(calls[1][1]["etag"], '\"raw-v1\"')
        self.assertFalse(next_run["sources"][0]["policy_backfill_pending"])
        self.assertEqual(next_run["raw_version_count"], 1)
        self.assertIn("newtag", self.latest()["candidates"][0]["tags"])
        self.assertTrue(verify(self.root)["ok"])

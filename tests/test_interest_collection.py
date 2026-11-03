"""Interest windows supplement approved sources without moving daily cursors."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from weekly_trend.classification import classify_for_source
from weekly_trend.live_pipeline import load_live_inputs, run_collection
from weekly_trend.pipeline import read_json, verify, write_json

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)


def interest(**changes):
    return {"id": "interest-12", "issue_number": 12, "title": "반려금융 사업", "question": "경쟁사의 실제 사업 진입을 확인",
            "keywords": ["반려금융"], "exclude_keywords": [], "topic_id": "digital", "active_from": "2026-11-01",
            "active_until": "2026-12-01", "updated_at": "2026-11-04T01:00:00Z",
            "source_url": "https://github.com/iesilder/kw-plan-weekly-trend/issues/12", "enabled": True, **changes}


def record(number=1, title="미래에셋증권, 반려금융 전용채널 구축", published=None):
    return {"uri": f"https://news.einfomax.co.kr/news/articleView.html?idxno={9000000 + number}",
            "title": title, "published_at": (published or NOW - timedelta(days=1)).isoformat(),
            "source_updated_at": None, "content_level": "metadata_only", "content_text": ""}


def response(records=(), **changes):
    return {"status": "success", "records": list(records), "fetched_count": len(records),
            "etag": '"interest-v1"', "last_modified": None, **changes}


class InterestCollectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="interest-collection-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for directory in ("config", "reports", "tests/fixtures"):
            shutil.copytree(ROOT / directory, self.root / directory)
        write_json(self.root / "config/interests.json", {"schema_version": "1.0", "interests": []})
        write_json(self.root / "reports/history.json", {"schema_version": "1.0", "reports": []})
        write_json(self.root / "reports/reviews.json", {"schema_version": "1.0", "revision": 0, "updated_at": None, "decisions": {}})
        origins = read_json(self.root / "config/sources.json")["sources"]
        self.rss = next(row for row in origins if row.get("adapter") == "einfomax_rss")
        self.dart = next(row for row in origins if row.get("adapter") == "dart_html")
        write_json(self.root / "config/sources.json", {"sources": [self.rss, self.dart]})
        self.calls = []
        self.dart_records = []

    def configure(self, *values):
        write_json(self.root / "config/interests.json", {"schema_version": "1.0", "interests": list(values)})

    def execute(self, records=(), now=NOW, *, fail=False, truncated=False, interests_only=False, request_url=None):
        def collect(source, previous_state=None, **kwargs):
            self.calls.append((copy.deepcopy(source), copy.deepcopy(previous_state)))
            if fail and source["id"] == self.rss["id"]:
                return {"status": "failed", "error_code": "network_error"}
            if source["id"] == self.dart["id"]:
                return response(self.dart_records)
            return response(records, truncated=truncated)
        with patch("weekly_trend.ai.request_once", side_effect=AssertionError("AI forbidden")), \
             patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("article body forbidden")):
            return run_collection(self.root, collector=collect, now=now, interests_only=interests_only, request_url=request_url)

    def ids(self):
        return [row["title"] for row in read_json(self.root / "site/data/latest.json")["candidates"]]

    def book(self):
        return read_json(self.root / "data/interests-collection.json")

    def daily_state(self):
        with sqlite3.connect(self.root / "data/raw.sqlite3") as db:
            return db.execute("SELECT source_id,state_json FROM live_source_state ORDER BY source_id").fetchall()

    def test_new_interest_supplements_topic_gate_but_preserves_source_and_noise_gates(self):
        self.configure(interest())
        titles = [record(), record(2, "삼성전자, 반려금융 전용채널 구축"),
                  record(3, "미래에셋증권 반려금융 목표주가 상향"),
                  record(4, "미래에셋증권 반려금융 고객이벤트"),
                  record(5, "미래에셋증권 AI 서비스 출시")]
        result = self.execute(titles)
        self.assertEqual(set(self.ids()), {titles[0]["title"], titles[4]["title"]})
        self.assertEqual(result["interests_count"], 1)
        self.assertFalse(result["ai_enabled"])
        self.assertTrue(verify(self.root)["ok"])
        topics, companies, sources, _ = load_live_inputs(self.root, now=NOW)
        dart = next(row for row in sources if row["id"] == self.dart["id"])
        self.assertIsNone(classify_for_source("반려금융 서비스 출시", dart, companies, topics))
        self.assertEqual(classify_for_source("분기보고서 (2026.09)", dart, companies, topics)["topic_id"], "performance")

    def test_initial_interest_backfill_does_not_reopen_unrelated_base_records(self):
        self.execute([record(1, "미래에셋증권 AI 서비스 출시")])
        before = self.daily_state()
        self.configure(interest())
        self.calls.clear()
        moment = NOW + timedelta(days=1)
        older = NOW - timedelta(days=10)
        self.execute([record(2, published=older), record(3, "미래에셋증권 AI 서비스 출시", published=older)], now=moment)
        rss_call = next(row for row in self.calls if row[0]["id"] == self.rss["id"])
        self.assertEqual(datetime.fromisoformat(rss_call[0]["collection_start"]), moment - timedelta(days=30))
        self.assertIsNone(rss_call[1]["etag"])
        self.assertEqual(len(self.ids()), 2)
        original = {sid: json.loads(value) for sid, value in before}[self.rss["id"]]
        current = {sid: json.loads(value) for sid, value in self.daily_state()}[self.rss["id"]]
        self.assertEqual(original["content_policy_version"], current["content_policy_version"])
        self.assertEqual(datetime.fromisoformat(current["next_collection_start"]), moment - timedelta(hours=48))
        self.calls.clear()
        self.execute([], now=moment + timedelta(days=1))
        call = next(row for row in self.calls if row[0]["id"] == self.rss["id"])
        self.assertEqual(datetime.fromisoformat(call[0]["collection_start"]), moment - timedelta(hours=48))
        self.assertEqual(call[1]["etag"], '"interest-v1"')

    def test_manual_interest_pass_fetches_news_only_and_keeps_daily_watermarks(self):
        self.configure(interest())
        self.dart_records = [{"uri": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261104000001",
                              "title": "분기보고서 (2026.09)", "published_at": NOW.isoformat(),
                              "source_updated_at": None, "content_level": "metadata_only", "content_text": ""}]
        self.execute([record(1, "미래에셋증권 AI 서비스 출시")])
        before = self.daily_state()
        reviews = (self.root / "reports/reviews.json").read_bytes()
        self.calls.clear()
        self.execute([record(2), record(3, "미래에셋증권 영업이익 실적 발표")], now=NOW + timedelta(hours=2),
                     interests_only=True, request_url="https://github.com/iesilder/kw-plan-weekly-trend/issues/99")
        self.assertEqual([row[0]["id"] for row in self.calls], [self.rss["id"]])
        self.assertEqual(self.daily_state(), before)
        self.assertEqual(len(self.ids()), 3)
        self.assertIn("분기보고서 (2026.09)", self.ids())
        self.assertEqual((self.root / "reports/reviews.json").read_bytes(), reviews)
        self.assertEqual(self.book()["last_attempt"]["mode"], "manual")
        self.assertEqual(self.book()["last_attempt"]["request_url"], "https://github.com/iesilder/kw-plan-weekly-trend/issues/99")
        self.assertTrue(verify(self.root)["ok"])

    def test_failed_or_truncated_new_interest_retains_its_original_start(self):
        self.configure(interest())
        self.execute([], fail=True)
        first = self.book()["entries"]["interest-12"]["sources"][self.rss["id"]]
        sidecar = read_json(self.root / "site/data/interests.json")
        self.assertEqual(sidecar["last_collection"]["status"], "failed")
        self.assertEqual(sidecar["interests"][0]["collection_status"], "failed")
        self.assertIsNone(first["last_success_at"])
        self.calls.clear()
        self.execute([], now=NOW + timedelta(days=2), truncated=True)
        call = next(row for row in self.calls if row[0]["id"] == self.rss["id"])
        self.assertEqual(call[0]["collection_start"], first["next_collection_start"])
        second = self.book()["entries"]["interest-12"]["sources"][self.rss["id"]]
        self.assertEqual(second["next_collection_start"], first["next_collection_start"])
        self.assertIsNone(second["last_success_at"])

    def test_keyword_edit_rescans_once_while_question_edit_reuses_checkpoint(self):
        self.configure(interest())
        self.execute([record()])
        original = self.book()["entries"]["interest-12"]["filter_fingerprint"]
        self.configure(interest(question="수익 모델 관점에서 확인"))
        self.calls.clear()
        self.execute([], now=NOW + timedelta(days=1))
        call = next(row for row in self.calls if row[0]["id"] == self.rss["id"])
        self.assertEqual(datetime.fromisoformat(call[0]["collection_start"]), NOW - timedelta(hours=48))
        self.assertEqual(self.book()["entries"]["interest-12"]["filter_fingerprint"], original)
        self.configure(interest(keywords=["반려금융", "신규단서"]))
        self.calls.clear()
        moment = NOW + timedelta(days=2)
        self.execute([], now=moment)
        call = next(row for row in self.calls if row[0]["id"] == self.rss["id"])
        self.assertEqual(datetime.fromisoformat(call[0]["collection_start"]), moment - timedelta(days=30))
        self.assertIsNone(call[1]["etag"])
        self.assertNotEqual(self.book()["entries"]["interest-12"]["filter_fingerprint"], original)

    def test_disabling_interest_archives_only_interest_records_without_deleting_history(self):
        self.configure(interest())
        self.execute([record(), record(2, "미래에셋증권 AI 서비스 출시")])
        with sqlite3.connect(self.root / "data/raw.sqlite3") as db:
            versions = db.execute("SELECT COUNT(*) FROM live_article_versions").fetchone()[0]
        self.configure(interest(enabled=False))
        self.execute([], now=NOW + timedelta(days=1))
        self.assertEqual(self.ids(), ["미래에셋증권 AI 서비스 출시"])
        with sqlite3.connect(self.root / "data/raw.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM live_article_versions").fetchone()[0], versions)
        self.assertTrue(verify(self.root)["ok"])

    def test_reactivated_interest_does_not_reuse_another_interests_feed_validator(self):
        other = interest(id="interest-13", issue_number=13, title="고객연구", keywords=["고객연구"],
                         source_url="https://github.com/iesilder/kw-plan-weekly-trend/issues/13")
        for manual in (False, True):
            with self.subTest(manual=manual):
                self.configure(interest(), other)
                self.execute([record()], now=NOW)
                self.configure(interest(), {**other, "enabled": False})
                def newer(source, previous_state=None, **kwargs):
                    return response([], etag='"v2"')
                run_collection(self.root, collector=newer, now=NOW + timedelta(days=1))
                self.configure(interest(), other)
                observed = []
                target = record(80 + int(manual), "미래에셋증권 고객연구 설계 완료", NOW + timedelta(days=1))
                def conditional(source, previous_state=None, **kwargs):
                    if source["id"] == self.dart["id"]:
                        return response([])
                    observed.append(previous_state)
                    if previous_state.get("etag"):
                        return {"status": "not_modified", "records": [], "fetched_count": 0}
                    return response([target], etag='"v2"')
                result = run_collection(self.root, collector=conditional, now=NOW + timedelta(days=2), interests_only=manual)
                self.assertTrue(result["collection_ok"])
                self.assertIsNone(observed[0]["etag"])
                self.assertIn(target["title"], self.ids())
                self.assertEqual(self.book()["entries"]["interest-13"]["last_success_at"],
                                 (NOW + timedelta(days=2)).isoformat(timespec="microseconds"))

    def test_new_interest_unexpected_304_does_not_claim_initial_scan_completed(self):
        self.execute([])
        self.configure(interest())
        def conditional(source, **kwargs):
            return {"status": "not_modified", "records": [], "fetched_count": 0}
        run_collection(self.root, collector=conditional, now=NOW + timedelta(days=1))
        entry = self.book()["entries"]["interest-12"]
        self.assertIsNone(entry["last_success_at"])
        self.assertEqual(entry["status"], "failed")

    def test_manual_without_active_interests_publishes_without_network(self):
        with patch("weekly_trend.collectors.collect_source", side_effect=AssertionError("no network")):
            result = run_collection(self.root, now=NOW, interests_only=True)
        self.assertTrue(result["collection_ok"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["article_count"], 0)
        self.assertTrue(verify(self.root)["ok"])


if __name__ == "__main__":
    unittest.main()

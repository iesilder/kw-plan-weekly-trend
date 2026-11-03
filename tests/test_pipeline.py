import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing

from weekly_trend.pipeline import read_json, run_demo, verify, write_json
from weekly_trend.storage import canonical_url

PROJECT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="weekly-test-", dir=PROJECT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in ("config", "reports", "tests/fixtures"):
            shutil.copytree(PROJECT / directory, self.root / directory)
        # Production enables collection; these tests deliberately exercise offline demo mode.
        settings = read_json(self.root / "config/pipeline.json")
        settings.update(mode="demo", real_collection_enabled=False, schedule_enabled=False, planned_collection_frequency="manual")
        write_json(self.root / "config/pipeline.json", settings)

    def rows(self, query):
        with closing(sqlite3.connect(self.root / "data/raw.sqlite3")) as db:
            return db.execute(query).fetchall()

    def test_repeat_preserves_articles_versions_and_current_revision(self):
        first = run_demo(self.root)
        current = self.rows("SELECT article_id,current_version_id FROM articles ORDER BY article_id")
        second = run_demo(self.root)
        self.assertEqual((first["article_count"], first["version_count"], first["issue_count"]), (7, 8, 6))
        self.assertEqual((second["article_count"], second["version_count"], second["issue_count"]), (7, 8, 6))
        self.assertEqual(second["run_count"], first["run_count"] + 1)
        self.assertEqual(second["inserted_versions"], 0)
        self.assertEqual(second["duplicate_count"], 9)
        self.assertEqual(current, self.rows("SELECT article_id,current_version_id FROM articles ORDER BY article_id"))
        self.assertTrue(verify(self.root)["ok"])

    def test_same_url_change_retains_history_and_advances_current(self):
        run_demo(self.root)
        fixture_path = self.root / "tests/fixtures/demo_articles.json"
        fixture = read_json(fixture_path)
        fixture["articles"].append({**fixture["articles"][1],
            "source_updated_at": "2026-11-03T09:00:00+09:00",
            "content_text": "추가로 수정된 가상 문장: 시험을 중단하고 검토 중입니다."})
        write_json(fixture_path, fixture)
        result = run_demo(self.root)
        self.assertEqual(result["article_count"], 7)
        self.assertEqual(result["version_count"], 9)
        self.assertEqual(result["inserted_versions"], 1)
        current_text = self.rows("""SELECT v.content_text FROM articles a JOIN article_versions v
            ON a.current_version_id=v.version_id WHERE a.canonical_url='demo://fixture/ai'""")[0][0]
        self.assertIn("시험을 중단", current_text)

    def test_tracking_parameters_removed_but_article_identifier_preserved(self):
        self.assertEqual(canonical_url("https://NEWS.example/article?id=42&utm_source=x&fbclid=y#top"),
                         "https://news.example/article?id=42")
        self.assertNotEqual(canonical_url("https://news.example/article?id=42"),
                            canonical_url("https://news.example/article?id=43"))

    def test_same_event_keeps_multiple_sources_and_other_event_is_separate(self):
        run_demo(self.root)
        first = read_json(self.root / "site/data/issues/demo-issue-capital.json")
        other = read_json(self.root / "site/data/issues/demo-issue-policy.json")
        self.assertEqual(len(first["evidence"]), 2)
        self.assertEqual(first["company"], other["company"])
        self.assertNotEqual(first["issue_id"], other["issue_id"])

    def test_demo_never_claims_real_ai_reports_or_sources(self):
        result = run_demo(self.root)
        self.assertEqual(result["analysis_mode"], "demo_rules")
        self.assertEqual(result["report_count"], 0)
        self.assertEqual(result["interests_count"], 0)
        self.assertFalse(result["ai_enabled"])
        self.assertEqual(read_json(self.root / "reports/history.json")["reports"], [])
        latest = read_json(self.root / "site/data/latest.json")
        self.assertEqual({item["topic_id"] for item in latest["candidates"]},
                         {"performance", "digital", "expansion", "strategy", "risk"})
        for file in (self.root / "site/data/issues").glob("*.json"):
            detail = read_json(file)
            self.assertIsNone(detail["last_report"])
            self.assertTrue(all(item["source_url"] is None for item in detail["evidence"]))

    def test_invalid_input_keeps_previous_database_and_mart(self):
        run_demo(self.root)
        before_db = (self.root / "data/raw.sqlite3").read_bytes()
        before_mart = (self.root / "site/data/latest.json").read_bytes()
        path = self.root / "tests/fixtures/demo_articles.json"
        fixture = read_json(path)
        fixture["articles"][0]["topic_id"] = "retail"
        write_json(path, fixture)
        with self.assertRaises(ValueError):
            run_demo(self.root)
        self.assertEqual(before_db, (self.root / "data/raw.sqlite3").read_bytes())
        self.assertEqual(before_mart, (self.root / "site/data/latest.json").read_bytes())

    def test_verification_rejects_mixed_generation_and_fabricated_evidence(self):
        run_demo(self.root)
        path = self.root / "site/data/issues/demo-issue-capital.json"
        detail = read_json(path)
        original = dict(detail)
        detail["generation_id"] = "wrong-generation"
        write_json(path, detail)
        with self.assertRaises(ValueError):
            verify(self.root)
        write_json(path, original)
        latest_path = self.root / "site/data/latest.json"
        latest = read_json(latest_path)
        latest["candidates"][0]["evidence_ids"] = ["invented-evidence"]
        write_json(latest_path, latest)
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_live_features_and_report_history_are_not_silently_used(self):
        path = self.root / "config/pipeline.json"
        settings = read_json(path)
        settings["ai_enabled"] = True
        write_json(path, settings)
        with self.assertRaises(ValueError):
            run_demo(self.root)
        settings["ai_enabled"] = False
        write_json(path, settings)
        write_json(self.root / "reports/history.json", {"reports": [{"status": "delivered"}]})
        with self.assertRaises(ValueError):
            run_demo(self.root)

    def test_demo_never_enables_a_real_schedule_or_accepts_nonboolean_flags(self):
        path = self.root / "config/pipeline.json"
        settings = read_json(path)
        for flag in [True, "false", 0, None]:
            write_json(path, {**settings, "schedule_enabled": flag})
            with self.assertRaises(ValueError):
                run_demo(self.root)

    def test_state_missing_or_stale_cannot_pass_verification(self):
        run_demo(self.root)
        path = self.root / "state/issues.json"
        original = read_json(path)
        path.unlink()
        with self.assertRaises(ValueError):
            verify(self.root)
        write_json(path, original)
        analysis = self.root / "state/analyses/demo-issue-capital.json"
        original_analysis = read_json(analysis)
        analysis.unlink()
        with self.assertRaises(ValueError):
            verify(self.root)
        write_json(analysis, {**original_analysis, "generation_id": "old-generation"})
        with self.assertRaises(ValueError):
            verify(self.root)
        write_json(analysis, original_analysis)
        self.assertTrue(verify(self.root)["ok"])

    def test_mixed_timezones_use_chronology_for_revision_and_issue(self):
        run_demo(self.root)
        path = self.root / "tests/fixtures/demo_articles.json"
        fixture = read_json(path)
        fixture["articles"].append({**fixture["articles"][1],
            "source_updated_at": "2026-11-02T02:00:00+00:00",
            "content_text": "UTC 기준 최신 수정입니다. 한국 시간 11시의 가상 상황입니다."})
        fixture["articles"].append({**fixture["articles"][6],
            "source_updated_at": "2026-11-02T00:00:00+00:00",
            "content_text": "다른 출처의 최신 설명입니다. 한국 시간 09시의 가상 상황입니다."})
        write_json(path, fixture)
        run_demo(self.root)
        ai_detail = read_json(self.root / "site/data/issues/demo-issue-ai.json")
        self.assertIn("UTC 기준 최신 수정", ai_detail["bullets"][0])
        capital_detail = read_json(self.root / "site/data/issues/demo-issue-capital.json")
        self.assertIn("다른 출처의 최신 설명", capital_detail["bullets"][0])
        self.assertTrue(verify(self.root)["ok"])


if __name__ == "__main__":
    unittest.main()

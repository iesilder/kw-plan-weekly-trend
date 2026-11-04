"""Feed polling never adds AI calls; daily catch-up shares the date ledger."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from weekly_trend import daily, ingestion

BEFORE_ELEVEN = datetime(2026, 11, 4, 1, 59, tzinfo=timezone.utc)
ELEVEN = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config/pipeline.json").write_text(json.dumps({"schedule_enabled": True}))
        self.calls = []

    def collect(self, root, now, source_mode):
        self.calls.append(source_mode)
        return {"collection_status": "success"}

    def daily(self, root, environ, now):
        self.calls.append("daily")
        # Exercise the real once-per-Korean-date guard without source/AI traffic.
        return daily.run(root, environ={}, now=now,
                         collector=lambda *a, **k: {"collection_status": "success"},
                         title_runner=lambda *a, **k: {"last_attempt": {"status": "success", "error_code": None}},
                         article_runner=lambda *a, **k: {"last_attempt": {"status": "success", "error_code": None}},
                         verifier=lambda root: None)

    def execute(self, now=BEFORE_ELEVEN, **kwargs):
        return ingestion.run(self.root, now=now, environ={}, collector=self.collect,
                             daily_runner=self.daily, verifier=lambda root: self.calls.append("verify"), **kwargs)

    def test_rss_before_eleven_does_not_run_daily_analysis_or_advance_daily_date(self):
        result = self.execute()
        self.assertEqual(result["mode"], "rss_metadata")
        self.assertEqual(self.calls, ["rss", "verify"])
        self.assertFalse((self.root / "data/daily-run.json").exists())

    def test_missing_daily_is_caught_up_once_after_korean_eleven_then_only_rss(self):
        result = self.execute(ELEVEN)
        self.assertEqual(result["mode"], "daily_catchup")
        self.assertEqual(result["daily"]["record"]["date_kst"], "2026-11-04")
        self.assertEqual(self.calls, ["daily"])
        self.calls.clear()
        self.assertEqual(self.execute(ELEVEN + timedelta(hours=1))["mode"], "rss_metadata")
        self.assertEqual(self.calls, ["rss", "verify"])
        self.calls.clear()
        self.assertEqual(self.execute(ELEVEN + timedelta(days=1))["mode"], "daily_catchup")
        self.assertEqual(self.calls, ["daily"])

    def test_code_source_bootstrap_fetches_all_metadata_even_when_daily_is_saved(self):
        self.execute(ELEVEN)
        self.calls.clear()
        result = self.execute(ELEVEN + timedelta(hours=1), bootstrap=True)
        self.assertEqual(result["mode"], "bootstrap_metadata")
        self.assertEqual(self.calls, ["all", "verify"])

    def test_failed_daily_saved_today_is_not_retried_by_hourly_polling(self):
        self.execute(ELEVEN)
        path = self.root / "data/daily-run.json"
        record = json.loads(path.read_text())
        record["status"] = "failed"
        record["stages"]["collection"] = {"status": "failed", "error_code": "collection_step_failed"}
        path.write_text(json.dumps(record))
        self.calls.clear()
        self.assertEqual(self.execute(ELEVEN + timedelta(hours=3))["mode"], "rss_metadata")
        self.assertEqual(self.calls, ["rss", "verify"])

    def test_registry_sync_precedes_metadata_capture_without_ai(self):
        def sync(root, environ, now):
            self.calls.append("registry")
            return {"ok": True}
        self.execute(registry_sync=sync)
        self.assertEqual(self.calls, ["registry", "rss", "verify"])

    def test_disabled_or_corrupt_ledger_stops_before_any_network(self):
        (self.root / "data").mkdir()
        (self.root / "data/daily-run.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [])
        (self.root / "config/pipeline.json").write_text(json.dumps({"schedule_enabled": False}))
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [])


class HourlyPersistenceTests(unittest.TestCase):
    def test_real_rss_capture_excludes_daily_list_sources_and_preserves_human_ai_state(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ('config', 'reports', 'state', 'data', 'site/data'):
                shutil.copytree(project / directory, root / directory)
            status = json.loads((root / 'site/data/status.json').read_text())
            previous = datetime.fromisoformat(status['last_run_at'].replace('Z', '+00:00'))
            moment = (previous + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
            preserved = {name: (root / name).read_bytes() for name in
                         ('reports/reviews.json', 'reports/history.json', 'state/ai-analysis.json',
                          'state/article-analysis.json', 'data/daily-run.json')}
            seen = []
            def source_capture(source, **kwargs):
                self.assertEqual(source['type'], 'rss')
                seen.append(source['id'])
                return {'status': 'success', 'records': [], 'fetched_count': 0,
                        'etag': None, 'last_modified': None}
            def capture(root, now, source_mode):
                return ingestion.run_collection(root, now=now, source_mode=source_mode, collector=source_capture)
            with patch('weekly_trend.ai.request_once', side_effect=AssertionError('AI forbidden')), \
                 patch('weekly_trend.article_analysis.fetch_article', side_effect=AssertionError('article fetch forbidden')):
                result = ingestion.run(root, now=moment, environ={}, collector=capture)
            self.assertEqual(result['mode'], 'rss_metadata')
            self.assertTrue(seen)
            self.assertNotIn('fsc-press-releases', seen)
            self.assertFalse(any(name.startswith('dart-') for name in seen))
            for name, original in preserved.items():
                self.assertEqual((root / name).read_bytes(), original, name)
            self.assertTrue(ingestion.verify(root)['ok'])

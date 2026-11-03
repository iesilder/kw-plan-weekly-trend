"""Daily orchestration, Korean date limits, and independent failure handling."""

from datetime import datetime, timedelta, timezone
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weekly_trend import daily

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)


class DailyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/pipeline.json').write_text(json.dumps({'schedule_enabled': True}))
        self.calls = []

    def collect(self, root, now):
        self.calls.append('collection')
        return {'collection_status': 'success'}

    def analyze(self, root, environ, now, automatic):
        self.assertTrue(automatic)
        self.calls.append('ai')
        return {'last_attempt': {'status': 'success', 'error_code': None}}

    def verify(self, root):
        self.calls.append('verify')

    def execute(self, **kwargs):
        return daily.run(self.root, now=kwargs.pop('now', NOW),
                         collector=kwargs.pop('collector', self.collect),
                         title_runner=kwargs.pop('title_runner', self.analyze),
                         article_runner=kwargs.pop('article_runner', self.analyze),
                         verifier=kwargs.pop('verifier', self.verify), **kwargs)

    def test_collect_then_both_ai_stages_and_verify_once_per_korean_date(self):
        first = self.execute()
        self.assertEqual(first['record']['date_kst'], '2026-11-04')
        self.assertEqual(self.calls, ['collection', 'ai', 'ai', 'verify'])
        self.assertEqual(first['record']['status'], 'success')
        self.calls.clear()
        before = (self.root / 'data/daily-run.json').read_bytes()
        second = self.execute(now=NOW + timedelta(hours=15))
        self.assertTrue(second['skipped'])
        self.assertEqual(self.calls, [])
        self.assertEqual((self.root / 'data/daily-run.json').read_bytes(), before)
        self.assertFalse(self.execute(now=NOW + timedelta(days=1))['skipped'])

    def test_registered_interests_are_synchronized_before_collection(self):
        def sync(root, environ, now):
            self.calls.append('registry')
            return {'ok': True, 'interest_count': 1}
        result = self.execute(registry_sync=sync)
        self.assertEqual(self.calls, ['registry', 'collection', 'ai', 'ai', 'verify'])
        self.assertEqual(result['registry']['interest_count'], 1)
        self.calls.clear()
        self.assertTrue(self.execute(registry_sync=sync)['skipped'])
        self.assertEqual(self.calls, [])

    def test_registry_failure_preserves_normal_collection_and_is_visible(self):
        result = self.execute(registry_sync=lambda *a, **k: {'ok': False, 'error_code': 'network_error'})
        self.assertEqual(self.calls, ['collection', 'ai', 'ai', 'verify'])
        self.assertEqual(result['record']['status'], 'partial_success')
        self.assertEqual(result['registry']['error_code'], 'network_error')

    def test_title_failure_does_not_stop_article_analysis_or_collection_publication(self):
        def failed(*args, **kwargs):
            self.calls.append('title_failure')
            raise OSError('SENSITIVE_PROVIDER_RESPONSE_OR_KEY')
        result = self.execute(title_runner=failed)
        self.assertEqual(self.calls, ['collection', 'title_failure', 'ai', 'verify'])
        self.assertEqual(result['record']['status'], 'partial_success')
        text = (self.root / 'data/daily-run.json').read_text()
        self.assertIn('analysis_step_failed', text)
        self.assertNotIn('SENSITIVE', text)
        self.assertTrue(self.execute()['skipped'])

    def test_reported_ai_failure_is_not_mistaken_for_completed_analysis(self):
        def failed(*args, **kwargs):
            return {'last_attempt': {'status': 'failed', 'error_code': 'invalid_output'}}
        result = self.execute(article_runner=failed)
        self.assertEqual(result['record']['status'], 'partial_success')
        self.assertEqual(result['record']['stages']['articles']['status'], 'failed')

    def test_quarantined_invalid_outputs_are_not_reported_as_cache_success(self):
        def suppressed(*args, **kwargs):
            return {'suppressed_count': 1,
                    'last_attempt': {'status': 'cached', 'error_code': None}}
        result = self.execute(article_runner=suppressed)
        self.assertEqual(result['record']['status'], 'partial_success')
        self.assertEqual(result['record']['stages']['articles'],
                         {'status': 'partial_success', 'error_code': 'unchanged_invalid_output'})

    def test_collection_exception_preserves_previous_verified_snapshot_without_ai_calls(self):
        def failed(*args, **kwargs):
            raise OSError('SENSITIVE_SOURCE_PAYLOAD')
        result = self.execute(collector=failed)
        self.assertEqual(self.calls, ['verify'])
        self.assertEqual(result['record']['status'], 'failed')
        self.assertEqual(result['record']['stages']['titles']['status'], 'skipped')
        self.assertNotIn('SENSITIVE', json.dumps(result))

    def test_all_sources_failed_is_saved_as_failure_even_when_ai_can_use_retained_snapshot(self):
        result = self.execute(collector=lambda *a, **k: {'collection_status': 'failed'})
        self.assertEqual(result['record']['status'], 'failed')
        self.assertEqual(self.calls, ['ai', 'ai', 'verify'])

    def test_invalid_snapshot_cannot_be_saved_or_published(self):
        def invalid(root):
            raise ValueError('snapshot mismatch')
        with self.assertRaises(ValueError):
            self.execute(verifier=invalid)
        self.assertFalse((self.root / 'data/daily-run.json').exists())

    def test_disabled_scheduler_does_not_collect_or_call_ai(self):
        (self.root / 'config/pipeline.json').write_text(json.dumps({'schedule_enabled': False}))
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_corrupt_ledger_fails_closed_before_network(self):
        (self.root / 'data').mkdir()
        (self.root / 'data/daily-run.json').write_text('{}')
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [])


class DailyPersistenceTests(unittest.TestCase):
    def test_real_pipeline_preserves_reviews_and_ai_cache_without_network(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ('config', 'reports', 'state', 'data', 'site/data'):
                shutil.copytree(project / directory, root / directory)
            (root / 'data/daily-run.json').unlink(missing_ok=True)
            before = {name: (root / name).read_bytes()
                      for name in ('reports/reviews.json', 'reports/history.json')}
            prior_status = json.loads((root / 'site/data/status.json').read_text())
            moment = max(NOW, datetime.fromisoformat(prior_status['last_run_at'].replace('Z', '+00:00')) + timedelta(hours=1))
            entries = {name: json.loads((root / f'state/{name}-analysis.json').read_text())['entries']
                       for name in ('ai', 'article')}
            def collect(root, now):
                return daily.run_collection(root, now=now, collector=lambda *a, **k:
                    {'status': 'success', 'records': [], 'fetched_count': 0,
                     'etag': None, 'last_modified': None})
            with patch('weekly_trend.ai.request_once', side_effect=AssertionError('network forbidden')), \
                 patch('weekly_trend.article_analysis.fetch_article', side_effect=AssertionError('network forbidden')):
                result = daily.run(root, now=moment, environ={}, collector=collect)
                self.assertFalse(result['skipped'])
                self.assertTrue(daily.run(root, now=moment, environ={}, collector=collect)['skipped'])
            status = json.loads((root / 'site/data/status.json').read_text())
            self.assertTrue(status['schedule_enabled'])
            self.assertFalse(status['ai_enabled'])
            self.assertEqual(status['collection_status'], 'success')
            for name, content in before.items():
                self.assertEqual((root / name).read_bytes(), content)
            for name, content in entries.items():
                self.assertEqual(json.loads((root / f'state/{name}-analysis.json').read_text())['entries'], content)
            self.assertTrue(daily.verify(root)['ok'])


if __name__ == '__main__':
    unittest.main()

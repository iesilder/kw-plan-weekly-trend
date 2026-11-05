"""Reliability guards without a live provider call or relaxed content checks."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from weekly_trend import ai
from test_daily import DailyTests
from test_article_analysis import ArticleAnalysisTests, PARAGRAPHS, read, write, NOW
from datetime import timedelta


class TransportDiagnostics(unittest.TestCase):
    def test_http_failure_codes_are_safe_and_use_one_request(self):
        for status, expected in [(400, 'request_invalid'), (404, 'request_not_found'),
                                 (408, 'request_timeout'), (401, 'authentication_failed'),
                                 (403, 'authentication_failed'), (429, 'rate_limited'),
                                 (503, 'provider_unavailable'), (422, 'request_rejected')]:
            with self.subTest(status=status):
                error = urllib.error.HTTPError(ai.ENDPOINT, status, 'SECRET', {}, io.BytesIO(b'SECRET'))
                with patch('weekly_trend.ai.urllib.request.build_opener') as factory:
                    factory.return_value.open.side_effect = error
                    with self.assertRaises(ai.AIError) as caught:
                        ai.request_once({}, 'fake-test-key', 1)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(caught.exception.http_status, status)
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertTrue(error.closed)
                factory.return_value.open.assert_called_once()
                self.assertIn(expected, ai.ERROR_CODES)

    def test_http_diagnostic_rejects_untrusted_values(self):
        for value in (True, "503 SECRET", 399, 600, {}, None):
            with self.subTest(value=value):
                self.assertIsNone(ai.AIError("provider_unavailable", value).http_status)
                with self.assertRaises(ValueError):
                    ai.validate_http_status({"http_status": value, "error_code": "provider_unavailable"})
        ai.validate_http_status({"error_code": "provider_unavailable"})
        ai.validate_http_status({"http_status": 503, "error_code": "provider_unavailable"})
        with self.assertRaises(ValueError):
            ai.validate_http_status({"http_status": 503, "error_code": None})

    def test_provider_schema_avoids_nested_cardinality_expansion(self):
        schema = ai.response_schema()
        self.assertNotIn('maxItems', json.dumps(schema))
        self.assertNotIn('minItems', json.dumps(schema))
        self.assertEqual(schema['required'], ['items'])
        props = schema['properties']['items']['items']['properties']
        self.assertEqual(set(props['suggested_topic_id']['enum']), ai.TOPICS)
        valid = {'suggested_topic_id': 'risk', 'relevance': 'relevant',
                 'rationale': '제목 기준 정책 변화 확인', 'check_points': [], 'duplicate_issue_ids': []}
        for override in ({'check_points': ['확인'] * 4},
                         {'duplicate_issue_ids': ['issue-' + str(i) * 20 for i in range(5)]}):
            with self.assertRaises(ValueError):
                ai.validate_result({**valid, **override}, 'issue-' + 'a' * 20)


class DailyReliability(DailyTests):
    def test_registry_exception_is_isolated_and_redacted(self):
        def broken(*args, **kwargs):
            raise OSError('SECRET TOKEN AND PROVIDER PAYLOAD')
        result = self.execute(registry_sync=broken)
        self.assertEqual(self.calls, ['collection', 'ai', 'ai', 'verify'])
        self.assertEqual(result['record']['status'], 'partial_success')
        self.assertEqual(result['registry']['error_code'], 'registry_step_failed')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_latest_success_does_not_hide_unfinished_snapshot(self):
        for key in ('failed', 'unavailable', 'pending', 'not_analyzed', 'stale'):
            with self.subTest(key=key):
                (self.root / 'data/daily-run.json').unlink(missing_ok=True)
                result = self.execute(article_runner=lambda *a, **k: {
                    'last_attempt': {'status': 'success', 'error_code': None}, 'counts': {key: 1}})
                self.assertEqual(result['record']['status'], 'partial_success')
                self.assertEqual(result['record']['stages']['articles']['error_code'], 'analysis_incomplete')


class NumericGuidance(ArticleAnalysisTests):
    def test_news_has_exact_numeric_guidance_without_body_persistence(self):
        self.run_good()
        sent = json.loads(self.calls[0][0]['input'])['items'][0]
        self.assertIn('format_rules', sent)
        from weekly_trend import article_analysis as articles
        for row in sent['paragraphs']:
            self.assertEqual(row['allowed_numeric_tokens'], sorted(articles._numbers(row['text'])))
        self.assertNotIn(PARAGRAPHS[2]['text'], (self.root / 'state/article-analysis.json').read_text())

    def test_valid_legacy_news_cache_is_preserved_after_guidance_change(self):
        self.run_good()
        path = self.root / 'state/article-analysis.json'
        state = read(path)
        for entry in state['entries'].values():
            entry['input_fingerprint'] = 'a' * 64
        write(path, state)
        self.calls.clear()
        self.run_good(now=NOW + timedelta(hours=25))
        self.assertFalse(self.calls)
        for key, old in state['entries'].items():
            new = read(path)['entries'][key]
            self.assertEqual(new['result'], old['result'])
            self.assertEqual(new['input_fingerprint'], old['input_fingerprint'])

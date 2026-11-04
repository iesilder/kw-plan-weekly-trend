"""Trusted request events and independent, persisted request deduplication."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_interests import registry_identity, request_identity, synchronize_registry
from weekly_trend import interest_requests

NOW = datetime(2026, 11, 4, 4, tzinfo=timezone.utc)


class RequestEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'event.json'
        self.env = {'GITHUB_REPOSITORY': 'iesilder/kw-plan-weekly-trend', 'GITHUB_REF': 'refs/heads/main',
                    'GITHUB_EVENT_NAME': 'issues', 'GITHUB_EVENT_PATH': str(self.path)}
        self.event = {'action': 'opened', 'repository': {'full_name': self.env['GITHUB_REPOSITORY']},
                      'issue': {'number': 42, 'user': {'login': 'iesilder'}, 'title': '[관심사 수집 요청]',
                                'html_url': 'https://github.com/iesilder/kw-plan-weekly-trend/issues/42',
                                'body': '### 확인\n\n- [X] 활성 경영진 관심사 수집을 요청합니다.\n'}}

    def submit(self):
        self.path.write_text(json.dumps(self.event))
        return request_identity(self.env)

    def test_operator_issue_and_explicit_dispatch_are_accepted(self):
        self.assertEqual(self.submit(), ('issue-42', self.event['issue']['html_url']))
        self.env.update(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_RUN_ID='123', GITHUB_ACTOR='iesilder')
        self.assertEqual(request_identity(self.env), ('dispatch-123', None))

    def test_untrusted_author_wrong_repository_or_unconfirmed_issue_is_rejected(self):
        cases = [lambda: self.event['issue']['user'].update(login='stranger'),
                 lambda: self.event['repository'].update(full_name='other/project'),
                 lambda: self.event['issue'].update(body='- [ ] 활성 경영진 관심사 수집을 요청합니다.'),
                 lambda: self.event['issue'].update(title='[관심사] 일반 등록'),
                 lambda: self.event['issue'].update(html_url='https://example.com/42')]
        original = json.dumps(self.event)
        for change in cases:
            self.event = json.loads(original)
            change()
            with self.assertRaises(ValueError):
                self.submit()

    def test_registration_lifecycle_and_renaming_rebuild_without_authorizing_collection(self):
        self.event['issue']['title'] = '[관심사] AI 적용 현황'
        for action in ('opened', 'edited', 'closed', 'reopened'):
            self.event['action'] = action
            self.path.write_text(json.dumps(self.event))
            self.assertEqual(registry_identity(self.env), 42)
            with self.assertRaises(ValueError):
                request_identity(self.env)
        self.event.update(action='edited', changes={'title': {'from': '[관심사] AI 적용 현황'}})
        self.event['issue']['title'] = '등록 취소'
        self.path.write_text(json.dumps(self.event))
        self.assertEqual(registry_identity(self.env), 42)
        self.event['issue']['user']['login'] = 'stranger'
        self.path.write_text(json.dumps(self.event))
        with self.assertRaises(ValueError):
            registry_identity(self.env)

    def test_registry_change_only_syncs_and_rebuilds_saved_raw(self):
        calls = []
        def sync(root, environ, now):
            calls.append('registry')
            return {'ok': True}
        def rebuild(root, now, rebuild_only):
            self.assertTrue(rebuild_only)
            calls.append('rebuild')
            return {'generation_id': 'live-saved-raw'}
        with patch('weekly_trend.ai.request_once', side_effect=AssertionError('AI forbidden')), \
             patch('weekly_trend.article_analysis.fetch_article', side_effect=AssertionError('article fetch forbidden')):
            result = synchronize_registry(self.root, environ={}, registry_sync=sync, rebuilder=rebuild,
                                          verifier=lambda root: calls.append('verify'))
        self.assertEqual(calls, ['registry', 'rebuild', 'verify'])
        self.assertEqual(result['generation_id'], 'live-saved-raw')
        calls.clear()
        with self.assertRaises(ValueError):
            synchronize_registry(self.root, registry_sync=lambda *a, **k: {'ok': False},
                                 rebuilder=rebuild, verifier=lambda root: calls.append('verify'))
        self.assertEqual(calls, [])


class InterestRequestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = []
        self.patches = [patch('weekly_trend.interests.prepare', return_value={'ok': True}),
                        patch('weekly_trend.interest_requests.interest_issue_ids', return_value={'issue-' + 'a' * 20})]
        for value in self.patches:
            value.start()
            self.addCleanup(value.stop)

    def registry(self, root, environ, now):
        self.calls.append('registry')
        return {'ok': True}

    def collector(self, root, now, interests_only, request_url):
        self.assertTrue(interests_only)
        self.calls.append('collection')
        return {'collection_status': 'success'}

    def analyze(self, root, environ, now, automatic, issue_ids):
        self.assertTrue(automatic)
        self.assertEqual(issue_ids, ['issue-' + 'a' * 20])
        self.calls.append('ai')
        return {'last_attempt': {'status': 'success', 'error_code': None}}

    def execute(self, request_id='issue-42', **kwargs):
        url = 'https://github.com/iesilder/kw-plan-weekly-trend/issues/' + request_id.removeprefix('issue-')
        return interest_requests.run(self.root, request_id, url, now=NOW, environ={},
                                     registry_sync=kwargs.pop('registry_sync', self.registry),
                                     collector=self.collector, title_runner=kwargs.pop('title_runner', self.analyze),
                                     article_runner=self.analyze, verifier=lambda root: None, **kwargs)

    def test_new_requests_run_after_daily_completion_but_saved_request_does_not_repeat(self):
        path = self.root / 'data/daily-run.json'
        path.parent.mkdir()
        path.write_text('daily completion is independent')
        first = self.execute()
        self.assertFalse(first['skipped'])
        self.assertEqual(self.calls, ['registry', 'collection', 'ai', 'ai'])
        self.calls.clear()
        self.assertTrue(self.execute()['skipped'])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.execute('issue-43')['skipped'])
        self.assertEqual(path.read_text(), 'daily completion is independent')

    def test_corrupt_saved_request_fails_before_registry_or_network(self):
        path = self.root / 'data/interests-requests.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'schema_version': '1.0', 'requests': {'issue-42': {'status': 'success'}}}))
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_registry_failure_does_not_collect_with_stale_new_topic(self):
        result = self.execute(registry_sync=lambda *a, **k: {'ok': False})
        self.assertEqual(self.calls, [])
        self.assertEqual(result['record']['status'], 'failed')
        self.assertEqual(result['record']['stages']['collection']['error_code'], 'registry_sync_failed')

    def test_one_ai_error_does_not_stop_other_analysis_and_never_logs_exception(self):
        def failed(*args, **kwargs):
            raise OSError('SECRET RESPONSE')
        result = self.execute(title_runner=failed)
        self.assertEqual(self.calls, ['registry', 'collection', 'ai'])
        self.assertEqual(result['record']['status'], 'partial_success')
        self.assertNotIn('SECRET', json.dumps(result))

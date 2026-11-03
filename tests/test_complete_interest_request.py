"""Post-publish request closure authorizes events without live GitHub writes."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from scripts import complete_interest_request as completion


class CompleteInterestRequestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.event_path = Path(temporary.name) / 'event.json'
        self.env = {'GITHUB_REPOSITORY': 'iesilder/kw-plan-weekly-trend', 'GITHUB_REF': 'refs/heads/main',
                    'GITHUB_EVENT_NAME': 'issues', 'GITHUB_EVENT_PATH': str(self.event_path), 'GITHUB_TOKEN': 'synthetic-test-token'}
        self.event = {'action': 'opened', 'repository': {'full_name': 'iesilder/kw-plan-weekly-trend'},
                      'issue': {'number': 42, 'user': {'login': 'iesilder'}, 'title': '[관심사 수집 요청]',
                                'html_url': 'https://github.com/iesilder/kw-plan-weekly-trend/issues/42',
                                'body': '### 확인\n\n- [X] 활성 경영진 관심사 수집을 요청합니다.\n'}}
        self.save_event()
        self.calls = []

    def save_event(self):
        self.event_path.write_text(json.dumps(self.event), encoding='utf-8')

    def transport(self, url, token, body, timeout):
        self.calls.append((url, token, body, timeout))
        return {'number': 42, 'state': 'closed', 'html_url': self.event['issue']['html_url'], 'body': 'not retained'}

    def test_valid_owner_request_patch_exact_identity_and_closed_response_idempotent(self):
        expected = {'ok': True, 'status': 'closed', 'issue_number': 42}
        self.assertEqual(completion.complete(self.env, self.transport), expected)
        self.assertEqual(completion.complete(self.env, self.transport), expected)
        self.assertEqual(self.calls, [('https://api.github.com/repos/iesilder/kw-plan-weekly-trend/issues/42',
                                      'synthetic-test-token', {'state': 'closed'}, completion.TIMEOUT)] * 2)
        self.assertNotIn('not retained', json.dumps(expected))

    def test_invalid_event_dispatch_or_missing_token_never_sends(self):
        baseline = copy.deepcopy(self.event)
        for change in (lambda: self.event.update(action='edited'),
                       lambda: self.event['repository'].update(full_name='other/repo'),
                       lambda: self.event['issue']['user'].update(login='other'),
                       lambda: self.event['issue'].update(pull_request={}),
                       lambda: self.event['issue'].update(title='[관심사] 등록'),
                       lambda: self.event['issue'].update(body='- [ ] 활성 경영진 관심사 수집을 요청합니다.'),
                       lambda: self.event['issue'].update(html_url='https://github.com/other/repo/issues/42')):
            self.event = copy.deepcopy(baseline)
            change()
            self.save_event()
            with self.assertRaises(completion.CompletionError):
                completion.complete(self.env, self.transport)
        self.event = baseline
        self.save_event()
        for env in ({**self.env, 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_ACTOR': 'iesilder', 'GITHUB_RUN_ID': '123'},
                    {**self.env, 'GITHUB_REPOSITORY': 'other/repo'}, {**self.env, 'GITHUB_REF': 'refs/heads/feature'},
                    {**self.env, 'GITHUB_TOKEN': ''}):
            with self.assertRaises(completion.CompletionError):
                completion.complete(env, self.transport)
        self.assertEqual(self.calls, [])

    def test_wrong_response_identity_open_state_or_pr_rejected(self):
        correct = {'number': 42, 'state': 'closed', 'html_url': self.event['issue']['html_url']}
        for response in (None, {'message': 'error'}, {**correct, 'number': 43}, {**correct, 'number': True},
                         {**correct, 'state': 'open'}, {**correct, 'html_url': 'https://github.com/other/repo/issues/42'},
                         {**correct, 'pull_request': {}}):
            with self.subTest(response=response):
                with self.assertRaises(completion.CompletionError):
                    completion.complete(self.env, lambda *args: response)

    def test_http_patch_method_auth_and_response_bounded_no_redirects(self):
        raw = json.dumps({'number': 42, 'state': 'closed', 'html_url': self.event['issue']['html_url']}).encode()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = raw
        opener = Mock()
        opener.open.return_value = response
        with patch.object(completion.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertTrue(completion.complete(self.env)['ok'])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'PATCH')
        self.assertEqual(request.full_url, 'https://api.github.com/repos/iesilder/kw-plan-weekly-trend/issues/42')
        self.assertEqual(json.loads(request.data), {'state': 'closed'})
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-test-token')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], completion.TIMEOUT)
        response.read.assert_called_once_with(completion.MAX_RESPONSE_BYTES + 1)
        handler = build.call_args.args[0]
        self.assertIsNone(handler.redirect_request(request, None, 302, 'Found', {}, 'https://other.example/'))

    def test_oversized_invalid_duplicate_json_and_http_errors_are_safe(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(completion.urllib.request, 'build_opener', return_value=opener):
            for raw in (b'x' * (completion.MAX_RESPONSE_BYTES + 1), b'not JSON', b'{"number":42,"number":43}'):
                response.read.return_value = raw
                with self.assertRaises(completion.CompletionError) as error:
                    completion.complete(self.env)
                self.assertNotIn('synthetic-test-token', str(error.exception))
            opener.open.side_effect = urllib.error.HTTPError('https://example.test', 403, 'SECRET HTTP BODY', {}, None)
            with self.assertRaises(completion.CompletionError) as error:
                completion.complete(self.env)
            self.assertNotIn('SECRET', str(error.exception))

    def test_cli_reports_only_generic_safe_code_and_nonzero_on_failure(self):
        with patch.object(completion, 'complete', side_effect=RuntimeError('SECRET PROVIDER RESPONSE')), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(completion.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {'ok': False, 'error_code': 'request_completion_failed'})
        self.assertNotIn('SECRET', output.getvalue())
        with patch.object(completion, 'complete', return_value={'ok': True, 'status': 'closed', 'issue_number': 42}), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(completion.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['issue_number'], 42)


if __name__ == '__main__':
    unittest.main()

"""Interest requests must never spend model calls on unrelated items or DART."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from analysis_fixture import install_analysis_fixture
from weekly_trend import ai, article_analysis

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
ENV = {'AI_FREE_PROJECT_CONFIRMED': 'true', 'GEMINI_API_KEY': 'test-only-key'}


def response(items):
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps({'items': items}, ensure_ascii=False)}]}]}


class InterestAIScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for folder in ('config', 'site/data', 'state', 'reports'):
            shutil.copytree(ROOT / folder, self.root / folder)
        latest = install_analysis_fixture(self.root)
        self.news = [v for v in latest['candidates'] if v['content_filter'] == 'topics']
        self.dart = next(v['issue_id'] for v in latest['candidates'] if v['content_filter'] == 'earnings_only')
        self.calls = []

    def title_transport(self, payload, key, timeout):
        items = json.loads(payload['input'])['items']
        self.calls.append(items)
        return response([{'issue_id': v['issue_id'], 'suggested_topic_id': 'digital', 'relevance': 'relevant',
                          'rationale': '사업 변화에 관한 기사이므로 검토할 수 있습니다.',
                          'check_points': ['추진 사항과 적용 범위를 확인하세요.'], 'duplicate_issue_ids': []} for v in items])

    def fetch(self, url, source):
        candidate = next(v for v in self.news if json.loads((self.root / 'site' / v['detail_path']).read_text())['evidence'][0]['source_url'] == url)
        text = '신규 서비스 출시를 추진하고 기관 고객에게 제공하는 방안을 검토한다.'
        return {'title': candidate['title'], 'paragraphs': [{'id': 'p1', 'text': text}],
                'content_hash': hashlib.sha256(text.encode()).hexdigest(), 'fetched_at': NOW.isoformat(), 'truncated': False}

    def body_transport(self, payload, key, timeout):
        items = json.loads(payload['input'])['items']
        self.calls.append(items)
        return response([{'issue_id': v['issue_id'], 'key_change': {'text': '신규 서비스 출시 추진', 'evidence_ids': ['p1']},
                          'facts': [{'text': '기관 고객 대상 제공 방안 검토', 'evidence_ids': ['p1']}],
                          'stage': '검토', 'why_candidate': '고객 대상 서비스 변화 관점에서 검토 가능'} for v in items])

    def test_title_only_selected_news_is_sent_and_other_pending_items_remain(self):
        wanted = self.news[0]['issue_id']
        result = ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW, automatic=True,
                        issue_ids=[wanted, self.dart])
        self.assertEqual([v['issue_id'] for v in self.calls[0]], [wanted])
        self.assertEqual(result['counts']['completed'], 1)
        self.assertEqual(result['counts']['not_analyzed'], len(self.news) - 1)
        state = ai.load_state(self.root)
        protected = copy.deepcopy(state['entries'])
        ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW, automatic=True, issue_ids=[])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(ai.load_state(self.root)['entries'], protected)

    def test_body_only_selected_news_is_fetched_and_summarized(self):
        wanted = self.news[-1]['issue_id']
        result = article_analysis.run(self.root, environ=ENV, fetcher=self.fetch, transport=self.body_transport,
                                      now=NOW, automatic=True, issue_ids=[wanted, self.dart])
        self.assertEqual([v['issue_id'] for v in self.calls[0]], [wanted])
        self.assertEqual(result['last_attempt']['fetch_count'], 1)
        self.assertEqual(result['counts']['completed'], 1)
        self.assertEqual(result['counts']['pending'], len(self.news) - 1)
        protected = copy.deepcopy(article_analysis.load_state(self.root)['entries'])
        article_analysis.run(self.root, environ=ENV, fetcher=self.fetch, transport=self.body_transport,
                             now=NOW, automatic=True, issue_ids=[])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(article_analysis.load_state(self.root)['entries'], protected)

    def test_interest_priority_is_first_within_new_work_without_exceeding_limits(self):
        wanted = max(self.news, key=lambda v: (v.get('published_date') or v['published_at'], v['issue_id']))['issue_id']
        config = json.loads((self.root / 'config/ai.json').read_text())
        config['max_items_per_run'] = 1
        (self.root / 'config/ai.json').write_text(json.dumps(config))
        ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW, automatic=True, priority_issue_ids=[wanted])
        self.assertEqual([v['issue_id'] for v in self.calls[0]], [wanted])
        self.calls.clear()
        article_analysis.run(self.root, environ=ENV, fetcher=self.fetch, transport=self.body_transport,
                             now=NOW, automatic=True, priority_issue_ids=[wanted])
        self.assertEqual(self.calls[0][0]['issue_id'], wanted)
        self.assertLessEqual(len(self.calls[0]), 5)

    def test_invalid_or_unknown_scope_fails_before_network(self):
        for module in (ai, article_analysis):
            for scope in ('all', ['issue-' + 'f' * 20], ['not-an-id']):
                with self.subTest(module=module.__name__, scope=scope), self.assertRaises(ValueError):
                    module.run(self.root, environ=ENV, now=NOW, issue_ids=scope)
        self.assertEqual(self.calls, [])

"""A submitted registry item must become a scoped manual collection and reuse caches."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from test_interests import issue, form
from weekly_trend import ai, article_analysis, interests, interest_requests
from weekly_trend.live_pipeline import run_collection
from weekly_trend.pipeline import verify

ROOT = Path(__file__).resolve().parents[1]


def reply(items):
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps({'items': items}, ensure_ascii=False)}]}]}


class InterestEndToEndTests(unittest.TestCase):
    def test_register_then_collect_now_without_repeating_daily_or_unrelated_ai(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for folder in ('config', 'reports', 'state', 'data', 'site/data'):
                shutil.copytree(ROOT / folder, root / folder)
            for name in ('interests-registry.json', 'interests-requests.json', 'interests-collection.json'):
                (root / 'data' / name).unlink(missing_ok=True)
            (root / 'config/interests.json').write_text(json.dumps({'schema_version': '1.0', 'interests': []}))
            status = json.loads((root / 'site/data/status.json').read_text())
            moment = max(datetime(2026, 11, 4, 4, tzinfo=timezone.utc),
                         datetime.fromisoformat(status['last_run_at'].replace('Z', '+00:00')) + timedelta(hours=1))
            protected = {name: (root / name).read_bytes() for name in ('reports/reviews.json', 'reports/history.json', 'data/daily-run.json')}
            with sqlite3.connect(root / 'data/raw.sqlite3') as db:
                baseline = db.execute('SELECT source_id,state_json FROM live_source_state ORDER BY source_id').fetchall()
            registration = issue(12, body=form(**{'관심 주제': '반려금융 사업', '검색어': '반려금융', '제외 검색어': '_No response_'}))
            env = {'GITHUB_TOKEN': 'synthetic-registry-key', 'GEMINI_API_KEY': 'synthetic-model-key', 'AI_FREE_PROJECT_CONFIRMED': 'true'}
            title = '미래에셋증권, 반려금융 전용채널 구축'
            uri = 'https://news.einfomax.co.kr/news/articleView.html?idxno=990000000001'
            calls = {'rss': 0, 'title': [], 'body': []}

            def sync(root, environ, now):
                return interests.sync_from_github(root, environ=environ, now=now,
                    transport=lambda *args: [registration])

            def source(source, **kwargs):
                self.assertEqual(source['type'], 'rss')
                calls['rss'] += 1
                rows = [] if source['id'] != 'einfomax-s1n2' else [
                    {'uri': uri, 'title': title, 'published_at': (moment - timedelta(hours=1)).isoformat(),
                     'source_updated_at': None, 'content_level': 'metadata_only', 'content_text': ''}]
                return {'status': 'success', 'records': rows, 'fetched_count': len(rows), 'etag': '"fixture"', 'last_modified': None}

            def collect(root, **kwargs):
                return run_collection(root, collector=source, **kwargs)

            def classify(payload, key, timeout):
                rows = json.loads(payload['input'])['items']
                calls['title'].extend(rows)
                return reply([{'issue_id': row['issue_id'], 'suggested_topic_id': 'digital', 'relevance': 'relevant',
                               'rationale': '신규 고객 서비스 변화이므로 검토할 수 있습니다.',
                               'check_points': ['서비스의 적용 범위를 확인하세요.'], 'duplicate_issue_ids': []} for row in rows])

            paragraph = '반려금융 전용채널을 구축하고 기관 고객에게 신규 서비스를 제공하는 방안을 검토한다.'

            def fetch(url, source):
                self.assertEqual(url, uri)
                return {'title': title, 'paragraphs': [{'id': 'p1', 'text': paragraph}],
                        'content_hash': hashlib.sha256(paragraph.encode()).hexdigest(),
                        'fetched_at': moment.isoformat(), 'truncated': False}

            def summarize(payload, key, timeout):
                rows = json.loads(payload['input'])['items']
                calls['body'].extend(rows)
                return reply([{'issue_id': row['issue_id'], 'key_change': {'text': '반려금융 전용채널 구축', 'evidence_ids': ['p1']},
                               'facts': [{'text': '기관 고객 대상 신규 서비스 제공 방안 검토', 'evidence_ids': ['p1']}],
                               'stage': '검토', 'why_candidate': '고객 서비스 변화 관점에서 검토 가능'} for row in rows])

            def titles(root, **kwargs):
                return ai.run(root, transport=classify, **kwargs)

            def bodies(root, **kwargs):
                return article_analysis.run(root, fetcher=fetch, transport=summarize, **kwargs)

            def execute(number):
                return interest_requests.run(root, f'issue-{number}',
                    f'https://github.com/iesilder/kw-plan-weekly-trend/issues/{number}',
                    environ=env, now=moment, registry_sync=sync, collector=collect,
                    title_runner=titles, article_runner=bodies)

            result = execute(99)
            self.assertEqual(result['record']['status'], 'success')
            self.assertEqual(result['record']['matched_count'], 1)
            self.assertEqual(len(calls['title']), 1)
            self.assertEqual(len(calls['body']), 1)
            self.assertEqual(calls['title'][0]['news_metadata'][0]['title'], title)
            self.assertTrue(execute(99)['skipped'])
            self.assertFalse(execute(100)['skipped'])
            self.assertEqual(len(calls['title']), 1)
            self.assertEqual(len(calls['body']), 1)
            self.assertTrue(verify(root)['ok'])
            with sqlite3.connect(root / 'data/raw.sqlite3') as db:
                self.assertEqual(db.execute('SELECT source_id,state_json FROM live_source_state ORDER BY source_id').fetchall(), baseline)
            for name, content in protected.items():
                self.assertEqual((root / name).read_bytes(), content)
            public = json.loads((root / 'site/data/interests.json').read_text())
            self.assertEqual(public['interests'][0]['match_count'], 1)

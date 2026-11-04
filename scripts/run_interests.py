"""Run an authorized interest collection request, or prepare/check its sidecar."""

import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPOSITORY = 'iesilder/kw-plan-weekly-trend'


def registry_identity(environ):
    """Authorize owner registrations for offline reclassification only."""
    if (environ.get('GITHUB_REPOSITORY') != REPOSITORY
            or environ.get('GITHUB_REF') != 'refs/heads/main'
            or environ.get('GITHUB_EVENT_NAME') != 'issues'):
        raise ValueError('Unsupported interest registration event')
    path = Path(environ['GITHUB_EVENT_PATH'])
    if path.stat().st_size > 1048576:
        raise ValueError('Oversized interest registration event')
    event = json.loads(path.read_text(encoding='utf-8'))
    issue = event.get('issue', {})
    number = issue.get('number')
    old_title = event.get('changes', {}).get('title', {}).get('from', '')
    if (event.get('action') not in {'opened', 'edited', 'closed', 'reopened'}
            or event.get('repository', {}).get('full_name') != REPOSITORY
            or issue.get('user', {}).get('login', '').casefold() != 'iesilder'
            or 'pull_request' in issue or type(number) is not int or not 1 <= number < 10**19
            or not (issue.get('title', '').startswith('[관심사]') or old_title.startswith('[관심사]'))
            or issue.get('html_url') != f'https://github.com/{REPOSITORY}/issues/{number}'):
        raise ValueError('Unauthorized interest registration event')
    return number


def synchronize_registry(root, environ=None, now=None, registry_sync=None, rebuilder=None, verifier=None):
    """Use stored raw metadata after a registry change; no source or AI call."""
    from weekly_trend.interests import sync_from_github
    from weekly_trend.live_pipeline import run_collection
    from weekly_trend.pipeline import verify
    result = (registry_sync or sync_from_github)(root, environ=environ, now=now)
    if not result.get('ok'):
        raise ValueError('Interest registry synchronization failed')
    snapshot = (rebuilder or run_collection)(root, now=now, rebuild_only=True)
    (verifier or verify)(root)
    return {'ok': True, 'mode': 'registry_rebuilt', 'registry': result,
            'generation_id': snapshot['generation_id'], 'request_count': 0}


def request_identity(environ):
    if environ.get('GITHUB_REPOSITORY') != REPOSITORY or environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Unsupported collection repository or ref')
    if environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        if environ.get('GITHUB_ACTOR', '').casefold() != 'iesilder':
            raise ValueError('Only the configured operator can request collection')
        run_id = environ.get('GITHUB_RUN_ID', '')
        if not re.fullmatch(r'[1-9][0-9]{0,18}', run_id):
            raise ValueError('Invalid dispatch identity')
        return 'dispatch-' + run_id, None
    if environ.get('GITHUB_EVENT_NAME') != 'issues':
        raise ValueError('Unsupported interest request event')
    path = Path(environ['GITHUB_EVENT_PATH'])
    if path.stat().st_size > 1048576:
        raise ValueError('Oversized interest request event')
    event = json.loads(path.read_text(encoding='utf-8'))
    issue = event.get('issue', {})
    number = issue.get('number')
    if (event.get('action') != 'opened' or event.get('repository', {}).get('full_name') != REPOSITORY
            or issue.get('user', {}).get('login', '').casefold() != 'iesilder'
            or 'pull_request' in issue or type(number) is not int or not 1 <= number < 10**19
            or not issue.get('title', '').startswith('[관심사 수집 요청]')
            or not re.search(r'^-\s+\[[xX]\]\s+활성 (?:최근 관심 주제|경영진 관심사) 수집을 요청합니다\.\s*$', issue.get('body') or '', re.M)):
        raise ValueError('Unauthorized or invalid interest collection request')
    url = f'https://github.com/{REPOSITORY}/issues/{number}'
    if issue.get('html_url') != url:
        raise ValueError('Request URL does not match its issue')
    return f'issue-{number}', url


def main():
    parser = argparse.ArgumentParser(description='Interest registration mirror and explicit collection requests')
    parser.add_argument('--root', type=Path, default=Path('.'))
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--prepare', action='store_true')
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--sync', action='store_true')
    args = parser.parse_args()
    try:
        if args.sync:
            registry_identity(os.environ)
            result = synchronize_registry(args.root, environ=os.environ)
        elif args.run:
            from weekly_trend.interest_requests import run
            request_id, url = request_identity(os.environ)
            result = run(args.root, request_id, url)
        else:
            from weekly_trend.interests import prepare, check
            from weekly_trend.finance_all import prepare as prepare_finance, check as check_finance
            result = (prepare if args.prepare else check)(args.root)
            result['finance_all'] = (prepare_finance if args.prepare else check_finance)(args.root)
    except Exception:
        parser.exit(1, 'Interest request validation failed. No unverified snapshot should be published.\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.run and os.environ.get('GITHUB_OUTPUT'):
        failed = result['record']['status'] == 'failed'
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as handle:
            handle.write(f'request_failed={str(failed).lower()}\n')
    if args.run and os.environ.get('GITHUB_STEP_SUMMARY'):
        record = result['record']
        lines = ['## 최근 관심 주제 별도 수집', '',
                 '이미 저장한 요청이므로 다시 수집하지 않았습니다.' if result['skipped'] else '활성 관심사를 기준으로 별도 수집·기사 분석을 수행했습니다.',
                 '', '| 단계 | 상태 | 사유 |', '|---|---|---|']
        for name, label in [('collection', '관심사 수집'), ('titles', '기사 제목 분류'), ('articles', '기사 본문 요약')]:
            value = record['stages'][name]
            lines.append(f"| {label} | {value['status']} | {value['error_code'] or 'none'} |")
        lines += ['', f"현재 연결 기사: {record['matched_count']}건", '',
                  '수집·저장 성공과 모든 기사 분석 완료는 다릅니다. 대기·실패 상태를 함께 확인합니다.', '']
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as handle:
            handle.write('\n'.join(lines))


if __name__ == '__main__':
    main()

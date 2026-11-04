"""Temporary real-source reader check; never prints or stores full bodies."""

import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend.article_sources import ArticleError, fetch_article


def main():
    root = Path(__file__).resolve().parents[1]
    source = next(row for row in json.loads((root / 'config/sources.json').read_text())['sources'] if row['id'] == 'fsc-press-releases')
    latest = json.loads((root / 'site/data/latest.json').read_text())
    targets = []
    for row in latest['candidates']:
        if not re.fullmatch(r'issue-[a-f0-9]{20}', row['issue_id']):
            raise ValueError('Invalid issue identity')
        detail = json.loads((root / 'site/data/issues' / (row['issue_id'] + '.json')).read_text())
        evidence = detail.get('evidence', [])
        if len(evidence) == 1 and evidence[0].get('source_id') == source['id']:
            targets.append((row, evidence[0]))
    rows = []
    for candidate, evidence in targets[:6]:
        try:
            body = fetch_article(evidence['source_url'], source)
            matches = ' '.join(body['title'].split()) == ' '.join(candidate['title'].split())
            rows.append({'issue_id': candidate['issue_id'], 'status': 'success' if matches else 'title_mismatch',
                         'title_matches': matches, 'paragraph_count': len(body['paragraphs']),
                         'body_chars': sum(len(p['text']) for p in body['paragraphs']), 'truncated': body['truncated']})
        except ArticleError as error:
            rows.append({'issue_id': candidate['issue_id'], 'status': 'failed', 'error_code': error.code})
        except Exception:
            rows.append({'issue_id': candidate['issue_id'], 'status': 'failed', 'error_code': 'unexpected_probe_error'})
    ok = bool(rows) and all(row['status'] == 'success' for row in rows)
    print(json.dumps({'status': 'success' if ok else 'failed', 'target_count': len(targets), 'checked_count': len(rows), 'results': rows}, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

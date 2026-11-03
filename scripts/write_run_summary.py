"""Summarize the verified snapshot without exposing HTTP payloads or credentials."""
import json
import os
from pathlib import Path

status = json.loads(Path('site/data/status.json').read_text(encoding='utf-8'))
demo = status.get('is_demo') is True
lines = ['## Fictional demo snapshot' if demo else '## Public metadata collection', '',
         'Collection stores metadata only. News AI and daily scheduling have separate status.', '', '| Item | Value |', '| --- | --- |']
for key in ('last_run_at', 'last_successful_run_at', 'generation_id', 'article_count', 'version_count', 'issue_count', 'run_count',
            'inserted_versions', 'duplicate_count', 'collection_status', 'publication_status',
            'archived_article_count', 'total_article_count', 'report_count', 'interests_count'):
    if key in status:
        lines.append(f'| {key} | {status[key]} |')
if not demo:
    lines += ['', '| Source | Status | Received | Matched | Last success |',
              '| --- | --- | ---: | ---: | --- |']
    for source in status.get('sources', []):
        cells = [source.get('source_name', source.get('source_id', '')), source.get('status', ''),
                 source.get('fetched_count', 0), source.get('accepted_count', 0), source.get('last_success_at') or 'none']
        lines.append('| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in cells) + ' |')
    lines += ['', 'Only permitted disclosure/news metadata and original source links are retained. No descriptions, article bodies, images or attachments.',
              'Unchanged inputs must not create new article versions. New or corrected live filings may legitimately change counts.']
summary = '\n'.join(lines) + '\n'
print(summary)
if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
        handle.write(summary)

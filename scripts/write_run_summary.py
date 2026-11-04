"""Summarize the verified snapshot without exposing HTTP payloads or credentials."""
import json
import os
from pathlib import Path

status = json.loads(Path('site/data/status.json').read_text(encoding='utf-8'))
demo = status.get('is_demo') is True
lines = ['## Fictional demo snapshot' if demo else '## Public metadata collection', '',
         'Valid metadata is stored before topic/interest filtering. The dashboard mart is derived from stored metadata; AI runs separately.', '', '| Item | Value |', '| --- | --- |']
for key in ('last_run_at', 'last_successful_run_at', 'generation_id', 'capture_schema_version', 'execution_mode', 'collection_performed', 'source_mode', 'raw_article_count', 'raw_version_count', 'mart_article_count', 'mart_excluded_count', 'article_count', 'version_count', 'issue_count', 'run_count',
            'inserted_versions', 'duplicate_count', 'collection_status', 'publication_status',
            'archived_article_count', 'total_article_count', 'report_count', 'interests_count'):
    if key in status:
        lines.append(f'| {key} | {status[key]} |')
if not demo:
    raw = status.get('capture_schema_version') == 'raw-metadata-v1'
    lines += ['', '| Source | Status | Received | Valid raw | New versions | Duplicates | Mart included | Mart excluded | Last success |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for source in status.get('sources', []):
        cells = [source.get('source_name', source.get('source_id', '')), source.get('status', ''),
                 source.get('fetched_count', 0), source.get('raw_saved_count', 'legacy filtered' if not raw else source.get('accepted_count', 0)),
                 source.get('inserted_versions', 0), source.get('duplicate_count', 0),
                 source.get('mart_included_count', 'unknown'), source.get('mart_excluded_count', 'unknown'),
                 source.get('last_success_at') or 'none']
        lines.append('| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in cells) + ' |')
    if raw:
        lines += ['', 'Valid raw counts include duplicates. Mart counts above describe the last response, not cumulative unique URLs.',
                  'Excluded valid metadata remains stored for later reclassification.']
        if status.get('execution_mode') == 'mart_rebuild':
            lines += ['', 'This execution rebuilt the mart from stored metadata without source collection or AI calls.']
    lines += ['', 'Only permitted disclosure/news metadata and original source links are retained. No descriptions, article bodies, images or attachments.',
              'Unchanged inputs must not create new article versions. New or corrected live filings may legitimately change counts.']
summary = '\n'.join(lines) + '\n'
print(summary)
if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
        handle.write(summary)

"""Temporary read-only transport diagnosis; no HTML/body/contact output or files."""

import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend import article_sources as articles, collectors

URL = 'https://www.fsc.go.kr/no010101/88054'


def exception_metadata(error):
    return {'exception_type': type(error).__name__, 'reason_type': type(getattr(error, 'reason', None)).__name__,
            'http_code': getattr(error, 'code', None) if isinstance(getattr(error, 'code', None), int) else None}


class TracedResponse:
    def __init__(self, response, record):
        self.response, self.record = response, record
    def __enter__(self):
        self.response.__enter__()
        return self
    def __exit__(self, *args):
        return self.response.__exit__(*args)
    def __getattr__(self, name):
        return getattr(self.response, name)
    def read(self, size):
        self.record['stage'] = 'body_read'
        try:
            value = self.response.read(size)
            self.record['bytes_read'] = self.record.get('bytes_read', 0) + len(value)
            return value
        except Exception as error:
            self.record.update(exception_metadata(error))
            raise


def traced_factory(records):
    def factory(*handlers):
        opener = build_opener(*handlers)
        class TracedOpener:
            def open(self, request, timeout):
                record = {'stage': 'connect_open', 'timeout_seconds': timeout}
                records.append(record)
                started = time.monotonic()
                try:
                    response = opener.open(request, timeout=timeout)
                    record.update(stage='response_headers', status=response.status,
                                  open_elapsed_ms=round((time.monotonic()-started)*1000))
                    return TracedResponse(response, record)
                except Exception as error:
                    record.update(exception_metadata(error), open_elapsed_ms=round((time.monotonic()-started)*1000))
                    raise
        return TracedOpener()
    return factory


def main():
    root = Path(__file__).resolve().parents[1]
    source = next(row for row in json.loads((root/'config/sources.json').read_text())['sources'] if row['id']=='fsc-press-releases')
    expected_title = next(json.loads(p.read_text())['title'] for p in (root/'site/data/issues').glob('*.json')
                          if any(row.get('source_url')==URL for row in json.loads(p.read_text()).get('evidence', [])))
    results = []
    for mode in ('raw_single', 'article_reader', 'collector_transport'):
        trace, started = [], time.monotonic()
        result = {'mode': mode}
        try:
            if mode == 'raw_single':
                expected = articles.article_url(URL, source, resolve=True)
                opener = traced_factory(trace)(articles._ArticleRedirect(expected, source))
                with opener.open(Request(expected, headers={'Accept':'text/html','Accept-Encoding':'identity'}), timeout=collectors.TIMEOUT_SECONDS) as response:
                    if articles.article_url(response.geturl(), source) != expected:
                        raise articles.ArticleError('unexpected_redirect')
                    data = collectors._read_response(response)
                body = articles.extract_article(data.decode('utf-8'), expected, source)
            elif mode == 'article_reader':
                with patch('weekly_trend.article_sources.build_opener', side_effect=traced_factory(trace)):
                    body = articles.fetch_article(URL, source)
            else:
                with patch('weekly_trend.collectors.build_opener', side_effect=traced_factory(trace)):
                    data, _ = collectors._fetch({**source, 'url':URL}, {})
                body = articles.extract_article(data.decode('utf-8'), URL, source)
            matches = ' '.join(body['title'].split()) == ' '.join(expected_title.split())
            result.update(status='success' if matches else 'title_mismatch', title_matches=matches,
                          paragraph_count=len(body['paragraphs']), body_chars=sum(len(p['text']) for p in body['paragraphs']), truncated=body['truncated'])
        except Exception as error:
            result.update(status='failed', error_code=getattr(error, 'code', None), **exception_metadata(error))
        result.update(elapsed_ms=round((time.monotonic()-started)*1000), requests=trace)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    ok = any(row.get('status')=='success' for row in results)
    print(json.dumps({'diagnostic_status':'success' if ok else 'failed','results':results}, ensure_ascii=False))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

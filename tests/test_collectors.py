from datetime import datetime, timezone
from io import BytesIO
import socket
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from weekly_trend.collectors import (CollectionError, MAX_RESPONSE_BYTES, _SafeRedirect,
                                    collect_source, validate_url)


NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
SOURCE = {"id": "dart-company", "name": "DART", "enabled": True, "type": "rss", "adapter": "dart_rss",
          "url": "https://dart.fss.or.kr/api/companyRSS.xml?crpCd=00111722",
          "allowed_hosts": ["dart.fss.or.kr"], "retention": "metadata_only", "policy_status": "verified",
          "retention_basis": "approved metadata", "retention_url": "https://dart.fss.or.kr/introduction/content6.do"}
FEED = b'''<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><item>
<title><![CDATA[<b>Capital</b> <script>secret-body</script> &amp; policy]]></title>
<link>http://dart.fss.or.kr/api/link.jsp?rcpNo=20261102000001</link>
<pubDate>Mon, 02 Nov 2026 09:00:00 +0900</pubDate>
<description>NEVER RETAIN THIS ARTICLE BODY</description>
</item></channel></rss>'''
DART_HTML = {**SOURCE, "type": "dart_html", "adapter": "dart_html", "corp_code": "00111722",
             "company": "Example Securities", "url": "https://dart.fss.or.kr/dsab001/search.ax"}


def dart_page(page=1, pages=1, total=1, first=1, count=1, corp_code="00111722"):
    rows = []
    for index in range(first, first + count):
        receipt = f"20261102{index:06d}"
        rows.append(f'''<tr><td>{index}</td><td><a onclick="openCorpInfoNew('{corp_code}', 'info');">Issuer</a></td>
        <td><a href="/dsaf001/main.do?rcpNo={receipt}" title="report viewer">Capital report {index}</a></td>
        <td>PERSONAL SUBMITTER NEVER RETAIN</td><td>2026.11.02</td><td>other</td></tr>''')
    return (f'<table class="tbList"><tbody id="tbody">{"".join(rows)}</tbody></table>'
            f'<div class="pageInfo">[{page}/{pages}] [총 {total:,}건]</div>').encode("utf-8")


class Response(BytesIO):
    status = 200

    def __init__(self, body, headers=None, url=None):
        super().__init__(body)
        self.headers = headers or {}
        self.url = url or SOURCE["url"]

    def geturl(self):
        return self.url


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.proxies = patch("weekly_trend.collectors.getproxies", return_value={})
        self.proxies.start()
        self.addCleanup(self.proxies.stop)
        self.dns = patch("weekly_trend.collectors.socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
        self.dns.start()
        self.addCleanup(self.dns.stop)
        self.sleep = patch("weekly_trend.collectors.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def collect(self, body=FEED, source=None, headers=None, previous=None):
        source = source or SOURCE
        with patch("weekly_trend.collectors.build_opener") as factory:
            factory.return_value.open.return_value = Response(body, headers, source["url"])
            result = collect_source(source, NOW, previous)
            return result, factory.return_value.open

    def test_dart_metadata_only_and_canonical_disclosure_url(self):
        result, _ = self.collect()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["fetched_count"], 1)
        article = result["records"][0]
        self.assertEqual(article["uri"], "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261102000001")
        self.assertEqual(article["title"], "Capital & policy")
        self.assertEqual(article["published_at"], "2026-11-02T00:00:00+00:00")
        self.assertIsNone(article["source_updated_at"])
        self.assertEqual(article["content_text"], "")
        self.assertNotIn("ARTICLE BODY", str(result))
        self.assertNotIn("secret-body", str(result))

    def test_atom_dates_and_alternate_link(self):
        source = {**SOURCE, "adapter": "generic_rss"}
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Strategy</title>
        <link rel="self" href="https://dart.fss.or.kr/feed"/><link href="https://dart.fss.or.kr/news/1"/>
        <published>2026-11-01T01:00:00Z</published><updated>2026-11-02T02:00:00+09:00</updated>
        <content>private article</content></entry></feed>'''
        result, _ = self.collect(body, source)
        self.assertEqual(result["status"], "success")
        article = result["records"][0]
        self.assertEqual(article["uri"], "https://dart.fss.or.kr/news/1")
        self.assertEqual(article["source_updated_at"], "2026-11-01T17:00:00+00:00")
        self.assertNotIn("private article", str(result))

    def test_missing_or_timezone_unknown_dates_remain_null(self):
        for stamp in (b"", b"2026-11-02", b"Mon, 02 Nov 2026 09:00:00", b"not a date", b"9999-12-31T23:00:00-12:00"):
            body = FEED.replace(b"Mon, 02 Nov 2026 09:00:00 +0900", stamp)
            result, _ = self.collect(body)
            self.assertIsNone(result["records"][0]["published_at"])

    def test_nested_content_cannot_become_a_feed_record(self):
        body = FEED.replace(b"NEVER RETAIN THIS ARTICLE BODY", b'<item><title>hidden-body</title><link>https://dart.fss.or.kr/api/link.jsp?rcpNo=20261102000002</link></item>')
        result, _ = self.collect(body)
        self.assertEqual(result["fetched_count"], 1)
        self.assertNotIn("hidden-body", str(result))

    def test_not_modified_preserves_validators_and_returns_no_records(self):
        previous = {"etag": '"version1"', "last_modified": "Mon, 02 Nov 2026 00:00:00 GMT"}
        with patch("weekly_trend.collectors.build_opener") as factory:
            factory.return_value.open.side_effect = HTTPError(SOURCE["url"], 304, "Not modified", {}, BytesIO())
            result = collect_source(SOURCE, NOW, previous)
            self.assertEqual(result["status"], "not_modified")
            self.assertEqual(result["etag"], previous["etag"])
            self.assertEqual(result["records"], [])
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("If-none-match"), previous["etag"])

    def test_success_replaces_old_etag_and_limits_headers(self):
        result, _ = self.collect(headers={"ETag": '"new"', "Last-Modified": "bad\r\nheader"}, previous={"etag": '"old"'})
        self.assertEqual(result["etag"], '"new"')
        self.assertIsNone(result["last_modified"])

    def test_standard_urllib_client_identifier_is_not_overridden(self):
        _, opened = self.collect()
        request = opened.call_args.args[0]
        self.assertIsNone(request.get_header("User-agent"))
        self.assertEqual(request.get_header("Accept-encoding"), "identity")

    def test_retries_once_without_persisting_error_response(self):
        with patch("weekly_trend.collectors.build_opener") as factory:
            factory.return_value.open.side_effect = [HTTPError(SOURCE["url"], 503, "sensitive", {}, BytesIO(b"secret")),
                                                     Response(FEED)]
            result = collect_source(SOURCE, NOW)
            self.assertEqual(result["status"], "success")
            self.assertEqual(factory.return_value.open.call_count, 2)
        with patch("weekly_trend.collectors.build_opener") as factory:
            factory.return_value.open.side_effect = URLError("secret-token-in-error")
            result = collect_source(SOURCE, NOW)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(factory.return_value.open.call_count, 2)
            self.assertNotIn("secret-token", str(result))

    def test_non_retryable_http_error(self):
        with patch("weekly_trend.collectors.build_opener") as factory:
            factory.return_value.open.side_effect = HTTPError(SOURCE["url"], 403, "Denied", {}, BytesIO())
            result = collect_source(SOURCE, NOW)
            self.assertEqual(result["error_code"], "http_error")
            self.assertEqual(factory.return_value.open.call_count, 1)

    def test_policy_gates_prevent_any_request(self):
        for change in ({"enabled": False}, {"retention": "full_text"}, {"policy_status": "unverified"}, {"rss_prohibited": True}):
            with patch("weekly_trend.collectors.build_opener") as factory:
                result = collect_source({**SOURCE, **change}, NOW)
                self.assertEqual(result["error_code"], "source_not_approved")
                factory.assert_not_called()

    def test_unsafe_feed_urls_and_private_dns_are_blocked(self):
        for url in ("file:///tmp/input", "demo://fixture/1", "javascript:alert(1)",
                    "https://127.0.0.1/feed", "https://dart.fss.or.kr@evil.example/feed",
                    "https://dart.fss.or.kr:8443/feed", "https://dart.fss.or.kr.evil.example/feed"):
            result = collect_source({**SOURCE, "url": url}, NOW)
            self.assertEqual(result["error_code"], "unsafe_url")
        with patch("weekly_trend.collectors.socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]):
            result = collect_source(SOURCE, NOW)
            self.assertEqual(result["error_code"], "unsafe_address")

    def test_redirect_checks_target_before_following(self):
        handler = _SafeRedirect({"dart.fss.or.kr"})
        request = Request(SOURCE["url"])
        for target in ("http://dart.fss.or.kr/feed", "https://evil.example/feed", "https://127.0.0.1/feed"):
            with self.assertRaises(CollectionError):
                handler.redirect_request(request, None, 302, "redirect", {}, target)
        redirect = handler.redirect_request(request, None, 302, "redirect", {}, "https://dart.fss.or.kr/other")
        self.assertEqual(redirect.full_url, "https://dart.fss.or.kr/other")

    def test_configured_https_proxy_resolves_host_without_local_dns(self):
        with patch("weekly_trend.collectors.getproxies", return_value={"https": "http://proxy.invalid:8080"}), \
                patch("weekly_trend.collectors.proxy_bypass", return_value=False), \
                patch("weekly_trend.collectors.socket.getaddrinfo", side_effect=OSError("No local DNS")) as dns:
            self.assertEqual(validate_url(SOURCE["url"], {"dart.fss.or.kr"}, resolve=True), SOURCE["url"])
            dns.assert_not_called()
            for url, hosts in (("https://127.0.0.1/feed", {"127.0.0.1"}),
                               ("https://user:password@dart.fss.or.kr/feed", {"dart.fss.or.kr"}),
                               ("https://evil.example/feed", {"dart.fss.or.kr"})):
                with self.assertRaises(CollectionError):
                    validate_url(url, hosts, resolve=True)

    def test_proxy_bypass_still_requires_direct_public_dns(self):
        with patch("weekly_trend.collectors.getproxies", return_value={"https": "http://proxy.invalid:8080"}), \
                patch("weekly_trend.collectors.proxy_bypass", return_value=True), \
                patch("weekly_trend.collectors.socket.getaddrinfo", side_effect=OSError("No local DNS")) as dns:
            with self.assertRaises(CollectionError) as raised:
                validate_url(SOURCE["url"], {"dart.fss.or.kr"}, resolve=True)
            self.assertEqual(raised.exception.code, "dns_error")
            dns.assert_called_once()

    def test_unsafe_article_urls_are_not_returned(self):
        for link in (b"javascript:alert(1)", b"https://evil.example/news", b"http://dart.fss.or.kr/api/link.jsp?rcpNo=wrong"):
            body = FEED.replace(b"http://dart.fss.or.kr/api/link.jsp?rcpNo=20261102000001", link)
            result, _ = self.collect(body)
            self.assertEqual(result["error_code"], "no_valid_items")
            self.assertEqual(result["records"], [])

    def test_dtd_entity_and_invalid_xml_are_rejected(self):
        cases = [(b'<!DOCTYPE rss [<!ENTITY x "secret">]><rss/>', "unsafe_xml"),
                 ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss><rss/>'.encode("utf-16"), "unsafe_xml"),
                 (b"<rss><broken>", "invalid_xml"), (b"<html>Access denied</html>", "invalid_feed")]
        for body, code in cases:
            result, _ = self.collect(body)
            self.assertEqual(result["error_code"], code)

    def test_response_size_and_compression_are_bounded(self):
        result, _ = self.collect(b"x" * (MAX_RESPONSE_BYTES + 1))
        self.assertEqual(result["error_code"], "response_too_large")
        result, _ = self.collect(headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)})
        self.assertEqual(result["error_code"], "response_too_large")
        result, _ = self.collect(headers={"Content-Encoding": "gzip"})
        self.assertEqual(result["error_code"], "unsupported_encoding")

    def test_fsc_only_subject_link_date_no_attachments_or_body(self):
        source = {**SOURCE, "id": "fsc", "name": "FSC", "type": "html", "adapter": "fsc_html",
                  "url": "https://www.fsc.go.kr/no010101", "allowed_hosts": ["www.fsc.go.kr"]}
        body = b'''<a href="https://evil.example">other menu</a><div class="board-list-wrap"><ul><li>
        <div class="inner"><div class="cont"><div class="subject"><a href="/no010101/88054?curPage=1"
        title="Regulatory announcement">Regulatory announcement</a></div><div class="info">staff info</div>
        <div class="file"><div><a href="/file.pdf">attachment text</a></div></div></div>
        <div class="day">2026-11-02</div></div></li></ul></div>'''
        result, _ = self.collect(body, source)
        self.assertEqual(result["status"], "success")
        record = result["records"][0]
        self.assertEqual(record["uri"], "https://www.fsc.go.kr/no010101/88054")
        self.assertEqual(record["published_date"], "2026-11-02")
        self.assertIsNone(record["published_at"])
        self.assertNotIn("staff info", str(result))
        self.assertNotIn("attachment", str(result))

    def test_timezone_required_and_empty_feed_distinct_from_invalid_page(self):
        self.assertEqual(collect_source(SOURCE, datetime(2026, 11, 2))["error_code"], "invalid_clock")
        result, _ = self.collect(b"<rss><channel/></rss>")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["fetched_count"], 0)

    def test_dart_html_pages_company_scope_and_date_precision(self):
        with patch("weekly_trend.collectors._fetch", side_effect=[(dart_page(pages=2, total=101, count=100), {}),
                (dart_page(page=2, pages=2, total=101, first=101), {})]) as fetch:
            result = collect_source(DART_HTML, NOW)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["records"]), 101)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["total_count"], 101)
        first = result["records"][0]
        self.assertIsNone(first["published_at"])
        self.assertEqual(first["published_date"], "2026-11-02")
        self.assertEqual(first["title"], "Capital report 1")
        self.assertNotIn("PERSONAL SUBMITTER", str(result))
        self.assertEqual(first["content_text"], "")
        query1 = parse_qs(urlsplit(fetch.call_args_list[0].args[0]["url"]).query)
        query2 = parse_qs(urlsplit(fetch.call_args_list[1].args[0]["url"]).query)
        self.assertEqual(query1["startDate"], ["20261003"])
        self.assertEqual(query1["textCrpCik"], ["00111722"])
        self.assertEqual(query2["currentPage"], ["2"])
        self.assertNotIn("finalReport", query1)

    def test_dart_html_truncated_range_is_explicit(self):
        with patch("weekly_trend.collectors._fetch", return_value=(dart_page(pages=4, total=350, count=100), {})):
            result = collect_source({**DART_HTML, "max_pages": 1}, NOW)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["records"]), 100)
        self.assertIn("상한", result["coverage_note"])

    def test_dart_html_wrong_issuer_moving_listing_or_wrong_page_fails(self):
        with patch("weekly_trend.collectors._fetch", return_value=(dart_page(corp_code="00999999"), {})):
            self.assertEqual(collect_source(DART_HTML, NOW)["error_code"], "issuer_mismatch")
        for second, code in ((dart_page(page=2, pages=2, total=102, first=101, count=2), "listing_changed"),
                             (dart_page(page=1, pages=2, total=101, first=101), "pagination_mismatch")):
            with patch("weekly_trend.collectors._fetch", side_effect=[(dart_page(pages=2, total=101, count=100), {}), (second, {})]):
                result = collect_source(DART_HTML, NOW)
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["records"], [])

    def test_dart_html_empty_listing_and_unexpected_page_are_distinct(self):
        with patch("weekly_trend.collectors._fetch", return_value=(dart_page(page=0, pages=0, total=0, count=0), {})):
            result = collect_source(DART_HTML, NOW)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["fetched_count"], 0)
        with patch("weekly_trend.collectors._fetch", return_value=(b"<html>access blocked</html>", {})):
            self.assertEqual(collect_source(DART_HTML, NOW)["error_code"], "unexpected_html")

    def test_dart_html_official_empty_placeholder_without_pagination(self):
        body = ('<table class="tbList"><tbody id="tbody"><tr>'
                '<td class="no_data end" colspan="6" align="center">\n 조회 결과가 없습니다. \n</td>'
                '</tr></tbody></table>').encode("utf-8")
        with patch("weekly_trend.collectors._fetch", return_value=(body, {})) as fetch:
            result = collect_source(DART_HTML, NOW)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["fetched_count"], 0)
        self.assertEqual(result["total_count"], 0)
        self.assertFalse(result["truncated"])
        fetch.assert_called_once()
        invalid = [body.replace(b'tbList', b'other'), body.replace(b'id="tbody"', b'id="other"'),
                   body.replace(b'no_data end', b'other'), body.replace(b'colspan="6"', b'colspan="1"'),
                   body.replace('조회 결과가 없습니다.'.encode(), '서비스를 이용할 수 없습니다.'.encode()),
                   '조회 결과가 없습니다.'.encode(),
                   body.replace(b'</tbody>', b'<tr><td>unexpected row</td></tr></tbody>')]
        for broken in invalid:
            with patch("weekly_trend.collectors._fetch", return_value=(broken, {})):
                result = collect_source(DART_HTML, NOW)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_code"], "unexpected_html")

    def test_dart_html_overlap_window_and_explicit_backend_window(self):
        with patch("weekly_trend.collectors._fetch", return_value=(dart_page(), {})) as fetch:
            collect_source(DART_HTML, NOW, {"last_success_at": "2026-11-01T06:17:00+09:00"})
            query = parse_qs(urlsplit(fetch.call_args.args[0]["url"]).query)
            self.assertEqual(query["startDate"], ["20261030"])
        with patch("weekly_trend.collectors._fetch", return_value=(dart_page(), {})) as fetch:
            collect_source({**DART_HTML, "collection_start": "2026-10-25T00:00:00Z", "collection_end": "2026-10-31T00:00:00Z"}, NOW)
            query = parse_qs(urlsplit(fetch.call_args.args[0]["url"]).query)
            self.assertEqual(query["startDate"], ["20261025"])
            self.assertEqual(query["endDate"], ["20261031"])

    def test_dart_html_window_uses_actual_korean_calendar_day(self):
        now = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
        with patch("weekly_trend.collectors._fetch", return_value=(dart_page(), {})) as fetch:
            result = collect_source(DART_HTML, now)
        query = parse_qs(urlsplit(fetch.call_args.args[0]["url"]).query)
        self.assertEqual(query["startDate"], ["20261004"])
        self.assertEqual(query["endDate"], ["20261103"])
        self.assertEqual(result["collection_end_date"], "20261103")

    def test_dart_html_rejects_invalid_window_and_unbounded_pagination(self):
        for change in ({"max_pages": 4}, {"initial_lookback_days": 99999}, {"overlap_hours": -1},
                       {"corp_code": "invalid"}, {"collection_start": "2027-01-01T00:00:00Z"}):
            with patch("weekly_trend.collectors._fetch") as fetch:
                self.assertEqual(collect_source({**DART_HTML, **change}, NOW)["error_code"], "invalid_config")
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

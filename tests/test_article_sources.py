from email.message import Message
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from weekly_trend import article_sources as articles
from weekly_trend.collectors import validate_url


E_URL = "https://news.einfomax.co.kr/news/articleView.html?idxno=1234567"
N_URL = "https://www.newswire.co.kr/newsRead.php?no=1031111"
E_SOURCE = {"id": "einfomax-s1n2", "enabled": True, "type": "rss", "adapter": "einfomax_rss", "allowed_hosts": ["news.einfomax.co.kr"]}
N_SOURCE = {"id": "newswire-securities", "enabled": True, "type": "rss", "adapter": "newswire_rss", "allowed_hosts": ["www.newswire.co.kr", "api.newswire.co.kr"]}
TEXT = "검증증권은 신규 투자정보 서비스를 출시한다고 밝혔다. 관련 회사와 추진 일정은 공식 원문에서 확인할 수 있다. " * 3


def page(body, *, newswire=False, canonical=None):
    content = ('<div class="release-body2 fs-md"><section class="article_column">' + body + '</section></div>'
               if newswire else '<div id="article-view-content-div" itemprop="articleBody">' + body + '</div>')
    canonical = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f'<html><head><meta property="og:title" content="검증증권 신규 서비스 출시">{canonical}</head><body><nav>메뉴</nav>{content}<footer>다른 기사</footer></body></html>'


class Response(io.BytesIO):
    def __init__(self, data, *, content_type="text/html; charset=utf-8", url=E_URL):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.status, self.url = 200, url

    def geturl(self):
        return self.url


class ArticleSourcesTests(unittest.TestCase):
    def no_dns(self):
        return patch("weekly_trend.article_sources.validate_url", side_effect=lambda url, hosts, resolve=False: validate_url(url, hosts))

    def test_einfomax_body_marker_paragraphs_remove_navigation_ads_contacts_and_rights(self):
        html = page('<p>' + TEXT + '</p><script>private instruction</script><div class="article-ad">광고</div>'
                    '<figure><figcaption>사진 설명</figcaption></figure><p>기자 이메일 reporter@example.com</p>'
                    '<p>문의 02-1234-5678</p><p>&lt;저작권자 연합인포맥스&gt;</p><p>공개 후속 사실입니다.</p>')
        result = articles.extract_article(html, E_URL, E_SOURCE)
        self.assertEqual(result["title"], "검증증권 신규 서비스 출시")
        self.assertEqual([p["id"] for p in result["paragraphs"]], ["p1", "p2"])
        text = json.dumps(result, ensure_ascii=False)
        for excluded in ("private instruction", "광고", "사진 설명", "reporter@", "02-1234", "저작권자", "메뉴", "다른 기사"):
            self.assertNotIn(excluded, text)
        self.assertFalse(result["truncated"])

    def test_newswire_br_body_and_nested_related_news_are_excluded(self):
        html = page(TEXT + '<br><br>추가 확인 대상입니다.<div class="release-contact">연락처 010-1234-5678</div>'
                    '<div class="release-source-news"><p>다른 회사 보도자료</p></div>'
                    '<div class="release-source"><h6>관련 분야</h6>증권</div>', newswire=True)
        result = articles.extract_article(html, N_URL, N_SOURCE)
        self.assertEqual(len(result["paragraphs"]), 2)
        self.assertNotIn("다른 회사", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("관련 분야", json.dumps(result, ensure_ascii=False))

    def test_url_exact_hosts_article_endpoint_numeric_id_and_no_extra_parameters(self):
        for url in ("http://news.einfomax.co.kr/news/articleView.html?idxno=1234567",
                    "https://127.0.0.1/news/articleView.html?idxno=1234567",
                    "https://news.einfomax.co.kr.evil.example/news/articleView.html?idxno=1234567",
                    "https://user@news.einfomax.co.kr/news/articleView.html?idxno=1234567",
                    "https://news.einfomax.co.kr:8443/news/articleView.html?idxno=1234567",
                    "https://news.einfomax.co.kr/login?idxno=1234567",
                    "https://news.einfomax.co.kr/news/articleView.html?idxno=x",
                    "https://news.einfomax.co.kr/news/articleView.html?idxno=1234567&idxno=1234568",
                    "https://news.einfomax.co.kr/news/articleView.html?idxno=1234567&redirect=https://private.example"):
            with self.subTest(url=url), self.assertRaises(articles.ArticleError):
                articles.article_url(url, E_SOURCE)
        self.assertEqual(articles.article_url(E_URL + "#body", E_SOURCE), E_URL)
        with self.assertRaises(articles.ArticleError):
            articles.article_url(N_URL, {**N_SOURCE, "enabled": False})
        with self.assertRaises(articles.ArticleError):
            articles.article_url("https://api.newswire.co.kr/newsRead.php?no=123", N_SOURCE)

    def test_mismatched_canonical_restricted_and_missing_bodies_fail_closed(self):
        for html, code in ((page('<p>' + TEXT + '</p>', canonical=E_URL.replace("1234567", "1234568")), "article_identity_mismatch"),
                           (page('<p>' + TEXT + '</p><div class="paywall">로그인 필요</div>'), "access_restricted"),
                           (page('<p>로그인 후 확인하세요</p>'), "body_not_found"),
                           ('<meta property="og:title" content="기사"><div>메뉴와 기사 목록만 있습니다.</div>', "body_not_found")):
            with self.subTest(code=code), self.assertRaisesRegex(articles.ArticleError, code):
                articles.extract_article(html, E_URL, E_SOURCE)

    def test_stable_content_hash_and_explicit_paragraph_text_limits(self):
        html = page(''.join('<p>' + (TEXT * 20) + str(i) + '</p>' for i in range(45)))
        a = articles.extract_article(html, E_URL, E_SOURCE)
        b = articles.extract_article(html, E_URL, E_SOURCE)
        self.assertEqual(a["content_hash"], b["content_hash"])
        self.assertTrue(a["truncated"])
        self.assertLessEqual(len(a["paragraphs"]), articles.MAX_PARAGRAPHS)
        self.assertLessEqual(sum(len(p["text"]) for p in a["paragraphs"]), articles.MAX_BODY_CHARS)
        self.assertTrue(all(len(p["text"]) <= articles.MAX_PARAGRAPH_CHARS for p in a["paragraphs"]))
        changed = articles.extract_article(page('<p>' + TEXT + '변경</p>'), E_URL, E_SOURCE)
        self.assertNotEqual(a["content_hash"], changed["content_hash"])

    def test_fetch_once_truthful_headers_and_bounded_response(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append((request, timeout))
                return Response(page('<p>' + TEXT + '</p>').encode())
        with self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()):
            result = articles.fetch_article(E_URL, E_SOURCE)
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertFalse(request.has_header("User-agent"))
        self.assertEqual(timeout, 15)
        self.assertTrue(result["paragraphs"])
        class LargeOpener:
            def open(self, request, timeout):
                return Response(b"x" * (articles.MAX_RESPONSE_BYTES + 1))
        with self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=LargeOpener()):
            with self.assertRaisesRegex(articles.ArticleError, "response_too_large"):
                articles.fetch_article(E_URL, E_SOURCE)

    def test_http_access_denial_and_rate_limit_are_codes_without_retry_or_response_leak(self):
        for status, code in ((401, "access_restricted"), (403, "access_restricted"), (429, "rate_limited"), (500, "http_error")):
            calls = []
            class Opener:
                def open(self, request, timeout):
                    calls.append(request)
                    raise HTTPError(E_URL, status, "private response", {}, io.BytesIO(b"never expose"))
            with self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()):
                with self.assertRaisesRegex(articles.ArticleError, code):
                    articles.fetch_article(E_URL, E_SOURCE)
            self.assertEqual(len(calls), 1)

    def test_redirects_only_preserve_the_same_article_identity(self):
        redirect = articles._ArticleRedirect(E_URL, E_SOURCE)
        request = Request(E_URL)
        with self.no_dns():
            same = redirect.redirect_request(request, None, 302, "redirect", {}, E_URL)
            self.assertEqual(same.full_url, E_URL)
            for url in (E_URL.replace("1234567", "1234568"), "https://127.0.0.1/", "https://evil.example/"):
                with self.subTest(url=url), self.assertRaises(articles.ArticleError):
                    redirect.redirect_request(request, None, 302, "redirect", {}, url)

    def test_non_html_response_and_unsupported_charset_are_not_parsed(self):
        for content_type, code in (("application/json", "unsupported_content_type"), ("text/html; charset=utf-16", "unsupported_charset")):
            class Opener:
                def open(self, request, timeout):
                    return Response(b"{}", content_type=content_type)
            with self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()):
                with self.assertRaisesRegex(articles.ArticleError, code):
                    articles.fetch_article(E_URL, E_SOURCE)


if __name__ == "__main__":
    unittest.main()

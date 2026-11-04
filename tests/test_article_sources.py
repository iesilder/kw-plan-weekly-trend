from email.message import Message
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from weekly_trend import article_sources as articles
from weekly_trend.collectors import CollectionError, validate_url


E_URL = "https://news.einfomax.co.kr/news/articleView.html?idxno=1234567"
N_URL = "https://www.newswire.co.kr/newsRead.php?no=1031111"
E_SOURCE = {"id": "einfomax-s1n2", "enabled": True, "type": "rss", "adapter": "einfomax_rss", "allowed_hosts": ["news.einfomax.co.kr"]}
N_SOURCE = {"id": "newswire-securities", "enabled": True, "type": "rss", "adapter": "newswire_rss", "allowed_hosts": ["www.newswire.co.kr", "api.newswire.co.kr"]}
F_URL = "https://www.fsc.go.kr/no010101/88054"
F_SOURCE = {"id": "fsc-press-releases", "enabled": True, "type": "html", "adapter": "fsc_html", "allowed_hosts": ["www.fsc.go.kr"]}
TEXT = "검증증권은 신규 투자정보 서비스를 출시한다고 밝혔다. 관련 회사와 추진 일정은 공식 원문에서 확인할 수 있다. " * 3


def page(body, *, newswire=False, canonical=None):
    content = ('<div class="release-body2 fs-md"><section class="article_column">' + body + '</section></div>'
               if newswire else '<div id="article-view-content-div" itemprop="articleBody">' + body + '</div>')
    canonical = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f'<html><head><meta property="og:title" content="검증증권 신규 서비스 출시">{canonical}</head><body><nav>메뉴</nav>{content}<footer>다른 기사</footer></body></html>'


def fsc_page(body, *, title="가상 자본시장 제도 개정안 공개", canonical=None):
    # Structure observed on FSC /no010101/88054. The article has no og:title.
    canonical = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return ('<html><head><title>보도자료 - 금융위원회</title>' + canonical + '</head><body>'
            '<nav>정부 홈페이지 메뉴</nav><div class="board-view-wrap"><div class="header">'
            '<div class="subject">' + title + '</div><div class="info">담당자 가상연락처</div></div>'
            '<div class="body"><div class="cont">' + body + '</div>'
            '<div class="file"><div class="title">첨부파일 목록</div><div class="file-list-wrap">'
            '<a href="/comm/getFile?fileNo=1">비공개로 보관할 필요 없는 첨부파일 이름.pdf</a></div></div></div>'
            '</div><footer>홈페이지 연락처 02-1234-5678</footer></body></html>')


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

    def test_fsc_url_only_accepts_official_press_detail_identity(self):
        self.assertEqual(articles.article_url(F_URL, F_SOURCE), F_URL)
        self.assertEqual(articles.article_url(F_URL + "#content", F_SOURCE), F_URL)
        for url in ("https://www.fsc.go.kr/no010101", "https://fsc.go.kr/no010101/88054",
                    "https://www.fsc.go.kr/no010102/88054", "https://www.fsc.go.kr/no010101/88054/",
                    "https://www.fsc.go.kr/no010101/0", F_URL + "?curPage=1",
                    "https://www.fsc.go.kr/comm/getFile?srvcId=BBSTY1&upperNo=88054",
                    "https://www.fsc.go.kr.evil.example/no010101/88054"):
            with self.subTest(url=url), self.assertRaises(articles.ArticleError):
                articles.article_url(url, F_SOURCE)
        for changed in ({"enabled": False}, {"type": "rss"}, {"allowed_hosts": ["fsc.go.kr"]}):
            with self.subTest(changed=changed), self.assertRaises(articles.ArticleError):
                articles.article_url(F_URL, {**F_SOURCE, **changed})

    def test_fsc_converted_document_fragments_keep_later_sections_in_order(self):
        blocks = ["표의 가상 항목 " + str(index) for index in range(60)] + ["최종 시행 예정 사항을 확인합니다."]
        packed = articles._fsc_paragraphs(blocks)
        self.assertLess(len(packed), articles.MAX_PARAGRAPHS)
        self.assertEqual(" ".join(packed), " ".join(blocks))
        long = "매우 긴 가상 문단의 사실 내용입니다. " * 200
        packed = articles._fsc_paragraphs([long.strip()])
        self.assertTrue(all(len(text) <= 1000 for text in packed))
        self.assertEqual(" ".join(packed), long.strip())

    def test_fsc_official_body_and_header_title_exclude_staff_files_and_navigation(self):
        body = ('<p>가상 자본시장 제도 개정안 공개</p><p>' + TEXT + '</p>'
                '<table><tr><td>가상기업</td><td>조치 검토</td></tr></table>'
                '<script>private instruction</script><p>담당자 가상 연락담당</p>'
                '<p>문의: 연락담당 부서</p><div class="info">가상 담당 연락 정보</div>'
                '<p><a href="/comm/getFile?fileNo=2">추가 자료.pdf</a></p>'
                '<p hidden>숨긴 자료</p><p>최종 시행 일정은 원문에서 확인하는 내용입니다.</p>')
        html = fsc_page(body).replace('</head>', '<meta property="og:title" content="잘못된 홈페이지 제목"></head>')
        result = articles.extract_article(html, F_URL, F_SOURCE)
        self.assertEqual(result["title"], "가상 자본시장 제도 개정안 공개")
        text = " ".join(p["text"] for p in result["paragraphs"])
        self.assertIn("가상기업 조치 검토", text)
        self.assertIn("최종 시행 일정", text)
        for excluded in ("담당", "첨부파일", "추가 자료.pdf", "메뉴", "private instruction", "숨긴", "02-1234"):
            self.assertNotIn(excluded, text)
        self.assertFalse(result["truncated"])

    def test_fsc_many_short_document_blocks_include_the_last_section_without_premature_truncation(self):
        blocks = ["가상 제도의 구체적인 적용 사항 " + str(index) for index in range(60)] + ["최종 후속 시행을 준비하는 가상 안내입니다."]
        result = articles.extract_article(fsc_page(''.join('<p>' + text + '</p>' for text in blocks)), F_URL, F_SOURCE)
        self.assertEqual(" ".join(row["text"] for row in result["paragraphs"]), " ".join(blocks))
        self.assertFalse(result["truncated"])
        self.assertLess(len(result["paragraphs"]), articles.MAX_PARAGRAPHS)

    def test_fsc_missing_or_ambiguous_body_title_and_attachment_only_pages_fail_closed(self):
        valid = fsc_page('<p>' + TEXT + '</p>')
        for html in (valid.replace('class="cont"', 'class="other"'),
                     valid.replace('class="subject"', 'class="other"'),
                     fsc_page(''), fsc_page('<p>' + ('가상 제목 ' * 40).strip() + '</p>', title=('가상 제목 ' * 40).strip()),
                     valid.replace('<div class="subject">', '<div class="subject">다른 제목</div><div class="subject">'),
                     valid.replace('<div class="cont">', '<div class="cont">추가 본문</div><div class="cont">')):
            with self.subTest(html=html[:100]), self.assertRaisesRegex(articles.ArticleError, "body_not_found"):
                articles.extract_article(html, F_URL, F_SOURCE)
        with self.assertRaisesRegex(articles.ArticleError, "article_identity_mismatch"):
            articles.extract_article(fsc_page('<p>' + TEXT + '</p>', canonical=F_URL.replace('88054', '88055')), F_URL, F_SOURCE)

    def test_fsc_fetch_reads_one_detail_page_without_following_attachments(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request.full_url)
                return Response(fsc_page('<p>' + TEXT + '</p>').encode(), url=F_URL)
        with self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()):
            result = articles.fetch_article(F_URL, F_SOURCE)
        self.assertEqual(calls, [F_URL])
        self.assertTrue(result["paragraphs"])

    def test_fsc_transient_network_and_server_errors_have_one_bounded_retry(self):
        for failure in (URLError(TimeoutError()), TimeoutError(),
                        HTTPError(F_URL, 503, "not logged", {}, io.BytesIO(b"not logged"))):
            calls = []
            class Opener:
                def open(self, request, timeout):
                    calls.append(request.full_url)
                    if len(calls) == 1:
                        raise failure
                    return Response(fsc_page('<p>' + TEXT + '</p>').encode(), url=F_URL)
            with self.subTest(failure=type(failure).__name__), self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()), patch("weekly_trend.article_sources.time.sleep") as sleep:
                self.assertTrue(articles.fetch_article(F_URL, F_SOURCE)["paragraphs"])
                self.assertEqual(calls, [F_URL, F_URL])
                sleep.assert_called_once_with(0.25)
        calls = []
        class TimeoutOpener:
            def open(self, request, timeout):
                calls.append(request.full_url)
                raise TimeoutError()
        with self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=TimeoutOpener()), patch("weekly_trend.article_sources.time.sleep"):
            with self.assertRaisesRegex(articles.ArticleError, "network_error"):
                articles.fetch_article(F_URL, F_SOURCE)
        self.assertEqual(calls, [F_URL, F_URL])

    def test_fsc_access_rate_limit_identity_and_parser_errors_are_not_retried(self):
        for status, code in ((401, "access_restricted"), (403, "access_restricted"), (429, "rate_limited")):
            calls = []
            class Opener:
                def open(self, request, timeout):
                    calls.append(request.full_url)
                    raise HTTPError(F_URL, status, "not logged", {}, io.BytesIO(b"not logged"))
            with self.subTest(status=status), self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()), patch("weekly_trend.article_sources.time.sleep") as sleep:
                with self.assertRaisesRegex(articles.ArticleError, code):
                    articles.fetch_article(F_URL, F_SOURCE)
                self.assertEqual(calls, [F_URL])
                sleep.assert_not_called()

    def test_fsc_response_deadline_can_retry_but_size_limit_remains_final(self):
        data = fsc_page('<p>' + TEXT + '</p>').encode()
        for code, expected_calls in (("timeout", 2), ("response_too_large", 1)):
            calls = []
            class Opener:
                def open(self, request, timeout):
                    calls.append(request.full_url)
                    return Response(data, url=F_URL)
            with self.subTest(code=code), self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=Opener()), patch("weekly_trend.article_sources._read_response", side_effect=[CollectionError(code, "not logged"), data]), patch("weekly_trend.article_sources.time.sleep"):
                if code == "timeout":
                    self.assertTrue(articles.fetch_article(F_URL, F_SOURCE)["paragraphs"])
                else:
                    with self.assertRaisesRegex(articles.ArticleError, code):
                        articles.fetch_article(F_URL, F_SOURCE)
            self.assertEqual(len(calls), expected_calls)
        for html, code in ((fsc_page(''), "body_not_found"),
                           (fsc_page('<p>' + TEXT + '</p>', canonical=F_URL.replace('88054', '88055')), "article_identity_mismatch")):
            calls = []
            class HTMLopener:
                def open(self, request, timeout):
                    calls.append(request.full_url)
                    return Response(html.encode(), url=F_URL)
            with self.subTest(code=code), self.no_dns(), patch("weekly_trend.article_sources.build_opener", return_value=HTMLopener()), patch("weekly_trend.article_sources.time.sleep") as sleep:
                with self.assertRaisesRegex(articles.ArticleError, code):
                    articles.fetch_article(F_URL, F_SOURCE)
                self.assertEqual(calls, [F_URL])
                sleep.assert_not_called()

    def test_fsc_body_size_budget_marks_truncation_and_keeps_replay_hash_stable(self):
        html = fsc_page(''.join('<p>' + TEXT + str(index) + '</p>' for index in range(100)))
        a = articles.extract_article(html, F_URL, F_SOURCE)
        b = articles.extract_article(html, F_URL, F_SOURCE)
        self.assertTrue(a["truncated"])
        self.assertEqual(a["content_hash"], b["content_hash"])
        self.assertLessEqual(sum(len(row["text"]) for row in a["paragraphs"]), articles.MAX_BODY_CHARS)
        self.assertLessEqual(len(a["paragraphs"]), articles.MAX_PARAGRAPHS)

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

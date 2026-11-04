"""Bounded public article readers for explicit and daily body analysis.

No source HTML or complete body is written to disk by this module. Missing,
restricted, or unexpected pages fail closed; authentication is never attempted.
"""

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .collectors import CollectionError, MAX_RESPONSE_BYTES, TIMEOUT_SECONDS, _read_response, validate_url

MAX_BODY_CHARS = 10_000
MAX_PARAGRAPH_CHARS = 2_000
MAX_PARAGRAPHS = 30
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
HIDDEN_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "figure", "figcaption", "iframe", "form", "button"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "blockquote", "h2", "h3", "h4", "table", "tr"}
HIDDEN_CLASS = re.compile(r"(?:^|[\s_-])(?:ad|ads|advertisement|banner|sns|share|social|copyright|contact|caption|reporter-profile)(?:$|[\s_-])", re.I)
FOOTER = re.compile(r"^(?:<\s*)?(?:저작권자|무단\s*전재|무단전재|언론\s*연락처|보도자료\s*문의|자료\s*문의|관련\s*문의|웹사이트\s*:|뉴스\s*제공\s*:|이\s*기사를\s*공유|기자\s*프로필)")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+82[- ]?|0)(?:\d{1,2})[- ]\d{3,4}[- ]\d{4}(?!\d)")


class ArticleError(Exception):
    """A small operational code; never includes response HTML or credentials."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def article_url(url, source, *, resolve=False):
    """Accept one configured publisher's HTTPS article endpoint and numeric ID."""
    adapter = source.get("adapter")
    if adapter == "fsc_html":
        if source.get("enabled") is not True or source.get("type") != "html":
            raise ArticleError("source_not_supported")
        host = "www.fsc.go.kr"
        if host not in source.get("allowed_hosts", []):
            raise ArticleError("unsafe_url")
        try:
            safe = validate_url(url, {host}, resolve=resolve)
            parts = urlsplit(safe)
            if not re.fullmatch(r"/no010101/[1-9]\d{0,11}", parts.path) or parts.query:
                raise ValueError("Unexpected press release identity")
            return urlunsplit(("https", host, parts.path, "", ""))
        except (CollectionError, ValueError):
            raise ArticleError("unsafe_url") from None
    specs = {"einfomax_rss": ("news.einfomax.co.kr", "/news/articleView.html", "idxno"),
             "newswire_rss": ("www.newswire.co.kr", "/newsRead.php", "no")}
    if adapter not in specs or source.get("enabled") is not True or source.get("type") != "rss":
        raise ArticleError("source_not_supported")
    host, path, key = specs[adapter]
    if host not in source.get("allowed_hosts", []):
        raise ArticleError("unsafe_url")
    try:
        safe = validate_url(url, {host}, resolve=resolve)
        parts = urlsplit(safe)
        query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
        if parts.path != path or set(query) != {key} or len(query[key]) != 1 or not re.fullmatch(r"[1-9]\d{0,11}", query[key][0]):
            raise ValueError("Unexpected article identity")
        return urlunsplit(("https", host, path, f"{key}={query[key][0]}", ""))
    except (CollectionError, ValueError):
        raise ArticleError("unsafe_url") from None


class _ArticleRedirect(HTTPRedirectHandler):
    max_redirections = 2
    max_repeats = 1

    def __init__(self, expected_url, source):
        self.expected_url, self.source = expected_url, source

    def redirect_request(self, request, fp, code, message, headers, new_url):
        if article_url(new_url, self.source, resolve=True) != self.expected_url:
            raise ArticleError("unexpected_redirect")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _plain(text):
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", text).split())


def _fsc_paragraphs(blocks):
    """Pack converted-document fragments without losing later body sections.

    FSC's inline press releases use short paragraphs for table cells and labels.
    Group those adjacent fragments before applying the shared body-size budget.
    A long fragment is split, not silently dropped by the per-paragraph bound.
    """
    packed, pending = [], ""
    for block in blocks:
        while len(block) > 1000:
            if pending:
                packed.append(pending)
                pending = ""
            end = block.rfind(" ", 500, 1001)
            end = 1000 if end < 0 else end
            packed.append(block[:end])
            block = block[end:].lstrip()
        if not block:
            continue
        if pending and len(pending) + len(block) + 1 > 1000:
            packed.append(pending)
            pending = ""
        pending = pending + " " + block if pending else block
    if pending:
        packed.append(pending)
    return packed


class _ArticleHTML(HTMLParser):
    def __init__(self, adapter):
        super().__init__(convert_charrefs=True)
        self.adapter = adapter
        self.stack = []
        self.parts, self.paragraphs = [], []
        self.title = ""
        self.canonicals = []
        self.body_markers = 0
        self.restricted = False
        self.title_markers = 0
        self.title_parts = []

    def active(self):
        return any(row[1] for row in self.stack) and not any(row[2] for row in self.stack)

    def flush(self):
        text = _plain("".join(self.parts))
        self.parts = []
        if self.adapter == "fsc_html" and re.match(r"^(?:담당부서|담당자|책임자|문의(?:처|사항)?\s*[:：])", text):
            return
        if text and not FOOTER.search(text) and not EMAIL.search(text) and not PHONE.search(text):
            if not self.paragraphs or text != self.paragraphs[-1]:
                self.paragraphs.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "")
        class_names = set(classes.split())
        was_active = self.active()
        news_container = self.adapter == "newswire_rss" and "release-body2" in classes.split()
        # Verified on the official press page: the header's subject is the exact
        # release title, and body > cont excludes the sibling attachment list.
        fsc_parent = (self.adapter == "fsc_html" and len(self.stack) >= 2 and
                      "board-view-wrap" in self.stack[-2][4])
        fsc_body = (fsc_parent and tag == "div" and "cont" in class_names and
                    "body" in self.stack[-1][4])
        fsc_title = (fsc_parent and tag == "div" and "subject" in class_names and
                     "header" in self.stack[-1][4])
        if fsc_title:
            self.title_markers += 1
        marker = ((self.adapter == "einfomax_rss" and attrs.get("id") == "article-view-content-div") or
                  (self.adapter == "newswire_rss" and (attrs.get("itemprop") == "articleBody" or
                   (tag == "section" and "article_column" in classes.split() and any(row[3] for row in self.stack)))) or fsc_body)
        if marker:
            self.body_markers += 1
        hidden = (tag in HIDDEN_TAGS or bool(HIDDEN_CLASS.search(classes)) or
                  bool(set(classes.split()) & {"release-source-news", "release-news-list", "release-source"}) or
                  attrs.get("aria-hidden") == "true" or "hidden" in attrs)
        if self.adapter == "fsc_html":
            hidden = hidden or bool(class_names & {"file", "file-list-wrap", "info", "board-view-nav"})
            if tag == "a" and any(row[1] for row in self.stack):
                # Inline download/viewer links are not the press body. This is
                # extraction only; the reader never follows any attachment URL.
                target = urlsplit(attrs.get("href", "")).path.lower()
                hidden = hidden or "/comm/getfile" in target or "/comm/viewer" in target or bool(re.search(r"\.(?:pdf|hwp|hwpx|docx?|xlsx?|zip)$", target))
        style = attrs.get("style", "").replace(" ", "").lower()
        hidden = hidden or "display:none" in style or "visibility:hidden" in style
        if was_active and (tag in BLOCK_TAGS or tag in {"br", "hr"} or hidden):
            self.flush()
        if was_active and re.search(r"(?:paywall|premium-lock|login-required|subscription-required)", classes, re.I):
            self.restricted = True
        if self.adapter != "fsc_html" and tag == "meta" and attrs.get("property", "").lower() == "og:title":
            self.title = _plain(attrs.get("content", ""))[:500]
        if tag == "link" and attrs.get("rel", "").lower() == "canonical" and attrs.get("href"):
            self.canonicals.append(attrs["href"])
        if tag not in VOID:
            self.stack.append((tag, marker, hidden, news_container, class_names, fsc_title))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.active() and (tag in BLOCK_TAGS or self.adapter == "fsc_html" and tag in {"td", "th"}):
            self.flush()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if self.active():
            self.parts.append(text)
        if self.adapter == "fsc_html" and any(row[5] for row in self.stack) and not any(row[2] for row in self.stack):
            self.title_parts.append(text)


def extract_article(html, url, source):
    """Extract a recognized body; return title, paragraphs, hash/time/truncation.

    Each paragraph has a deterministic sequential ``pN`` ID. Hashes include the
    bounded paragraph contents and truncation flag, never a fetch timestamp.
    """
    expected = article_url(url, source)
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ArticleError("response_too_large")
    parser = _ArticleHTML(source["adapter"])
    try:
        parser.feed(html)
        parser.close()
        parser.flush()
    except (ValueError, RecursionError):
        raise ArticleError("invalid_html") from None
    if any(article_url(canonical, source) != expected for canonical in parser.canonicals):
        raise ArticleError("article_identity_mismatch")
    if parser.restricted:
        raise ArticleError("access_restricted")
    if source["adapter"] == "fsc_html":
        if parser.title_markers != 1:
            raise ArticleError("body_not_found")
        parser.title = _plain("".join(parser.title_parts))[:500]
        # A release with only a repeated heading and attachments has no usable
        # inline body; do not generate a purported body summary from that title.
        parser.paragraphs = _fsc_paragraphs([text for text in parser.paragraphs if text != parser.title])
    if parser.body_markers != 1 or not parser.title:
        raise ArticleError("body_not_found")
    if not parser.paragraphs or sum(len(text) for text in parser.paragraphs) < 100:
        raise ArticleError("body_not_found")
    paragraphs, used, truncated = [], 0, False
    for text in parser.paragraphs:
        if used >= MAX_BODY_CHARS or len(paragraphs) >= MAX_PARAGRAPHS:
            truncated = True
            break
        bounded = text[:min(MAX_PARAGRAPH_CHARS, MAX_BODY_CHARS - used)]
        truncated = truncated or len(bounded) != len(text)
        if bounded:
            paragraphs.append({"id": f"p{len(paragraphs) + 1}", "text": bounded})
            used += len(bounded)
    fingerprint = json.dumps({"title": parser.title, "paragraphs": paragraphs, "truncated": truncated},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"title": parser.title, "paragraphs": paragraphs,
            "content_hash": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "truncated": truncated}


def fetch_article(url, source):
    """Read a bounded public article, with one transient retry for FSC only."""
    expected = article_url(url, source, resolve=True)
    request = Request(expected, headers={"Accept": "text/html", "Accept-Encoding": "identity"})
    opener = build_opener(_ArticleRedirect(expected, source))
    attempts = 2 if source.get("adapter") == "fsc_html" else 1
    for attempt in range(attempts):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                if article_url(response.geturl(), source, resolve=True) != expected:
                    raise ArticleError("unexpected_redirect")
                if response.status != 200:
                    raise ArticleError("http_error")
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise ArticleError("unsupported_content_type")
                data = _read_response(response)
                charset = response.headers.get_content_charset() or "utf-8"
                if charset.lower().replace("_", "-") not in {"utf-8", "euc-kr", "cp949"}:
                    raise ArticleError("unsupported_charset")
                try:
                    html = data.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    raise ArticleError("invalid_html") from None
                return extract_article(html, expected, source)
        except HTTPError as error:
            code = error.code
            error.close()
            if attempt + 1 < attempts and 500 <= code <= 599:
                time.sleep(0.25)
                continue
            raise ArticleError("access_restricted" if code in {401, 403} else "rate_limited" if code == 429 else "http_error") from None
        except CollectionError as error:
            if attempt + 1 < attempts and error.code == "timeout":
                time.sleep(0.25)
                continue
            raise ArticleError(error.code) from None
        except (URLError, TimeoutError, OSError):
            if attempt + 1 < attempts:
                time.sleep(0.25)
                continue
            raise ArticleError("network_error") from None

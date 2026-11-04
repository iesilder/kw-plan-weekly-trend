"""Bounded, metadata-only readers for explicitly approved public sources.

Feed descriptions and article bodies are deliberately never returned. A successful
fetch describes this request only; a short RSS feed does not guarantee full coverage.
"""

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import ipaddress
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies, proxy_bypass
import xml.etree.ElementTree as ET


TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TITLE_CHARS = 500
MAX_ITEMS = 2000


class CollectionError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_title(value):
    parser = _PlainText()
    parser.feed(str(value or "")[:10000])
    text = unescape(" ".join(parser.parts))
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", text)
    return " ".join(text.split())[:MAX_TITLE_CHARS]


def _host_allowed(host, allowed_hosts):
    if not host or host.endswith(".") or host.lower() not in allowed_hosts:
        return False
    try:
        address = ipaddress.ip_address(host)
        return address.is_global and not address.is_multicast and not address.is_reserved
    except ValueError:
        return "." in host and not host.endswith((".local", ".localhost", ".internal", ".test"))


def validate_url(url, allowed_hosts, *, resolve=False):
    """Validate the exact configured host; subdomains are not implicitly allowed."""
    if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise CollectionError("unsafe_url", "Source or article URL is not permitted")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or not _host_allowed(parts.hostname, allowed_hosts)):
            raise ValueError("Unapproved URL")
    except ValueError:
        raise CollectionError("unsafe_url", "Source or article URL is not permitted") from None
    # urllib sends HTTPS through an explicitly configured CONNECT proxy when
    # applicable; that proxy resolves the destination. Do not require a second,
    # local DNS route which may intentionally be unavailable in that environment.
    # Literal IP and exact-host restrictions above apply in either case.
    uses_proxy = bool(getproxies().get("https")) and not proxy_bypass(parts.hostname)
    if resolve and not uses_proxy:
        try:
            addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise CollectionError("dns_error", "Source hostname could not be resolved") from None
        resolved = [ipaddress.ip_address(address[4][0]) for address in addresses]
        if not resolved or any(not address.is_global or address.is_multicast or address.is_reserved for address in resolved):
            raise CollectionError("unsafe_address", "Source resolved to a non-public address")
    return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", parts.query, ""))


class _SafeRedirect(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe = validate_url(newurl, self.hosts, resolve=True)
        redirected = super().redirect_request(req, fp, code, msg, headers, safe)
        # A validator belongs to the requested resource, not to another host.
        if redirected is not None and urlsplit(req.full_url).hostname != urlsplit(safe).hostname:
            redirected.remove_header("If-none-match")
            redirected.remove_header("If-modified-since")
        return redirected


def _header(value):
    return value if isinstance(value, str) and len(value) <= 512 and not re.search(r"[\x00-\x1f\x7f]", value) else None


def _read_response(response):
    encoding = (response.headers.get("Content-Encoding") or "identity").lower()
    if encoding != "identity":
        raise CollectionError("unsupported_encoding", "Unexpected compressed response")
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > MAX_RESPONSE_BYTES:
                raise CollectionError("response_too_large", "Source response exceeds the size limit")
        except ValueError:
            raise CollectionError("invalid_response", "Invalid response length") from None
    chunks, size, started = [], 0, time.monotonic()
    while True:
        if time.monotonic() - started > TIMEOUT_SECONDS:
            raise CollectionError("timeout", "Source response exceeded its time limit")
        chunk = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise CollectionError("response_too_large", "Source response exceeds the size limit")
        chunks.append(chunk)


def _fetch(source, previous):
    hosts = {str(host).lower() for host in source["allowed_hosts"]}
    url = validate_url(source["url"], hosts, resolve=True)
    # Keep urllib's truthful, versioned default client identifier. The official
    # DART list accepts this standard request but returned HTTP 503 for our
    # former custom User-Agent during live compatibility checks.
    headers = {"Accept-Encoding": "identity",
               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html"}
    for key, name in (("etag", "If-None-Match"), ("last_modified", "If-Modified-Since")):
        value = _header(previous.get(key))
        if value:
            headers[name] = value
    opener = build_opener(_SafeRedirect(hosts))
    for attempt in range(2):
        try:
            with opener.open(Request(url, headers=headers), timeout=TIMEOUT_SECONDS) as response:
                validate_url(response.geturl(), hosts, resolve=True)
                status = response.status
                if status not in (200, 304):
                    raise CollectionError("http_error", "Source returned an unexpected HTTP status")
                validators = {"etag": _header(response.headers.get("ETag")),
                              "last_modified": _header(response.headers.get("Last-Modified"))}
                if status == 304:
                    return None, {key: value or previous.get(key) for key, value in validators.items()}
                return _read_response(response), validators
        except HTTPError as error:
            if error.code == 304:
                validators = {"etag": _header(error.headers.get("ETag")) or _header(previous.get("etag")),
                              "last_modified": _header(error.headers.get("Last-Modified")) or _header(previous.get("last_modified"))}
                error.close()
                return None, validators
            retryable = error.code == 429 or 500 <= error.code <= 599
            error.close()
            if attempt == 0 and retryable:
                time.sleep(0.25)
                continue
            raise CollectionError("http_error", "Source request failed with an HTTP error") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 0:
                time.sleep(0.25)
                continue
            raise CollectionError("network_error", "Source could not be reached within the request limits") from None


def _date(value):
    if not value or len(value) > 100:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        return None


def _date_without_timezone(value):
    """Retain an explicitly supplied calendar date without inventing its offset."""
    if not value or len(value) > 100:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return None
    return parsed.date().isoformat()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(element, names):
    for child in element:
        if _local(child.tag) in names:
            return "".join(child.itertext()).strip()
    return ""


def _article_url(value, source):
    if not value:
        raise CollectionError("unsafe_url", "Missing article URL")
    url = urljoin(source["url"], value.strip())
    if source.get("adapter") in {"dart_rss", "dart_html"}:
        parts = urlsplit(url)
        identifiers = parse_qs(parts.query).get("rcpNo", [])
        if (parts.scheme not in {"http", "https"} or parts.hostname != "dart.fss.or.kr"
                or parts.username is not None or parts.password is not None
                or parts.path not in {"/api/link.jsp", "/dsaf001/main.do"}
                or len(identifiers) != 1 or not re.fullmatch(r"\d{14}", identifiers[0])):
            raise CollectionError("unsafe_url", "Invalid DART disclosure link")
        url = "https://dart.fss.or.kr/dsaf001/main.do?" + urlencode({"rcpNo": identifiers[0]})
    elif source.get("adapter") == "fsc_html":
        parts = urlsplit(url)
        if parts.hostname != "www.fsc.go.kr" or not re.fullmatch(r"/no010101/\d+", parts.path):
            raise CollectionError("unsafe_url", "Invalid FSC article link")
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    elif source.get("adapter") == "newswire_rss":
        safe = validate_url(url, {str(host).lower() for host in source["allowed_hosts"]})
        parts = urlsplit(safe)
        identifiers = parse_qs(parts.query, keep_blank_values=True).get("no", [])
        if (parts.hostname != "www.newswire.co.kr" or parts.path != "/newsRead.php"
                or len(identifiers) != 1 or not re.fullmatch(r"[0-9]+", identifiers[0])):
            raise CollectionError("unsafe_url", "Invalid Newswire article link")
        # The publisher's numeric article ID identifies this resource; the RSS
        # sourceType parameter only describes how a reader reached it.
        url = "https://www.newswire.co.kr/newsRead.php?" + urlencode({"no": identifiers[0]})
    return validate_url(url, {str(host).lower() for host in source["allowed_hosts"]})


def _record(source, title, link, published="", updated="", published_date=None):
    title = plain_title(title)
    if not title:
        return None
    try:
        uri = _article_url(link, source)
    except (CollectionError, ValueError):
        return None
    published_at = _date(published)
    if not published_date and published_at is None:
        published_date = _date_without_timezone(published)
    record = {"uri": uri, "title": title, "published_at": published_at,
              "source_updated_at": _date(updated), "source_id": source["id"],
              "source_name": source["name"], "content_text": "", "content_level": "metadata_only",
              "retention_basis": source["retention_basis"], "retention_url": source["retention_url"]}
    if published_date:
        record["published_date"] = published_date
    for field in ("company_id", "company", "scope"):
        if field in source:
            record[field] = source[field]
    return record


def _parse_feed(body, source):
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", body.replace(b"\x00", b""), re.I):
        raise CollectionError("unsafe_xml", "DTD and entity declarations are not allowed")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        raise CollectionError("invalid_xml", "Source did not return a valid feed") from None
    if _local(root.tag) not in {"rss", "RDF", "feed"}:
        raise CollectionError("invalid_feed", "Response is not an RSS or Atom feed")
    if _local(root.tag) == "rss":
        channels = [element for element in root if _local(element.tag) == "channel"]
        if len(channels) != 1:
            raise CollectionError("invalid_feed", "RSS channel is missing or ambiguous")
        entries = [element for element in channels[0] if _local(element.tag) == "item"]
    else:
        entry_name = "entry" if _local(root.tag) == "feed" else "item"
        entries = [element for element in root if _local(element.tag) == entry_name]
    if len(entries) > MAX_ITEMS:
        raise CollectionError("too_many_items", "Feed exceeds the item limit")
    records = []
    for entry in entries:
        link = ""
        if _local(entry.tag) == "entry":
            for child in entry:
                if _local(child.tag) == "link" and child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                    break
        else:
            link = _child_text(entry, {"link"})
        item = _record(source, _child_text(entry, {"title"}), link,
                       _child_text(entry, {"pubDate", "published", "date"}),
                       _child_text(entry, {"updated", "modified"}))
        if item:
            records.append(item)
    if entries and not records:
        raise CollectionError("no_valid_items", "Feed contained no valid metadata items")
    return records, len(entries)


class _FSCList(HTMLParser):
    """Only read subject links and dates inside the official list container."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.items = [], []
        self.current = None
        self.has_list = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if "board-list-wrap" in classes:
            self.has_list = True
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, classes))
        inside = any("board-list-wrap" in classes for _, classes in self.stack)
        if tag == "li" and inside and self.current is None:
            self.current = {"title": "", "href": "", "day": "", "title_parts": []}
        if self.current is not None and tag == "a" and any("subject" in classes for _, classes in self.stack):
            self.current["href"] = attrs.get("href", "")
            self.current["title"] = attrs.get("title", "")

    def handle_data(self, data):
        if self.current is not None:
            if any("day" in classes for _, classes in self.stack):
                self.current["day"] += data
            if any("subject" in classes for _, classes in self.stack):
                self.current["title_parts"].append(data)

    def handle_endtag(self, tag):
        if tag == "li" and self.current is not None:
            self.items.append(self.current)
            self.current = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _parse_fsc(body, source):
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("invalid_encoding", "FSC response was not valid UTF-8") from None
    parser = _FSCList()
    parser.feed(text)
    # The official list exposes its result count and current/last page. Check
    # them rather than treating a blocked or redesigned page as an empty feed.
    text_parser = _PlainText()
    text_parser.feed(text)
    page_text = " ".join(" ".join(text_parser.parts).split())
    page_match = re.search(r"전체\s*([\d,]+)\s*건\s*페이지\s*(\d+)\s*/\s*(\d+)", page_text)
    if not parser.has_list or not page_match:
        raise CollectionError("unexpected_html", "Expected FSC listing pagination was not found")
    total, page, pages = (int(value.replace(",", "")) for value in page_match.groups())
    if total == 0:
        if any(entry["href"] for entry in parser.items):
            raise CollectionError("unexpected_html", "FSC empty listing included article links")
        return [], page, pages, total
    if not parser.items or page < 1 or pages < 1 or page > pages:
        raise CollectionError("unexpected_html", "Expected FSC listing was not found")
    records = []
    for entry in parser.items:
        day = entry["day"].strip()
        try:
            published_date = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise CollectionError("invalid_item", "FSC listing included an invalid publication date") from None
        item = _record(source, entry["title"] or " ".join(entry["title_parts"]), entry["href"],
                       published_date=published_date)
        if item is None:
            raise CollectionError("invalid_item", "FSC listing included invalid article metadata")
        records.append(item)
    return records, page, pages, total


class _DARTList(HTMLParser):
    """Read only statutory report labels, issuer IDs and receipt dates."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.has_list_body = False
        self.in_body = False
        self.in_info = False
        self.info_depth = 0
        self.page_info = []
        self.row = None
        self.cell = -1
        self.items = []
        self.row_count = 0
        self.empty_rows = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.tables.append("tbList" in attrs.get("class", "").split())
        if tag == "tbody" and attrs.get("id") == "tbody" and self.tables and self.tables[-1]:
            self.in_body = True
            self.has_list_body = True
        if tag == "div":
            if "pageInfo" in attrs.get("class", "").split():
                self.in_info = True
                self.info_depth = 1
            elif self.in_info:
                self.info_depth += 1
        if self.in_body and tag == "tr":
            self.row = {"title": [], "day": [], "href": "", "corp_code": "",
                        "empty_marker": False, "empty_parts": [], "reading_empty": False}
            self.cell = -1
            self.row_count += 1
        if self.row is not None:
            if tag == "td":
                self.cell += 1
                is_empty_cell = (self.cell == 0 and attrs.get("colspan") == "6"
                                 and "no_data" in attrs.get("class", "").split())
                self.row["reading_empty"] = is_empty_cell
                if is_empty_cell:
                    self.row["empty_marker"] = True
            elif tag == "a" and self.cell == 1:
                match = re.search(r"openCorpInfoNew\(['\"](\d{8})['\"]", attrs.get("onclick", ""))
                if match:
                    self.row["corp_code"] = match.group(1)
            elif tag == "a" and self.cell == 2 and attrs.get("href", "").startswith("/dsaf001/main.do?"):
                self.row["href"] = attrs["href"]

    def handle_data(self, data):
        if self.in_info:
            self.page_info.append(data)
        if self.row is not None:
            if self.row["reading_empty"]:
                self.row["empty_parts"].append(data)
            if self.cell == 2:
                self.row["title"].append(data)
            elif self.cell == 4:
                self.row["day"].append(data)

    def handle_endtag(self, tag):
        if tag == "div" and self.in_info:
            self.info_depth -= 1
            if self.info_depth == 0:
                self.in_info = False
        if tag == "tr" and self.row is not None:
            if self.row["href"]:
                self.items.append(self.row)
            elif (self.cell == 0 and self.row["empty_marker"]
                  and " ".join("".join(self.row["empty_parts"]).split()) == "조회 결과가 없습니다."):
                self.empty_rows += 1
            self.row = None
        if tag == "td" and self.row is not None:
            self.row["reading_empty"] = False
        if tag == "tbody":
            self.in_body = False
        if tag == "table" and self.tables:
            self.tables.pop()


def _parse_dart_list(body, source):
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("invalid_encoding", "DART listing was not valid UTF-8") from None
    parser = _DARTList()
    parser.feed(text)
    page_info = "".join(parser.page_info)
    match = re.search(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]\s*\[\s*총\s*([\d,]+)\s*건\s*\]", page_info)
    # DART's genuine zero-result response omits pagination and uses this exact
    # placeholder inside its official result table. Generic empty or blocked
    # HTML must not silently become a successful zero-item collection.
    known_empty = (parser.has_list_body and parser.row_count == 1
                   and parser.empty_rows == 1 and not parser.items)
    if not page_info.strip() and known_empty:
        return [], 0, 0, 0
    if not match:
        raise CollectionError("unexpected_html", "Expected DART listing pagination was not found")
    page, pages, total = int(match[1]), int(match[2]), int(match[3].replace(",", ""))
    if not parser.has_list_body or (total == 0 and (parser.items or parser.row_count > parser.empty_rows)):
        raise CollectionError("unexpected_html", "Expected DART result table was not found")
    if total > 0 and (not parser.items or pages < 1 or page < 1):
        raise CollectionError("unexpected_html", "DART listing metadata was incomplete")
    records = []
    for item in parser.items:
        if item["corp_code"] != source["corp_code"]:
            raise CollectionError("issuer_mismatch", "DART listing returned an unexpected issuer")
        try:
            day = datetime.strptime("".join(item["day"]).strip(), "%Y.%m.%d").date().isoformat()
        except ValueError:
            day = None
        record = _record(source, " ".join(item["title"]), item["href"], published_date=day)
        if record is None:
            raise CollectionError("invalid_item", "DART listing included invalid report metadata")
        records.append(record)
    return records, page, pages, total


def _dart_window(source, now, previous):
    kst = timezone(timedelta(hours=9))
    try:
        lookback = int(source.get("initial_lookback_days", 30))
        overlap = int(source.get("overlap_hours", 48))
        if not 1 <= lookback <= 365 or not 0 <= overlap <= 168:
            raise ValueError("Invalid window")
        end = now
        if source.get("collection_end"):
            end_text = _date(source["collection_end"])
            if not end_text:
                raise ValueError("Invalid end")
            end = datetime.fromisoformat(end_text)
        start = end - timedelta(days=lookback)
        if previous.get("last_success_at"):
            last = _date(previous["last_success_at"])
            if last:
                start = datetime.fromisoformat(last) - timedelta(hours=overlap)
        if source.get("collection_start"):
            start_text = _date(source["collection_start"])
            if not start_text:
                raise ValueError("Invalid start")
            start = datetime.fromisoformat(start_text)
        if start > end:
            raise ValueError("Invalid order")
        return start.astimezone(kst).strftime("%Y%m%d"), end.astimezone(kst).strftime("%Y%m%d")
    except (TypeError, ValueError, OverflowError):
        raise CollectionError("invalid_config", "DART collection window is invalid") from None


def _collect_dart_html(source, now, previous):
    if (not re.fullmatch(r"\d{8}", str(source.get("corp_code", "")))
            or source["url"] != "https://dart.fss.or.kr/dsab001/search.ax"):
        raise CollectionError("invalid_config", "DART issuer or listing endpoint is invalid")
    start, end = _dart_window(source, now, previous)
    try:
        max_pages = int(source.get("max_pages", 3))
        if not 1 <= max_pages <= 3:
            raise ValueError("Page limit")
    except (TypeError, ValueError):
        raise CollectionError("invalid_config", "DART page limit must be between one and three") from None
    records, seen, fetched, total, pages, previous_total = [], set(), 0, 0, 0, None
    for current in range(1, max_pages + 1):
        query = {"textCrpCik": source["corp_code"], "textCrpNm": source.get("corp_name", source.get("company", "")),
                 "startDate": start, "endDate": end, "currentPage": current,
                 "maxResults": 100, "maxLinks": 10}
        request_source = {**source, "url": source["url"] + "?" + urlencode(query)}
        body, _ = _fetch(request_source, {})
        if body is None:
            raise CollectionError("invalid_response", "DART listing unexpectedly returned no representation")
        items, returned_page, pages, total = _parse_dart_list(body, source)
        if total and returned_page != current:
            raise CollectionError("pagination_mismatch", "DART returned an unexpected page")
        # A moving listing must be retried later, never declared fully collected.
        if previous_total is not None and previous_total != total:
            raise CollectionError("listing_changed", "DART listing changed while paging")
        previous_total = total
        fetched += len(items)
        for item in items:
            if item["uri"] in seen:
                raise CollectionError("listing_changed", "DART listing repeated a report while paging")
            seen.add(item["uri"])
            records.append(item)
        if current >= pages or total == 0:
            if len(records) != total:
                raise CollectionError("incomplete_listing", "DART listing count did not match its metadata")
            break
        if current < max_pages:
            time.sleep(0.5)
    truncated = pages > max_pages
    note = ("페이지 상한으로 조회 기간의 일부만 수집했습니다. 수집 성공 기준시각을 전진시키지 않습니다."
            if truncated else "지정한 접수일 범위의 DART 공시 목록을 수집했습니다. 기사·공시 본문은 수집하지 않았습니다.")
    return {"records": records, "fetched_count": fetched, "etag": None, "last_modified": None,
            "truncated": truncated, "coverage_note": note, "total_count": total,
            "collection_start_date": start, "collection_end_date": end}


def _collect_fsc_html(source, now, previous):
    """Read the official date-filtered press list without article/attachment GETs."""
    if source["url"] != "https://www.fsc.go.kr/no010101":
        raise CollectionError("invalid_config", "FSC listing endpoint is invalid")
    start, end = _dart_window(source, now, previous)
    start_day = datetime.strptime(start, "%Y%m%d").strftime("%Y-%m-%d")
    end_day = datetime.strptime(end, "%Y%m%d").strftime("%Y-%m-%d")
    try:
        max_pages = int(source.get("max_pages", 10))
        if not 1 <= max_pages <= 10:
            raise ValueError("Page limit")
    except (TypeError, ValueError):
        raise CollectionError("invalid_config", "FSC page limit must be between one and ten") from None
    records, seen, previous_total, pages, total = [], set(), None, 0, 0
    for current in range(1, max_pages + 1):
        query = {"curPage": current, "srchBeginDt": start_day, "srchEndDt": end_day,
                 "srchCtgry": "", "srchKey": "sj", "srchText": ""}
        body, _ = _fetch({**source, "url": source["url"] + "?" + urlencode(query)}, {})
        if body is None:
            raise CollectionError("invalid_response", "FSC listing unexpectedly returned no representation")
        items, page, pages, total = _parse_fsc(body, source)
        if total and page != current:
            raise CollectionError("pagination_mismatch", "FSC returned an unexpected page")
        if previous_total is not None and total != previous_total:
            raise CollectionError("listing_changed", "FSC listing changed while paging")
        previous_total = total
        for item in items:
            if not start_day <= item["published_date"] <= end_day:
                raise CollectionError("window_mismatch", "FSC returned an item outside the requested date range")
            if item["uri"] in seen:
                raise CollectionError("listing_changed", "FSC listing repeated an article while paging")
            seen.add(item["uri"])
            records.append(item)
        if total == 0 or current >= pages:
            if len(records) != total:
                raise CollectionError("incomplete_listing", "FSC listing count did not match its metadata")
            break
        if current < max_pages:
            time.sleep(0.5)
    truncated = pages > max_pages
    return {"records": records, "fetched_count": len(records), "etag": None, "last_modified": None,
            "truncated": truncated, "total_count": total,
            "collection_start_date": start, "collection_end_date": end,
            "coverage_note": ("금융위원회 보도자료 목록 페이지 상한에 도달했습니다. 다음 수집 기준시각을 전진시키지 않습니다."
                              if truncated else "금융위원회 공식 보도자료 목록의 제목·발행일·원문 링크를 저장했습니다. 본문·첨부는 요청하지 않았습니다.")}


def collect_source(source, now, previous_state=None):
    """Fetch one approved source; errors return a safe failure result without bodies."""
    previous = previous_state or {}
    result = {"status": "failed", "records": [], "fetched_count": 0,
              "etag": _header(previous.get("etag")), "last_modified": _header(previous.get("last_modified")),
              "error_code": None, "error_message": None}
    try:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise CollectionError("invalid_clock", "Collection time must include a timezone")
        if (source.get("enabled") is not True or source.get("retention") != "metadata_only"
                or source.get("policy_status") != "verified" or source.get("rss_prohibited") is True):
            raise CollectionError("source_not_approved", "Source is not enabled for approved metadata collection")
        if (not all(isinstance(source.get(field), str) and source[field] for field in
                    ("id", "name", "url", "retention_basis", "retention_url"))
                or not isinstance(source.get("allowed_hosts"), list) or not source["allowed_hosts"]):
            raise CollectionError("invalid_config", "Source configuration is incomplete")
        if not (source.get("type") == "rss" or source.get("type") == "dart_html" or
                (source.get("type") == "html" and source.get("adapter") == "fsc_html")):
            raise CollectionError("unsupported_source", "Source type is not supported")
        if source.get("type") == "dart_html":
            if source.get("adapter") != "dart_html":
                raise CollectionError("invalid_config", "DART listing adapter is required")
            return {**result, **_collect_dart_html(source, now, previous), "status": "success"}
        if source.get("adapter") == "fsc_html":
            if source.get("type") != "html":
                raise CollectionError("invalid_config", "FSC listing adapter requires HTML type")
            return {**result, **_collect_fsc_html(source, now, previous), "status": "success"}
        body, validators = _fetch(source, previous)
        if body is None:
            return {**result, **validators, "status": "not_modified"}
        records, count = _parse_feed(body, source)
        return {**result, **validators, "status": "success", "records": records, "fetched_count": count}
    except CollectionError as error:
        return {**result, "error_code": error.code, "error_message": error.message}


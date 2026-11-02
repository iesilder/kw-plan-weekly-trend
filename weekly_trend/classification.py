"""Conservative, title-only routing. This module does not perform AI analysis."""

import html
import ipaddress
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

TOPIC_KEYWORDS = {
    "performance": ("실적", "순이익", "영업이익", "매출", "배당", "자사주", "자기주식", "주식소각", "주주환원", "주주정책", "증자", "감자", "자본", "기업가치", "밸류업", "사업보고서", "분기보고서", "반기보고서"),
    "digital": ("인공지능", "디지털", "신사업", "생성형", "핀테크", "토큰증권", "블록체인", "로보어드바이저", "마이데이터", "ai", "sto"),
    "expansion": ("제휴", "인수", "합병", "해외", "협약", "파트너십", "글로벌", "현지법인", "주식의취득", "출자증권취득", "m&a"),
    "strategy": ("조직", "경영전략", "전략", "대표이사", "최고경영자", "사장", "임원", "인사", "사업재편", "경영계획", "ceo"),
    "risk": ("규제", "위험", "리스크", "운영", "제재", "과징금", "징계", "내부통제", "불완전판매", "전산장애", "접속장애", "시스템장애", "보안", "정보유출", "해킹", "검사", "사고", "소송", "준법", "금융사고", "계열금융회사의약관에의한금융거래"),
}
EXCLUDED_PRODUCT_FORMS = ("투자설명서", "일괄신고추가서류", "증권발행실적보고서", "파생결합증권", "파생결합사채")
OWNERSHIP_FORMS = ("임원주요주주특정증권등소유상황보고서", "주식등의대량보유상황보고서")


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_title(value):
    if not isinstance(value, str) or len(value) > 10000:
        raise ValueError("Invalid title")
    parser = _TextParser()
    parser.feed(html.unescape(value))
    text = " ".join(" ".join(parser.parts).split())
    text = "".join(character for character in text if ord(character) >= 32)
    if not text or len(text) > 1000:
        raise ValueError("Invalid title")
    return text


def safe_https_url(value, allowed_domains=None):
    if not isinstance(value, str) or not value or len(value) > 4000:
        raise ValueError("Invalid HTTPS URL")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("Unsafe HTTPS URL")
    parts = urlsplit(value)
    try:
        host, port = parts.hostname, parts.port
    except ValueError as error:
        raise ValueError("Invalid HTTPS URL") from error
    if parts.scheme.lower() != "https" or not host or parts.username is not None or parts.password is not None:
        raise ValueError("Only public HTTPS URLs without credentials are supported")
    if port not in (None, 443) or "." not in host or host.lower().endswith((".", ".local", ".localhost", ".internal")):
        raise ValueError("Unsafe HTTPS host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        raise ValueError("IP addresses are not accepted as article sources")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or ".." in host:
        raise ValueError("Invalid HTTPS host")
    if allowed_domains and host.lower() not in {domain.lower() for domain in allowed_domains}:
        raise ValueError("Article host outside source allowlist")
    return value


def _matches(title, keyword):
    if keyword.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", title.casefold()) is not None
    return keyword.casefold() in title.casefold()


def classify(title, companies, topics):
    """Return routing hints only when a configured company and topic occur in title."""
    if any(form in title.replace(" ", "") for form in EXCLUDED_PRODUCT_FORMS):
        return None
    matches = [company for company in companies if company.get("enabled", True) and
               any(_matches(title.replace(" ", ""), name.replace(" ", "")) for name in
                   [company["name"], *company.get("aliases", [])])]
    if not matches:
        return None
    topic_hits = {topic: [word for word in words if _matches(title, word)]
                  for topic, words in TOPIC_KEYWORDS.items() if topic in topics}
    topic_hits = {topic: words for topic, words in topic_hits.items() if words}
    compact_title = re.sub(r"[^가-힣a-zA-Z0-9]", "", title)
    ownership_form = any(form in compact_title for form in OWNERSHIP_FORMS)
    if ownership_form:
        # These statutory labels describe holdings, not an executive appointment.
        # Only the visible form name is used; no disclosure body is interpreted.
        topic_hits["performance"] = [word for word in ("주요주주", "소유상황", "대량보유") if word in compact_title]
    if not topic_hits:
        return None
    # The tie-break is a stable display routing rule, not an importance judgment.
    topic_id = "performance" if ownership_form else max(topic_hits, key=lambda topic: len(topic_hits[topic]))
    return {"company": " · ".join(item["name"] for item in matches),
            "company_ids": [item["id"] for item in matches],
            "companies": [item["name"] for item in matches],
            "topic_id": topic_id, "topic": topics[topic_id],
            "matched_keywords": topic_hits[topic_id], "matched_topic_ids": list(topic_hits)}

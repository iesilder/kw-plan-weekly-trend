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
DART_POLICY_VERSION = "dart-earnings-v1"


def source_policy(source):
    """DART has a narrower policy than news even if a config field is omitted."""
    is_dart = source.get("adapter") in {"dart_html", "dart_rss"} or source.get("type") == "dart_html"
    if is_dart:
        content_filter = source.get("content_filter", "earnings_only")
        if content_filter != "earnings_only":
            raise ValueError("DART sources must use earnings_only")
        version = source.get("content_policy_version", DART_POLICY_VERSION)
    else:
        content_filter = source.get("content_filter", "topics")
        if content_filter != "topics":
            raise ValueError("News sources must use the five-topic filter")
        version = source.get("content_policy_version", "news-topics-v1")
    if not isinstance(version, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", version):
        raise ValueError("Invalid content policy version")
    return content_filter, version


def earnings_form(title):
    """Recognize complete DART form labels, not a keyword somewhere in a title."""
    compact = re.sub(r"\s+", "", title)
    compact = re.sub(r"^(?:\[(?:기재정정|첨부정정|정정|첨부추가)\])+", "", compact)
    if re.fullmatch(r"(?:연결재무제표기준|별도재무제표기준|개별재무제표기준|연결|별도)?영업(?:\(잠정\))?실적(?:\(공정공시\))?", compact):
        return "영업(잠정)실적" if "(잠정)" in compact else "영업실적"
    periodic = re.fullmatch(r"(사업|반기|분기)보고서(?:\(\d{4}\.\d{2}(?:\.\d{2})?\))?", compact)
    if periodic:
        return periodic[1] + "보고서"
    audit = re.fullmatch(r"(연결)?감사보고서(?:제출)?(?:\(\d{4}\.\d{2}(?:\.\d{2})?\))?", compact)
    if audit:
        return (audit[1] or "") + "감사보고서"
    if re.fullmatch(r"매출액또는손익구조(?:(?:30%\(대규모법인은15%\)|30%|15%)이상)?변경(?:\(자율공시\))?", compact):
        return "매출액또는손익구조변경"
    return None


def classify_for_source(title, source, companies, topics):
    """Apply a source's allowed form policy before the general news topic rules."""
    content_filter, _ = source_policy(source)
    if content_filter == "earnings_only":
        form = earnings_form(title)
        if not form or source.get("scope") != "company":
            return None
        company = next((item for item in companies if item["id"] == source.get("company_id") and item.get("enabled", True)), None)
        if company is None:
            return None
        return {"company": company["name"], "company_ids": [company["id"]], "companies": [company["name"]],
                "topic_id": "performance", "topic": topics["performance"],
                "matched_keywords": [form], "matched_topic_ids": ["performance"]}
    if source.get("scope") == "company":
        company = next(item for item in companies if item["id"] == source["company_id"])
        return classify(company["name"] + " " + title, [company], topics)
    route = classify(title, companies, topics)
    if route is None and source.get("scope") == "industry" and any(word in title for word in ("증권", "금융투자", "자본시장", "증선위", "금융회사")):
        route = classify("증권업권공통 " + title, [{"id": "industry", "name": "증권업권공통"}], topics)
        if route:
            route.update(company="증권업권 공통", company_ids=[], companies=[])
    return route


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
    compact_title = re.sub(r"\s+", "", title)
    customer_dividend = re.search(r"(?:배당.{0,20}(?:고객|가입자|투자자)|(?:고객|가입자|투자자).{0,20}배당)", compact_title) is not None
    def relevant_keyword(word):
        if customer_dividend and word in {"배당", "실적"}:
            return False
        if word == "인수":
            # Underwriting authorization is not a corporate acquisition.
            return _matches(title.replace("인수업", ""), word)
        return _matches(title, word)
    topic_hits = {topic: [word for word in words if relevant_keyword(word)]
                  for topic, words in TOPIC_KEYWORDS.items() if topic in topics}
    topic_hits = {topic: words for topic, words in topic_hits.items() if words}
    ib_business = any(word in compact_title.casefold() for word in ("ib사업", "투자은행사업"))
    new_ib_business = ib_business and (any(word in compact_title for word in ("신규", "본격화", "진출")) or
                                      ("첫" in compact_title and "설립" in compact_title))
    if new_ib_business:
        business_word = "IB사업" if "ib사업" in compact_title.casefold() else "투자은행사업"
        topic_hits["digital"] = [business_word, *[word for word in ("신규", "본격화", "진출", "첫", "설립") if word in compact_title]]
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

"""Conservative, title-only routing. This module does not perform AI analysis."""

import html
import ipaddress
import json
from pathlib import Path
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

TOPIC_IDS = {"performance", "digital", "expansion", "strategy", "risk"}
RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "classification_rules.json"


def validate_classification_rules(rules):
    """Validate the editable lists; no expression language or executable rules."""
    if not isinstance(rules, dict) or rules.get("schema_version") != "1.0":
        raise ValueError("Unsupported classification rule schema")
    if not isinstance(rules.get("version"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", rules["version"]):
        raise ValueError("Invalid classification rule version")
    if set(rules.get("topic_keywords", {})) != TOPIC_IDS:
        raise ValueError("Classification rules must retain exactly the five topic IDs")
    def words(values):
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in values):
            raise ValueError("Classification keywords must be lists of short, nonempty strings")
    for section in ("topic_keywords", "tag_keywords", "signals", "exclusions"):
        if not isinstance(rules.get(section), dict):
            raise ValueError("Missing classification rule section: " + section)
        for name, values in rules[section].items():
            if not isinstance(name, str) or not name:
                raise ValueError("Rule labels must be nonempty strings")
            words(values)
    stages = rules.get("event_stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("Event stages are required")
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str) or not stage["name"]:
            raise ValueError("Invalid event stage")
        words(stage.get("keywords"))
    scope = rules.get("subject_scope")
    if not isinstance(scope, dict):
        raise ValueError("Subject scope is required")
    for key in ("related_groups", "authorities", "market_infrastructure"):
        if not isinstance(scope.get(key), list):
            raise ValueError("Missing subject list: " + key)
        for subject in scope[key]:
            if not isinstance(subject, dict) or not isinstance(subject.get("name"), str) or not subject["name"]:
                raise ValueError("Invalid subject entry")
            words(subject.get("aliases"))
    for key in ("group_context_terms", "sector_terms", "external_contact_terms", "external_action_terms"):
        words(scope.get(key))
    for key in ("foreign_authority_terms", "domestic_securities_terms"):
        if key in scope:
            words(scope[key])
    required_signals = {"permission_terms", "permission_completion_terms", "interim_bodies", "interim_decisions",
                        "policy_terms", "organization_terms", "organization_change_terms", "partnership_terms",
                        "new_service_terms", "new_service_actions", "performance_actions", "risk_events",
                        "historical_transition_terms"}
    required_exclusions = {"filing_forms", "promotion_terms", "stock_commentary_terms", "forecast_terms",
                           "individual_product_terms", "individual_product_actions", "strategic_product_terms"}
    if not required_signals <= rules["signals"].keys() or not required_exclusions <= rules["exclusions"].keys():
        raise ValueError("Required signal or exclusion lists are missing")
    return rules


def load_classification_rules(path=None):
    # Read on every explicit load so editing the same path never reuses stale rules.
    with Path(path or RULES_PATH).open(encoding="utf-8") as handle:
        return validate_classification_rules(json.load(handle))


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
    effective = source.get("_effective_content_policy_version", version)
    if not isinstance(effective, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", effective):
        raise ValueError("Invalid effective content policy version")
    return content_filter, effective


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
    """Keep DART's form gate independent of the news event rules."""
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
                "matched_keywords": [form], "matched_topic_ids": ["performance"],
                "tags": ["실적"], "event_stage": "실적 공시", "classification_rules_version": DART_POLICY_VERSION}
    rules = source.get("_classification_rules")
    scoped_title, scoped_companies = title, companies
    # Official press releases often omit their issuing institution in the title.
    # Supply that provenance only for routing; the stored source title stays exact.
    if (source.get("adapter") == "fsc_html" and
            source.get("authority_name") == "금융위원회" and
            urlsplit(source.get("url", "")).hostname == "www.fsc.go.kr"):
        scoped_title = "금융위원회 " + title
    if source.get("scope") == "company":
        company = next((item for item in companies if item["id"] == source.get("company_id") and item.get("enabled", True)), None)
        if company is None:
            return None
        scoped_title, scoped_companies = company["name"] + " " + title, [company]
    route = classify(scoped_title, scoped_companies, topics, rules=rules)
    if route and source.get("scope") == "competitors" and route["subject_scope"] != "securities_company":
        route = None
    if route:
        return route
    # Interests supplement the event keywords, never the approved source or
    # securities-sector identity gate. Their routing is not source metadata.
    interests = source.get("_interests", [])
    if (not interests or source.get("type") != "rss" or
            source.get("adapter") not in {"einfomax_rss", "newswire_rss"}):
        return None
    from .interests import match_title
    matches = match_title(title, interests)
    if not matches:
        return None
    rules = validate_classification_rules(rules) if rules is not None else load_classification_rules()
    if _excluded(scoped_title, rules):
        return None
    subject = _subjects(scoped_title, scoped_companies, rules)
    if subject is None or (source.get("scope") == "competitors" and subject["subject_scope"] != "securities_company"):
        return None
    matched = {row["interest_id"]: row["keywords"] for row in matches}
    selected = [item for item in interests if item["id"] in matched]
    primary = selected[0]["topic_id"]
    tags = [name for name, words in rules["tag_keywords"].items() if _any(title, words)]
    return {**subject, "topic_id": primary, "topic": topics[primary],
            "matched_keywords": list(dict.fromkeys(word for row in matches for word in row["keywords"])),
            "matched_topic_ids": list(dict.fromkeys(item["topic_id"] for item in selected)),
            "tags": list(dict.fromkeys([*tags, "경영진 관심사"])),
            "event_stage": _event_stage(_current_action(title, rules), rules, primary) or "관심사 키워드",
            "classification_rules_version": rules["version"]}


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
        return re.search(r"(?<![a-z0-9])" + re.escape(keyword.casefold()) + r"(?![a-z0-9])", title.casefold()) is not None
    return re.sub(r"\s+", "", keyword).casefold() in re.sub(r"\s+", "", title).casefold()


def _hits(title, words):
    return [word for word in words if _matches(title, word)]


def _any(title, words):
    return any(_matches(title, word) for word in words)


def _subjects(title, companies, rules):
    matched = [company for company in companies if company.get("enabled", True) and
               _any(title, [company["name"], *company.get("aliases", [])])]
    if matched:
        return {"company": " · ".join(item["name"] for item in matched),
                "company_ids": [item["id"] for item in matched],
                "companies": [item["name"] for item in matched], "subject_scope": "securities_company"}
    scope = rules["subject_scope"]
    if (_any(title, scope.get("foreign_authority_terms", [])) and
            not _any(title, scope.get("domestic_securities_terms", []))):
        return None
    for field, context, kind in (("related_groups", "group_context_terms", "financial_group"),
                                 ("authorities", "sector_terms", "authority"),
                                 ("market_infrastructure", "sector_terms", "market_infrastructure")):
        matched = [item for item in scope[field] if _any(title, [item["name"], *item["aliases"]])]
        if matched and _any(title, scope[context]):
            names = [item["name"] for item in matched]
            return {"company": " · ".join(names), "company_ids": [], "companies": names, "subject_scope": kind}
    if _any(title, scope["external_contact_terms"]) and _any(title, scope["external_action_terms"]):
        return {"company": "증권업권 관련", "company_ids": [], "companies": [], "subject_scope": "sector_contact"}
    return None


def _current_action(title, rules):
    # Explicit 'after approval' wording makes the following action current. Tags
    # still use the whole title, but historical licensing must not override launch.
    endings = [match.end() for term in rules["signals"]["historical_transition_terms"]
               for match in re.finditer(r"\s*".join(re.escape(char) for char in term), title, re.IGNORECASE)]
    return title[max(endings):] if endings else title


def _excluded(title, rules):
    exclusions = rules["exclusions"]
    form = re.sub(r"[^가-힣a-zA-Z0-9]", "", title)
    if any(re.sub(r"[^가-힣a-zA-Z0-9]", "", term) in form for term in exclusions["filing_forms"]):
        return True
    if _any(title, exclusions["promotion_terms"] + exclusions["stock_commentary_terms"]):
        return True
    service_change = (_any(title, rules["signals"]["new_service_terms"]) and
                      _any(title, rules["signals"]["new_service_actions"]))
    if _any(title, exclusions["forecast_terms"]) and not service_change:
        return True
    if (_any(title, exclusions.get("research_terms", [])) and
            _any(title, exclusions.get("third_party_terms", [])) and not service_change):
        return True
    return (_any(title, exclusions["individual_product_terms"]) and
            _any(title, exclusions["individual_product_actions"]) and
            not _any(title, exclusions["strategic_product_terms"]))


def _event_stage(title, rules, primary=None):
    signals = rules["signals"]
    permission = _any(title, signals["permission_terms"])
    if permission and _any(title, signals.get("permission_noncompletion_terms", [])):
        return "미승인"
    tentative = any(_any(title, stage["keywords"]) for stage in rules["event_stages"]
                    if stage["name"] in {"검토", "계획·추진"})
    if permission and _any(title, signals.get("permission_rejection_terms", [])) and not tentative:
        return "불허·반려"
    # Tentative and denied actions must never turn into completed announcements.
    early = {"부인", "중단·철회", "검토", "계획·추진", "신청"}
    for stage in rules["event_stages"]:
        if stage["name"] in early and _any(title, stage["keywords"]):
            return stage["name"]
    if permission and _any(title, signals.get("permission_uncertain_terms", [])):
        return None
    if permission and _any(title, signals.get("permission_rejection_terms", [])):
        return "불허·반려"
    if permission and _any(title, ["보류", "유보"]):
        return "보류"
    if permission and _any(title, signals.get("permission_discussion_terms", [])):
        return "쟁점·논의"
    if permission and _any(title, signals["interim_bodies"]) and _any(title, signals["interim_decisions"]):
        return "심사 통과"
    if permission and _any(title, ["예비", "인가안", "안건", "심사", "심의", "상정"]):
        return "심사"
    if permission and (_any(title, signals["permission_completion_terms"]) or
                       re.search(r"(?:인가|허가|승인|등록)(?:\s*(?:완료|결정|확정|획득|취득))?\s*$", title)):
        return "인가·승인"
    if _any(title, signals.get("inspection_terms", [])):
        return "점검·조사"
    if primary == "strategy" and _any(title, signals["organization_terms"]) and _any(title, signals["organization_change_terms"]):
        return "조직 변경"
    for stage in rules["event_stages"]:
        if stage["name"] not in early and _any(title, stage["keywords"]):
            return stage["name"]
    # Explicit financial amounts are an outcome even without the word 'announced'.
    if _any(title, ["영업이익", "순이익", "매출", "매출액"]) and re.search(r"\d[\d,.]*\s*(?:조|억|만)?\s*원", title):
        return "성과"
    if _any(title, signals["policy_terms"]) and _any(title, ["방향", "주요내용", "공개"]):
        return "정책 발표"
    return None


def classify(title, companies, topics, rules=None):
    """Route only titles with a relevant subject, topic and observable change."""
    rules = validate_classification_rules(rules) if rules is not None else load_classification_rules()
    if _excluded(title, rules):
        return None
    subject = _subjects(title, companies, rules)
    if subject is None:
        return None
    action = _current_action(title, rules)
    # '인수업' is securities underwriting, never an M&A event on its own.
    matching_action = action.replace("인수업", "증권업무")
    customer_dividend = re.search(r"(?:배당.{0,20}(?:고객|가입자|투자자)|(?:고객|가입자|투자자).{0,20}배당)",
                                  re.sub(r"\s+", "", title)) is not None
    hits = {topic: _hits(matching_action, words) for topic, words in rules["topic_keywords"].items() if topic in topics}
    if customer_dividend:
        hits["performance"] = [word for word in hits.get("performance", []) if word not in {"배당", "실적"}]
    hits = {topic: values for topic, values in hits.items() if values}
    signals = rules["signals"]
    if not hits:
        return None
    if _any(action, signals["permission_terms"] + signals["policy_terms"] + signals["risk_events"]):
        primary = "risk"
    elif _any(action, signals["organization_terms"]) and _any(action, signals["organization_change_terms"]):
        primary = "strategy"
    elif subject["subject_scope"] == "financial_group" and _any(action, ["증권", "자회사"]) and _any(action, ["장내매수", "지분확대", "주식매수", "자본", "증자"]):
        primary = "performance"
    elif _any(action, signals["partnership_terms"]):
        primary = "expansion"
    elif _any(action, signals["new_service_terms"]) and (_any(action, signals["new_service_actions"]) or _any(action, ["누적", "완판", "발행액", "돌파"])):
        primary = "digital"
    elif "expansion" in hits and _any(matching_action, ["인수", "합병", "M&A", "해외", "현지법인", "사무소", "지분투자"]):
        primary = "expansion"
    else:
        # Stable display routing only; this is not a ranking of news importance.
        primary = next((topic for topic in ("performance", "digital", "strategy", "expansion", "risk") if topic in hits), None)
    if primary not in topics:
        return None
    stage = _event_stage(matching_action, rules, primary)
    if stage is None:
        return None
    if primary not in hits:
        # A named signal may be narrower than its configurable topic keywords.
        evidence_words = signals["permission_terms"] + signals["policy_terms"] if primary == "risk" else signals["new_service_terms"]
        hits[primary] = _hits(action, evidence_words)
    tags = [name for name, words in rules["tag_keywords"].items()
            if _any(title.replace("인수업", "증권업무"), words)]
    if customer_dividend:
        tags = [tag for tag in tags if tag != "주주정책"]
    return {**subject, "topic_id": primary, "topic": topics[primary],
            "matched_keywords": hits[primary], "matched_topic_ids": list(hits),
            "tags": tags, "event_stage": stage, "classification_rules_version": rules["version"]}

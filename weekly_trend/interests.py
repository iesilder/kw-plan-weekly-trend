"""Owner-authored public GitHub Issues as a bounded, offline-readable registry.

Only registration forms become configuration. Network synchronization is explicit;
matching, preparing the public sidecar and checking it never make HTTP requests.
"""

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "1.0"
REPOSITORY = "iesilder/kw-plan-weekly-trend"
OWNER = "iesilder"
ISSUE_URL = "https://github.com/" + REPOSITORY + "/issues/"
API_URL = "https://api.github.com/repos/" + REPOSITORY + "/issues"
REGISTRATION_PREFIX = "[관심사] "
REQUEST_TITLE = "[관심사 수집 요청]"
CONSENT = "관심사와 검색어가 공개 GitHub 이슈와 웹사이트에 저장됨을 이해합니다."
HEADINGS = ("관심 주제", "보고 질문", "검색어", "제외 검색어", "분류", "시작일", "종료일", "공개 저장 동의")
TOPICS = {"performance", "digital", "expansion", "strategy", "risk"}
TOPIC_LABELS = {"실적·자본·주주정책": "performance", "신사업·상품·고객전략": "digital", "제휴·인수·해외사업": "expansion",
                "조직·경영전략": "strategy", "정책·인허가·리스크": "risk"}
FIELDS = {"id", "issue_number", "title", "question", "keywords", "exclude_keywords", "topic_id", "active_from", "active_until", "updated_at", "source_url", "enabled"}
INTEREST_ID = re.compile(r"interest-[1-9][0-9]{0,9}\Z")
CANDIDATE_ID = re.compile(r"issue-[a-f0-9]{20}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
MAX_ACTIVE = 20
MAX_RECORDS = 1000
MAX_PAGES = 5
PAGE_SIZE = 100
MAX_RESPONSE_BYTES = 2_000_000
MAX_FILE_BYTES = 8_000_000
TIMEOUT = 20
COLLECTION_STATUSES = {"success", "partial_success", "failed", "skipped"}
SYNC_ERRORS = {"missing_token", "active_limit", "response_too_large", "github_rate_limited", "github_access_denied", "github_http_error",
               "network_error", "invalid_github_response", "invalid_interest_form", "public_consent_required", "pagination_limit", "sync_error"}


class InterestError(Exception):
    """A safe code; never include raw Issue bodies, HTTP bodies or credentials."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Interest time must contain a timezone")
    return value.astimezone(timezone.utc)


def _stamp(now=None):
    return _now(now).isoformat(timespec="microseconds")


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("Invalid interest timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Invalid interest timestamp")
    return parsed


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid interest date")
    return date.fromisoformat(value)


def _keys(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Interest data has missing or unexpected fields")


def _text(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid bounded interest text")
    return value


def _words(values, required=False):
    if not isinstance(values, list) or len(values) > 10 or (required and not values):
        raise ValueError("Interest keywords must have at most ten entries")
    seen = set()
    for value in values:
        _text(value, 80)
        key = " ".join(value.casefold().split())
        if key in seen:
            raise ValueError("Duplicate interest keyword")
        seen.add(key)
    return values


def _number(value):
    if type(value) is not int or not 1 <= value <= 9_999_999_999:
        raise ValueError("Invalid interest issue number")
    return value


def _url(value, number=None):
    if not isinstance(value, str) or not re.fullmatch(re.escape(ISSUE_URL) + r"[1-9][0-9]{0,9}", value) or (number is not None and value != ISSUE_URL + str(number)):
        raise ValueError("Invalid interest source URL")
    return value


def validate_interest(value):
    _keys(value, FIELDS)
    number = _number(value["issue_number"])
    if value["id"] != "interest-" + str(number):
        raise ValueError("Interest identity differs from its source issue")
    _text(value["title"], 120)
    _text(value["question"], 1000)
    _words(value["keywords"], required=True)
    _words(value["exclude_keywords"])
    if not isinstance(value["topic_id"], str) or value["topic_id"] not in TOPICS or type(value["enabled"]) is not bool:
        raise ValueError("Invalid interest topic or enabled flag")
    start = _date(value["active_from"])
    if value["active_until"] is not None and _date(value["active_until"]) < start:
        raise ValueError("Interest expiry precedes its start")
    _time(value["updated_at"])
    _url(value["source_url"], number)
    return value


def _validate_records(records):
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("Too many interest records")
    seen, events = set(), {}
    for record in records:
        validate_interest(record)
        if record["id"] in seen:
            raise ValueError("Duplicate interest identity")
        seen.add(record["id"])
        if record["enabled"]:
            start = _date(record["active_from"])
            events[start] = events.get(start, 0) + 1
            end = _date(record["active_until"]) if record["active_until"] else None
            if end is not None and end < date.max:
                after = end + timedelta(days=1)
                events[after] = events.get(after, 0) - 1
    active = 0
    for day in sorted(events):
        active += events[day]
        if active > MAX_ACTIVE:
            raise InterestError("active_limit")
    return records


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate interest JSON field")
        result[key] = value
    return result


def _read(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Interest file exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)


def _state(interest, now=None):
    today = _now(now).astimezone(ZoneInfo("Asia/Seoul")).date()
    if not interest["enabled"]:
        return "disabled"
    if interest["active_until"] and _date(interest["active_until"]) < today:
        return "expired"
    return "active" if _date(interest["active_from"]) <= today else "disabled"


def load_interests(root, now=None, active_only=False):
    """Read strict normalized configuration; only legacy *empty* shape is accepted.

    Active dates are inclusive Korean calendar dates. Future registrations are
    enabled configuration but do not match or collect until their start date.
    """
    path = Path(root) / "config/interests.json"
    value = _read(path) if path.exists() else {"schema_version": "1.0", "interests": []}
    if value != {"interests": []}:
        _keys(value, {"schema_version", "interests"})
        if value["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported interest schema")
    records = _validate_records(value["interests"])
    return [record for record in records if not active_only or _state(record, now) == "active"]


def _matches(title, keyword):
    if keyword.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(keyword.casefold()) + r"(?![a-z0-9])", title.casefold()) is not None
    return re.sub(r"\s+", "", keyword).casefold() in re.sub(r"\s+", "", title).casefold()


def match_title(title, interests):
    """Match supplied active interests without network, NLP, or source inference.

    Latin keywords use ASCII alphanumeric boundaries; Korean whitespace is
    ignored. Any exclusion vetoes that interest. Call load_interests(...,
    active_only=True) before collection; this helper does not consult the clock.
    """
    _text(title, 1000)
    result = []
    for interest in interests:
        if any(_matches(title, word) for word in interest["exclude_keywords"]):
            continue
        words = [word for word in interest["keywords"] if _matches(title, word)]
        if words:
            result.append({"interest_id": interest["id"], "keywords": words})
    return result


def interest_fingerprint(interest):
    """Hash matching/routing fields only; wording edits do not restart backfill."""
    fields = {key: interest[key] for key in ("keywords", "exclude_keywords", "topic_id")}
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _empty_registry():
    return {"schema_version": "1.0", "status": "never_synced", "last_attempt_at": None, "last_success_at": None, "error_code": None}


def _registry(root):
    path = Path(root) / "data/interests-registry.json"
    value = _read(path) if path.exists() else _empty_registry()
    _keys(value, _empty_registry())
    if value["schema_version"] != "1.0" or value["status"] not in {"never_synced", "success", "failed", "blocked"}:
        raise ValueError("Invalid interest registry status")
    for field in ("last_attempt_at", "last_success_at"):
        if value[field] is not None:
            _time(value[field])
    if value["error_code"] is not None and (not isinstance(value["error_code"], str) or not CODE.fullmatch(value["error_code"])):
        raise ValueError("Invalid interest registry error")
    return value


def _persist(root, values):
    """Replace staged files together and roll back ordinary local I/O failures."""
    root = Path(root)
    with tempfile.TemporaryDirectory(prefix="interest-save-", dir=root) as directory:
        stage = Path(directory)
        originals, replaced = [], []
        for index, (relative, value) in enumerate(values):
            path = root / relative
            originals.append(path.read_bytes() if path.exists() else None)
            (stage / str(index)).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            for index, (relative, _) in enumerate(values):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / str(index), path)
                replaced.append(index)
        except OSError:
            for index in reversed(replaced):
                path = root / values[index][0]
                if originals[index] is None:
                    path.unlink()
                else:
                    (stage / "restore").write_bytes(originals[index])
                    os.replace(stage / "restore", path)
            raise


def _optional(value):
    return "" if value.strip() in {"", "_No response_"} else value.strip()


def _form_words(value, required=False):
    value = _optional(value)
    words = []
    for item in re.split(r"[,\n]", value):
        word = item.strip()
        if word:
            words.append(word)
    return _words(words, required)


def _parse_issue(issue, previous, now):
    body = issue["body"]
    if not isinstance(body, str) or len(body) > 12_000:
        raise InterestError("invalid_interest_form")
    headings = list(re.finditer(r"^### ([^\r\n]+)\r?$", body, re.MULTILINE))
    if tuple(match[1] for match in headings) != HEADINGS or (headings and body[:headings[0].start()].strip()):
        raise InterestError("invalid_interest_form")
    sections = {match[1]: body[match.end():headings[index + 1].start() if index + 1 < len(headings) else len(body)].strip()
                for index, match in enumerate(headings)}
    if not re.fullmatch(r"- \[[xX]\] " + re.escape(CONSENT), sections["공개 저장 동의"]):
        raise InterestError("public_consent_required")
    registered = _time(issue["created_at"]) if issue.get("created_at") else now
    start = _optional(sections["시작일"]) or (previous["active_from"] if previous else registered.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat())
    value = {"id": "interest-" + str(issue["number"]), "issue_number": issue["number"],
             "title": sections["관심 주제"], "question": " ".join(sections["보고 질문"].split()),
             "keywords": _form_words(sections["검색어"], True), "exclude_keywords": _form_words(sections["제외 검색어"]),
             "topic_id": TOPIC_LABELS.get(sections["분류"], sections["분류"]), "active_from": start, "active_until": _optional(sections["종료일"]) or None,
             "updated_at": issue["updated_at"], "source_url": issue["html_url"], "enabled": True}
    validate_interest(value)
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def request_page(url, token, timeout):
    """One bounded GitHub request, no redirects/retries, decoded JSON only."""
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                                  "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "weekly-trend-interest-registry/1.0"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise InterestError("response_too_large")
            return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except urllib.error.HTTPError as error:
        raise InterestError("github_rate_limited" if error.code == 429 else "github_access_denied" if error.code in {401, 403} else "github_http_error") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise InterestError("network_error") from None
    except (ValueError, UnicodeError):
        raise InterestError("invalid_github_response") from None


def sync_from_github(root, environ=None, transport=None, now=None):
    """Fetch the complete bounded open-Issue registry and retain history.

    ``transport(url, token, timeout)`` may return a decoded GitHub Issue list for
    deterministic tests. Missing credentials, malformed forms, incomplete paging
    and normal HTTP/network errors return safe status and retain prior config.
    Closing/removing a registration disables its saved record after a complete
    successful snapshot; records are not deleted. No API response is persisted.
    """
    root, instant = Path(root), _now(now)
    stamp = _stamp(instant)
    previous = load_interests(root)
    registry = _registry(root)
    request_count = 0

    def failed(code, status="failed"):
        code = code if code in SYNC_ERRORS else "sync_error"
        metadata = {**registry, "status": status, "last_attempt_at": stamp, "error_code": code}
        _persist(root, [("data/interests-registry.json", metadata)])
        return {"ok": False, "status": status, "error_code": code, "changed": False, "interest_count": len(previous),
                "request_count": request_count, "last_attempt_at": stamp, "last_success_at": metadata["last_success_at"]}

    token = (os.environ if environ is None else environ).get("GITHUB_TOKEN", "")
    if not isinstance(token, str) or not token.strip():
        return failed("missing_token", "blocked")
    prior = {record["id"]: record for record in previous}
    found, seen = {}, set()
    try:
        for page in range(1, MAX_PAGES + 1):
            url = API_URL + "?state=open&creator=iesilder&per_page=" + str(PAGE_SIZE) + "&page=" + str(page)
            request_count += 1
            payload = (transport or request_page)(url, token.strip(), TIMEOUT)
            if not isinstance(payload, list) or len(payload) > PAGE_SIZE or len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise InterestError("invalid_github_response")
            for issue in payload:
                if not isinstance(issue, dict):
                    raise InterestError("invalid_github_response")
                number = _number(issue.get("number"))
                if number in seen or issue.get("state") != "open":
                    raise InterestError("invalid_github_response")
                seen.add(number)
                # The Issues API also returns PRs, whose public URL is /pull/N.
                if "pull_request" in issue:
                    continue
                _url(issue.get("html_url"), number)
                user = issue.get("user")
                if not isinstance(user, dict) or not isinstance(user.get("login"), str):
                    raise InterestError("invalid_github_response")
                if user["login"] != OWNER:
                    continue
                title = issue.get("title")
                if not isinstance(title, str) or len(title) > 256:
                    raise InterestError("invalid_github_response")
                if title != REGISTRATION_PREFIX.strip() and not title.startswith(REGISTRATION_PREFIX):
                    continue
                _time(issue.get("updated_at"))
                record = _parse_issue(issue, prior.get("interest-" + str(number)), instant)
                found[record["id"]] = record
            if len(payload) < PAGE_SIZE:
                break
        else:
            raise InterestError("pagination_limit")
        records = list(found.values())
        records.extend({**record, "enabled": False, "updated_at": stamp if record["enabled"] else record["updated_at"]}
                       for record in previous if record["id"] not in found)
        records.sort(key=lambda record: record["issue_number"])
        _validate_records(records)
    except InterestError as error:
        return failed(error.code)
    except (ValueError, TypeError, KeyError, UnicodeError):
        return failed("invalid_interest_form")
    except (OSError, TimeoutError, urllib.error.URLError):
        return failed("network_error")
    except Exception:
        # Injected transports may contain credentials/raw responses in messages.
        return failed("sync_error")
    registry = {"schema_version": "1.0", "status": "success", "last_attempt_at": stamp, "last_success_at": stamp, "error_code": None}
    _persist(root, [("config/interests.json", {"schema_version": "1.0", "interests": records}), ("data/interests-registry.json", registry)])
    return {"ok": True, "status": "success", "error_code": None, "changed": records != previous, "interest_count": len(records),
            "request_count": request_count, "last_attempt_at": stamp, "last_success_at": stamp}


def _collection(root, value=None):
    path = Path(root) / "data/interests-collection.json"
    value = value if value is not None else _read(path) if path.exists() else {"schema_version": "1.0", "entries": {}, "last_attempt": None}
    _keys(value, {"schema_version", "entries", "last_attempt"})
    if value["schema_version"] != "1.0" or not isinstance(value["entries"], dict) or len(value["entries"]) > MAX_RECORDS:
        raise ValueError("Invalid interest collection book")
    for interest_id, entry in value["entries"].items():
        if not isinstance(interest_id, str) or not INTEREST_ID.fullmatch(interest_id) or not isinstance(entry, dict):
            raise ValueError("Invalid interest collection identity")
        if not {"filter_fingerprint", "last_success_at", "sources"} <= set(entry) or not HASH.fullmatch(entry["filter_fingerprint"]) or not isinstance(entry["sources"], dict):
            raise ValueError("Invalid interest collection checkpoint")
        if entry["last_success_at"] is not None:
            _time(entry["last_success_at"])
        if entry.get("status", "success") not in {"success", "partial_success", "failed", "pending"}:
            raise ValueError("Invalid interest collection status")
    attempt = value["last_attempt"]
    if attempt is not None:
        required = {"mode", "started_at", "completed_at", "status", "request_url", "error_code"}
        if not isinstance(attempt, dict) or not required <= set(attempt) or len(attempt) > 12:
            raise ValueError("Invalid interest collection attempt")
        if attempt["mode"] not in {"daily", "manual"} or attempt["status"] not in COLLECTION_STATUSES or _time(attempt["completed_at"]) < _time(attempt["started_at"]):
            raise ValueError("Invalid interest collection attempt")
        if attempt["request_url"] is not None:
            _url(attempt["request_url"])
        if attempt["error_code"] is not None and (not isinstance(attempt["error_code"], str) or not CODE.fullmatch(attempt["error_code"])):
            raise ValueError("Invalid interest collection error")
    return value


def _news(candidate, detail, generation, sources):
    if candidate.get("is_demo") is not False or candidate.get("content_filter") != "topics" or not isinstance(detail, dict):
        return False
    if detail.get("issue_id") != candidate.get("issue_id") or detail.get("generation_id") != generation:
        raise ValueError("Interest evidence belongs to another snapshot")
    evidence = detail.get("evidence", [])
    if not evidence or not isinstance(evidence, list):
        return False
    for row in evidence:
        source = sources.get(row.get("source_id")) if isinstance(row, dict) else None
        if not source or source.get("type") != "rss" or source.get("adapter") not in {"einfomax_rss", "newswire_rss"} or source.get("content_filter", "topics") != "topics":
            return False
        if source.get("enabled") is not True or source.get("policy_status") != "verified" or source.get("retention") != "metadata_only" or row.get("is_demo") is not False:
            return False
    return True


def _recent(candidate, detail, now):
    """Use real source dates; re-fetching old metadata cannot revive a match."""
    today = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    earliest, latest = today - timedelta(days=30), today + timedelta(days=1)
    rows = detail["evidence"]
    known = []
    for row in rows:
        for field in ("published_at", "source_updated_at"):
            if row.get(field) is not None:
                known.append(_time(row[field]).astimezone(ZoneInfo("Asia/Seoul")).date())
        if row.get("published_date") is not None:
            known.append(_date(row["published_date"]))
    if known:
        return earliest <= max(known) <= latest
    # Canonical mart fetched_at is the version's *first* fetch, not last poll.
    explicit = [row["first_fetched_at"] for row in [candidate, *rows] if row.get("first_fetched_at") is not None]
    fallback = explicit or [row["fetched_at"] for row in [candidate, *rows] if row.get("fetched_at") is not None]
    return bool(fallback) and earliest <= min(_time(value).astimezone(ZoneInfo("Asia/Seoul")).date() for value in fallback) <= latest


def _detail(root, candidate):
    issue_id = candidate.get("issue_id")
    if not isinstance(issue_id, str) or not CANDIDATE_ID.fullmatch(issue_id):
        raise ValueError("Invalid interest candidate identity")
    expected = "data/issues/" + issue_id + ".json"
    if candidate.get("detail_path") != expected:
        raise ValueError("Invalid interest candidate detail path")
    return _read(Path(root) / "site" / expected)


def build_public(root, latest, details=None, now=None, collection=None):
    """Return a generation-bound public sidecar using trusted RSS titles only.

    ``details`` is an issue-id keyed map when staging a new collection snapshot.
    All configured interests/history are exposed; only currently active interests
    receive matches. Collection internals and untrusted Issue bodies stay private.
    """
    instant = _now(now)
    records = load_interests(root, now=instant)
    registry, book = _registry(root), _collection(root, collection)
    if not isinstance(latest, dict) or not isinstance(latest.get("generation_id"), str) or not latest["generation_id"] or not isinstance(latest.get("candidates"), list):
        raise ValueError("Invalid interest source snapshot")
    active = [record for record in records if _state(record, instant) == "active"]
    matches = {record["id"]: [] for record in records}
    if latest.get("is_demo") is False and active:
        sources_path = Path(root) / "config/sources.json"
        sources = {source["id"]: source for source in _read(sources_path).get("sources", [])} if sources_path.exists() else {}
        seen = set()
        for candidate in latest["candidates"]:
            issue_id = candidate.get("issue_id")
            if not isinstance(issue_id, str) or not CANDIDATE_ID.fullmatch(issue_id) or issue_id in seen:
                raise ValueError("Invalid or repeated interest candidate")
            seen.add(issue_id)
            detail = details.get(issue_id) if details is not None else _detail(root, candidate)
            if not _news(candidate, detail, latest["generation_id"], sources) or not _recent(candidate, detail, instant):
                continue
            for match in match_title(candidate["title"], active):
                matches[match["interest_id"]].append({"issue_id": issue_id, "keywords": match["keywords"]})
    public_records = []
    for record in records:
        state = _state(record, instant)
        entry = book["entries"].get(record["id"])
        status = state if state != "active" else "never_collected"
        if state == "active" and entry:
            status = "stale" if entry["filter_fingerprint"] != interest_fingerprint(record) else entry.get("status", "success" if entry["last_success_at"] else "pending")
        public_records.append({**record, "state": state, "last_collected_at": entry["last_success_at"] if entry else None,
                               "collection_status": status, "match_count": len(matches[record["id"]]), "matches": matches[record["id"]]})
    attempt = book["last_attempt"]
    return {"schema_version": "1.0", "generation_id": latest["generation_id"], "generated_at": _stamp(instant),
            "registry": {key: registry[key] for key in ("status", "last_attempt_at", "last_success_at", "error_code")},
            "last_collection": {key: attempt[key] for key in ("mode", "started_at", "completed_at", "status", "request_url", "error_code")} if attempt else None,
            "interests": public_records}


def prepare(root, now=None):
    """Rebuild the public sidecar offline; never synchronize GitHub implicitly."""
    root = Path(root)
    latest = _read(root / "site/data/latest.json")
    public = build_public(root, latest, now=now)
    _persist(root, [("site/data/interests.json", public)])
    return {"ok": True, "mode": "prepared", "generation_id": public["generation_id"], "interest_count": len(public["interests"]), "request_count": 0}


def check(root):
    """Verify the published sidecar against current config, registry and mart."""
    root = Path(root)
    path = root / "site/data/interests.json"
    if not path.exists():
        if not load_interests(root) and not (root / "data/interests-collection.json").exists():
            return {"ok": True, "mode": "not_prepared", "request_count": 0}
        raise ValueError("Interest public sidecar is missing")
    actual = _read(path)
    latest = _read(root / "site/data/latest.json")
    expected = build_public(root, latest, now=_time(actual.get("generated_at")))
    if actual != expected:
        raise ValueError("Interest public sidecar differs from current source data")
    return {"ok": True, "mode": "verified", "generation_id": actual["generation_id"], "interest_count": len(actual["interests"]), "request_count": 0}

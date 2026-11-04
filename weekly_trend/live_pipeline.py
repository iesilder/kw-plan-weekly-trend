"""Live public metadata collection; no article-body request, AI call or scheduler."""

import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .classification import (classify_for_source, load_classification_rules,
                             plain_title, safe_https_url, source_policy)
from .live_storage import (apply_policies, connect, counts, current_records, ingest,
                           previous_sources, save_source, timestamp, validate_policies)
from .pipeline import TOPIC_IDS, read_json, write_json
from .storage import canonical_url, digest

CAPTURE_VERSION = "raw-metadata-v1"

COVERAGE_NOTE = "현재 피드·목록에 남아 있는 항목만 확인하며, 해당 기간 전체 수집을 보장하지 않습니다."
ERROR_MESSAGES = {
    "collector_exception": "출처 수집 중 오류가 발생했습니다. 다음 실행에서 재시도합니다.",
    "invalid_response": "출처 응답 형식을 검증하지 못했습니다.",
    "invalid_records": "출처 항목이 저장 안전성 검사를 통과하지 못했습니다.",
}


def _source_with_classification_context(source, rules, companies, topics):
    """Give new collection and stored-history routing the same effective policy."""
    source = {**{key: value for key, value in source.items() if not key.startswith("_")},
              "_classification_rules": rules}
    content_filter, configured_version = source_policy(source)
    relevant_companies = (companies if content_filter != "earnings_only" else
                          [item for item in companies if item["id"] == source.get("company_id")])
    policy = {"configured_version": configured_version, "content_filter": content_filter,
              "scope": source.get("scope"), "company_id": source.get("company_id"),
              "authority_name": source.get("authority_name"), "adapter": source.get("adapter"), "url": source.get("url"),
              "companies": sorted([{"id": item["id"], "name": item["name"],
                                    "enabled": item.get("enabled", True),
                                    "aliases": sorted(item.get("aliases", []))}
                                   for item in relevant_companies], key=lambda item: item["id"]),
              "topics": ({"performance": topics["performance"]} if content_filter == "earnings_only" else topics)}
    if content_filter != "earnings_only":
        policy["rules"] = rules
    fingerprint = digest(json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")))[:16]
    source["_effective_content_policy_version"] = configured_version[:80] + "_" + fingerprint
    return source


def _schedule_metadata(settings, *, allow_legacy=False):
    enabled = settings.get("schedule_enabled")
    frequency = settings.get("planned_collection_frequency")
    zone = settings.get("timezone", "Asia/Seoul")
    if type(enabled) is not bool or zone != "Asia/Seoul":
        raise ValueError("Collection schedule must use a boolean flag and Asia/Seoul timezone")
    if enabled:
        if frequency != "daily_1100_kst" and not (allow_legacy and frequency == "daily_0800_kst"):
            raise ValueError("Enabled collection schedule must declare daily 11:00 KST")
    elif frequency not in (None, "manual", "daily_not_enabled"):
        raise ValueError("Disabled collection schedule cannot claim an active daily frequency")
    return {"schedule_enabled": enabled, "planned_collection_frequency": frequency or "manual", "timezone": zone}



def _interest_source(source):
    return source.get("type") == "rss" and source.get("adapter") in {"einfomax_rss", "newswire_rss"}


def _interest_book(root):
    path = Path(root) / "data/interests-collection.json"
    value = read_json(path) if path.exists() else {"schema_version": "1.0", "entries": {}, "last_attempt": None}
    if (not isinstance(value, dict) or set(value) != {"schema_version", "entries", "last_attempt"}
            or value["schema_version"] != "1.0" or not isinstance(value["entries"], dict)):
        raise ValueError("Invalid interest collection checkpoints")
    for interest_id, entry in value["entries"].items():
        if (not re.fullmatch(r"interest-[1-9][0-9]*", interest_id) or not isinstance(entry, dict)
                or not re.fullmatch(r"[a-f0-9]{64}", entry.get("filter_fingerprint", ""))
                or not isinstance(entry.get("sources"), dict)):
            raise ValueError("Invalid interest collection identity")
        if entry.get("last_success_at") is not None:
            timestamp(entry["last_success_at"])
        for source_id, checkpoint in entry["sources"].items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", source_id) or not isinstance(checkpoint, dict):
                raise ValueError("Invalid interest source checkpoint")
            if not re.fullmatch(r"[a-f0-9]{64}", checkpoint.get("filter_fingerprint", "")):
                raise ValueError("Invalid interest source fingerprint")
            timestamp(checkpoint["next_collection_start"])
            if checkpoint.get("last_success_at") is not None:
                timestamp(checkpoint["last_success_at"])
    attempt = value["last_attempt"]
    if attempt is not None:
        if (not isinstance(attempt, dict) or set(attempt) != {"mode", "started_at", "completed_at", "status", "request_url", "error_code"}
                or attempt["mode"] not in {"daily", "manual"}
                or attempt["status"] not in {"success", "partial_success", "failed", "skipped"}):
            raise ValueError("Invalid interest collection attempt")
        timestamp(attempt["started_at"])
        timestamp(attempt["completed_at"])
        if attempt["error_code"] is not None and not re.fullmatch(r"[a-z_]{1,80}", attempt["error_code"]):
            raise ValueError("Invalid interest collection error")
    return value


def _interest_context(source, interests, book, now, manual=False):
    from .interests import interest_fingerprint
    windows, force, checkpoints = {}, False, []
    for interest in interests:
        fingerprint = interest_fingerprint(interest)
        entry = book["entries"].get(interest["id"], {})
        prior = entry.get("sources", {}).get(source["id"], {})
        same = prior.get("filter_fingerprint") == fingerprint
        start = (prior.get("next_collection_start") if same else None) or timestamp((now - timedelta(days=30)).isoformat())
        windows[interest["id"]] = start
        force = force or not same or prior.get("last_success_at") is None
        if same:
            checkpoints.append(prior)
    result = {**source, "_interests": interests, "_interest_windows": windows,
              "_interest_force": force, "_interests_only": manual}
    if manual:
        _, policy = source_policy(source)
        # A validator from one interest cannot stand in for a representation
        # another interest has never processed (for example after reactivation).
        if len({row.get("last_success_at") for row in checkpoints}) > 1 or len({row.get("etag") for row in checkpoints}) > 1:
            result["_interest_force"] = True
        result["_manual_previous"] = {
            "next_collection_start": min(windows.values()),
            "completed_content_policy_version": policy, "completed_content_filter": "topics",
            "last_success_at": min((row["last_success_at"] for row in checkpoints if row.get("last_success_at")), default=None),
            "etag": next((row.get("etag") for row in checkpoints if row.get("etag")), None),
            "last_modified": next((row.get("last_modified") for row in checkpoints if row.get("last_modified")), None)}
    return result


def _update_interest_book(book, interests, sources, source_states, now, manual, request_url):
    from .interests import interest_fingerprint
    stamp = timestamp(now.isoformat())
    by_source = {state["source_id"]: state for state in source_states if _interest_source(next(
        (source for source in sources if source["id"] == state["source_id"]), {}))}
    for interest in interests:
        fingerprint = interest_fingerprint(interest)
        old = book["entries"].get(interest["id"], {})
        same = old.get("filter_fingerprint") == fingerprint
        entry = {"filter_fingerprint": fingerprint, "last_success_at": old.get("last_success_at") if same else None,
                 "sources": dict(old.get("sources", {})), "status": "failed"}
        completed = 0
        for source in sources:
            if not _interest_source(source):
                continue
            state = by_source.get(source["id"])
            if state is None:
                continue
            prior = entry["sources"].get(source["id"], {})
            prior_same = prior.get("filter_fingerprint") == fingerprint
            succeeded = state["status"] != "failed" and not state["truncated"]
            row = {"filter_fingerprint": fingerprint,
                   "last_success_at": stamp if succeeded else prior.get("last_success_at") if prior_same else None,
                   "next_collection_start": timestamp((now - timedelta(hours=48)).isoformat()) if succeeded else source["_interest_windows"][interest["id"]],
                   "etag": state.get("etag") if succeeded else prior.get("etag") if prior_same else None,
                   "last_modified": state.get("last_modified") if succeeded else prior.get("last_modified") if prior_same else None,
                   "status": state["status"] if not state["truncated"] else "partial_success",
                   "error_code": state.get("error_code")}
            entry["sources"][source["id"]] = row
            completed += int(succeeded)
        total = len(by_source)
        received = sum(state["status"] != "failed" for state in by_source.values())
        entry["status"] = "success" if total and completed == total else "partial_success" if received else "failed"
        if entry["status"] == "success":
            entry["last_success_at"] = stamp
        book["entries"][interest["id"]] = entry
    successes = sum(state["status"] != "failed" and not state["truncated"] for state in by_source.values())
    received = sum(state["status"] != "failed" for state in by_source.values())
    status = ("skipped" if not interests else "success" if by_source and successes == len(by_source)
              else "partial_success" if received else "failed")
    book["last_attempt"] = {"mode": "manual" if manual else "daily", "started_at": stamp, "completed_at": stamp,
                            "status": status, "request_url": request_url,
                            "error_code": "interest_sources_failed" if interests and not received else
                                          "interest_sources_incomplete" if interests and successes != len(by_source) else None}
    return book

def load_live_inputs(root, now=None):
    topics = read_json(root / "config/topics.json")["topics"]
    if len(topics) != 5 or {item["id"] for item in topics} != TOPIC_IDS or any(not item.get("enabled", True) for item in topics):
        raise ValueError("Exactly the five agreed topics must remain enabled")
    if any(not isinstance(item.get("name"), str) or not item["name"].strip() for item in topics):
        raise ValueError("Topic display names must be nonempty text")
    companies = read_json(root / "config/companies.json")["companies"]
    company_ids = [item.get("id") for item in companies]
    if (len(companies) != 13
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value) for value in company_ids)
            or len(set(company_ids)) != 13
            or not {f"company-{number}" for number in range(1, 11)} <= set(company_ids)
            or any(not isinstance(item.get("name"), str) or not item["name"].strip() for item in companies)
            or len({item["name"] for item in companies}) != 13):
        raise ValueError("Thirteen unique companies must preserve the original ten company IDs")
    for company in companies:
        if (not isinstance(company.get("enabled", True), bool) or not isinstance(company.get("aliases", []), list)
                or any(not isinstance(alias, str) or not alias.strip() for alias in company.get("aliases", []))):
            raise ValueError("Company aliases and enabled flags must be valid")
    rules = load_classification_rules(root / "config/classification_rules.json")
    topic_names = {item["id"]: item["name"] for item in topics}
    from .interests import load_interests
    interests = load_interests(root, now=now, active_only=True)
    if read_json(root / "reports/history.json")["reports"]:
        raise ValueError("Registered delivered reports must remain empty in this phase")
    settings = read_json(root / "config/pipeline.json")
    if settings.get("real_collection_enabled") is not True or settings.get("ai_enabled") is not False:
        raise ValueError("Live collection must be explicitly enabled; raw collection must not run AI")
    _schedule_metadata(settings)
    days, hours = settings.get("initial_lookback_days", 30), settings.get("overlap_hours", 48)
    if days != 30 or hours != 48:
        raise ValueError("This phase uses an initial 30-day window and a 48-hour overlap")
    sources = [item for item in read_json(root / "config/sources.json")["sources"] if item.get("enabled") is True]
    if not sources or len({item["id"] for item in sources}) != len(sources):
        raise ValueError("At least one uniquely identified source must be enabled")
    company_ids = {item["id"] for item in companies}
    for source in sources:
        if source.get("retention") != "metadata_only" or source.get("policy_status") != "verified":
            raise ValueError("Enabled sources must explicitly permit verified metadata-only retention")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", source["id"]):
            raise ValueError("Invalid source ID")
        safe_https_url(source["url"])
        safe_https_url(source["retention_url"])
        if not source.get("retention_basis") or not source.get("allowed_hosts"):
            raise ValueError("Source retention basis and allowed_hosts are required")
        if source.get("scope") == "company" and source.get("company_id") not in company_ids:
            raise ValueError("Company source must refer to a configured company")
        content_filter, _ = source_policy(source)
        if content_filter == "earnings_only" and source.get("scope") != "company":
            raise ValueError("DART earnings sources require a verified company issuer")
    sources = [_source_with_classification_context(source, rules, companies, topic_names) for source in sources]
    sources = [{**source, "_interests": interests} if _interest_source(source) else source for source in sources]
    return topic_names, companies, sources, settings


def normalize_record(record, source, topics, companies, start, now):
    """Return safe source metadata, independently from all downstream mart filters."""
    title = plain_title(record["title"])
    uri = canonical_url(safe_https_url(record["uri"], source["allowed_hosts"]))
    published = timestamp(record.get("published_at"))
    updated = timestamp(record.get("source_updated_at"))
    published_date = record.get("published_date")
    if published_date is not None:
        from datetime import date
        if not isinstance(published_date, str) or date.fromisoformat(published_date).isoformat() != published_date:
            raise ValueError("Invalid publication date")
        if date.fromisoformat(published_date) > now.astimezone(timezone(timedelta(hours=9))).date() + timedelta(days=1):
            raise ValueError("Publication date is implausibly in the future")
    known_times = [datetime.fromisoformat(value) for value in (published, updated) if value]
    # Date-only records remain date-only. UTC midnight is not invented as publication time.
    if known_times and max(known_times) > now + timedelta(days=1):
        raise ValueError("Publication timestamp is implausibly in the future")
    return {"uri": uri, "title": title, "published_at": published,
            "source_updated_at": updated, "published_date": published_date,
            "source_id": source["id"], "source_name": plain_title(source["name"]),
            "content_text": "", "content_level": "metadata_only",
            "retention_basis": plain_title(source["retention_basis"]),
            "retention_url": safe_https_url(source["retention_url"])}, None


def build_snapshot(db, topics, now):
    records = current_records(db)
    generation = "live-" + digest(now + "|" + "|".join(item["version_id"] for item in records))
    candidates, details = [], {}
    for item in records:
        issue_id = "issue-" + digest(item["uri"])
        reason = ("제목의 회사·업권 정보와 주제 키워드(" + ", ".join(item["matched_keywords"]) +
                  ")를 기준으로 분류했습니다. 중요도·시사점은 아직 분석하지 않았습니다.")
        if item["content_filter"] == "earnings_only":
            reason = ("공시 서식명(" + ", ".join(item["matched_keywords"]) +
                      ")이 잠정·실제 실적 수집 범위에 해당합니다. 본문·실적 수치·중요도는 아직 분석하지 않았습니다.")
        candidate = {"issue_id": issue_id, "title": item["title"], "company": item["company"],
                     "company_ids": item["company_ids"], "companies": item["companies"],
                     "topic_id": item["topic_id"], "topic": topics[item["topic_id"]],
                     "priority": "unranked", "report_status": "no_history", "why_now": reason,
                     "bullets": [], "evidence_ids": [item["version_id"]],
                     "detail_path": f"data/issues/{issue_id}.json", "is_demo": False,
                     "analysis_status": "pending_ai", "evidence_confidence": "metadata_only",
                     "source_name": item["source_name"], "published_at": item["published_at"],
                     "published_date": item["published_date"], "fetched_at": item["fetched_at"],
                     "matched_keywords": item["matched_keywords"], "matched_topic_ids": item["matched_topic_ids"],
                     "tags": item.get("tags", []), "event_stage": item.get("event_stage"),
                     "classification_rules_version": item.get("classification_rules_version")}
        candidate.update(content_filter=item["content_filter"], content_policy_version=item["content_policy_version"])
        versions = db.execute("SELECT COUNT(*) FROM live_article_versions WHERE article_id=?", (item["article_id"],)).fetchone()[0]
        evidence = {key: item[key] for key in ("title", "published_at", "published_date", "source_updated_at",
                    "fetched_at", "content_level", "content_text", "source_id", "source_name", "retention_basis", "retention_url")}
        evidence.update(evidence_id=item["version_id"], source_url=item["uri"], is_demo=False)
        details[issue_id] = {**candidate, "schema_version": "1.0", "generation_id": generation,
                            "change_summary": (f"같은 URL의 메타데이터 {versions}개 버전을 보존했습니다. " if versions > 1 else "") +
                                "본문·기보고 대비 변화는 분석하지 않았습니다.",
                            "last_report": None, "evidence": [evidence]}
        candidates.append(candidate)
    candidates.sort(key=lambda item: (item["published_at"] or item["published_date"] or "", item["fetched_at"], item["issue_id"]), reverse=True)
    iso = datetime.fromisoformat(now).astimezone(timezone(timedelta(hours=9))).isocalendar()
    latest = {"schema_version": "1.0", "generation_id": generation, "generated_at": now,
              "report_week": f"{iso.year}-W{iso.week:02d}", "is_demo": False,
              "topics": [{"id": topic_id, "name": name} for topic_id, name in topics.items()],
              "candidates": candidates}
    history = {"schema_version": "1.0", "generation_id": generation, "is_demo": False, "reports": []}
    return latest, details, history


def _public_source(state):
    return {key: value for key, value in state.items()
            if key not in {"etag", "last_modified"} and not key.startswith("_")}


def source_inventory(db, source_ids):
    """Latest observed attempt per enabled source, independent of capture scope."""
    latest = {}
    for row in db.execute("SELECT source_id,state_json FROM live_source_runs ORDER BY source_run_id"):
        if row["source_id"] in source_ids:
            latest[row["source_id"]] = _public_source(json.loads(row["state_json"]))
    return [latest[source_id] for source_id in source_ids if source_id in latest]


def _collect_one(collector, source, previous, topics, companies, now):
    content_filter, policy_version = source_policy(source)
    # Capture and classification have independent versions. Only the one-time
    # raw-store migration may reopen 30 days; edits to interests/rules reuse raw.
    backfill = previous.get("completed_capture_version") != CAPTURE_VERSION
    start = (datetime.fromisoformat(previous["next_collection_start"]) if previous.get("next_collection_start") else
             datetime.fromisoformat(previous["last_success_at"]) - timedelta(hours=48)
             if previous.get("last_success_at") else now - timedelta(days=30))
    if backfill:
        start = (datetime.fromisoformat(previous["policy_backfill_start"])
                 if previous.get("capture_version") == CAPTURE_VERSION and previous.get("policy_backfill_start")
                 else now - timedelta(days=30))
    stamp = timestamp(now.isoformat())
    state = {"source_id": source["id"], "source_name": plain_title(source["name"]),
             "status": "failed", "last_attempt_at": stamp, "last_success_at": previous.get("last_success_at"),
             "fetched_count": 0, "accepted_count": 0, "inserted_versions": 0, "duplicate_count": 0,
             "raw_saved_count": 0, "mart_included_count": 0, "mart_excluded_count": 0,
             "capture_version": CAPTURE_VERSION, "completed_capture_version": previous.get("completed_capture_version"),
             "rejected_count": 0, "outside_scope_count": 0, "outside_window_count": 0, "outside_content_filter_count": 0,
             "error_code": None, "error_message": None, "coverage_start": timestamp(start.isoformat()),
             "coverage_end": stamp, "coverage_note": COVERAGE_NOTE,
             "truncated": False, "total_count": None,
             "coverage_watermark_at": previous.get("coverage_watermark_at"),
             "next_collection_start": timestamp(start.isoformat()),
             "etag": None if backfill else previous.get("etag"),
             "last_modified": None if backfill else previous.get("last_modified"),
             "content_filter": content_filter, "content_policy_version": policy_version,
             "completed_content_policy_version": previous.get("completed_content_policy_version"),
             "completed_content_filter": previous.get("completed_content_filter"),
             "policy_backfill_pending": backfill,
             "policy_backfill_start": timestamp(start.isoformat()) if backfill else None}
    force_representation = backfill
    request_previous = {**previous, "etag": None, "last_modified": None} if force_representation else previous
    state["coverage_start"] = timestamp(start.isoformat())
    try:
        result = collector({**source, "collection_start": state["coverage_start"], "collection_end": stamp}, now=now, previous_state=request_previous or None)
    except Exception:
        result = {"status": "failed", "error_code": "collector_exception"}
    if not isinstance(result, dict) or result.get("status") not in {"success", "not_modified", "failed"}:
        result = {"status": "failed", "error_code": "invalid_response"}
    if force_representation and result["status"] == "not_modified":
        # A newly widened lookback must receive the representation, not reuse a
        # validator established under an older filter or shorter collection window.
        result = {"status": "failed", "error_code": "invalid_response"}
    if result["status"] == "failed":
        code = result.get("error_code", "collector_exception")
        state["error_code"] = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,60}", code) else "collector_exception"
        state["error_message"] = ERROR_MESSAGES.get(state["error_code"], "출처 수집에 실패했습니다. 이전 정상 자료를 보존하고 다음 실행에서 재시도합니다.")
        return state, []
    records = result.get("records", [])
    fetched = result.get("fetched_count", len(records) if isinstance(records, list) else 0)
    if not isinstance(records, list) or not isinstance(fetched, int) or fetched < 0 or result["status"] == "not_modified" and records:
        state.update(error_code="invalid_response", error_message=ERROR_MESSAGES["invalid_response"])
        return state, []
    accepted = []
    state["fetched_count"] = fetched
    for record in records:
        try:
            normalized, excluded = normalize_record(record, source, topics, companies, start, now)
        except (KeyError, ValueError, TypeError):
            state["rejected_count"] += 1
            continue
        if normalized:
            accepted.append(normalized)
            known = [datetime.fromisoformat(value) for value in
                     (normalized["published_at"], normalized["source_updated_at"]) if value]
            if (max(known) < start if known else normalized.get("published_date") and normalized["published_date"] < start.date().isoformat()):
                state["outside_window_count"] += 1
            route = classify_for_source(normalized["title"], source, companies, topics)
            if route is None:
                state["outside_content_filter_count" if content_filter == "earnings_only" else "outside_scope_count"] += 1
        else:
            state[excluded + "_count"] += 1
    if records and state["rejected_count"] == len(records):
        state.update(error_code="invalid_records", error_message=ERROR_MESSAGES["invalid_records"])
        return state, []
    truncated = result.get("truncated") is True
    state.update(status=result["status"], last_success_at=stamp, accepted_count=len(accepted), raw_saved_count=len(accepted), truncated=truncated)
    if isinstance(result.get("total_count"), int) and result["total_count"] >= 0:
        state["total_count"] = result["total_count"]
    if truncated:
        state["coverage_note"] = "출처 페이지 처리 한도에 도달했습니다. 누락 가능성이 있어 다음 실행에서도 같은 시작 범위부터 재확인합니다. " + COVERAGE_NOTE
    else:
        state["coverage_watermark_at"] = stamp
        state["next_collection_start"] = timestamp((now - timedelta(hours=48)).isoformat())
        state["completed_capture_version"] = CAPTURE_VERSION
        state["completed_content_policy_version"] = policy_version
        state["completed_content_filter"] = content_filter
        state["policy_backfill_pending"] = False
        state["policy_backfill_start"] = None
    for key in ("etag", "last_modified"):
        value = result.get(key)
        if result["status"] == "success":
            # A new representation without a validator supersedes an old one.
            state[key] = None
        if value is not None and isinstance(value, str) and len(value) <= 2000 and not any(ord(char) < 32 for char in value):
            state[key] = value
    return state, accepted


def _replace(stage, destinations):
    replacements = []
    try:
        for index, (source, target) in enumerate(destinations):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = stage / f"previous-{index}"
            if target.exists():
                os.replace(target, backup)
            replacements.append((target, backup))
            os.replace(source, target)
    except OSError:
        for target, backup in reversed(replacements):
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if backup.exists():
                os.replace(backup, target)
        raise


def run_collection(root, collector=None, now=None, interests_only=False, request_url=None, *, source_mode="all", rebuild_only=False):
    root = Path(root).resolve()
    if source_mode not in {"all", "rss"} or type(rebuild_only) is not bool:
        raise ValueError("Invalid capture mode")
    if type(interests_only) is not bool:
        raise ValueError("Interest-only collection must be explicit")
    if request_url is not None and not re.fullmatch(r"https://github\.com/iesilder/kw-plan-weekly-trend/issues/[1-9][0-9]*", request_url):
        raise ValueError("Invalid interest collection request URL")
    now = datetime.fromisoformat(timestamp((now or datetime.now(timezone.utc)).isoformat()))
    from .reviews import load_reviews
    from .interests import load_interests
    reviews = load_reviews(root)
    topics, companies, sources, settings = load_live_inputs(root, now=now)
    interests = load_interests(root, now=now, active_only=True)
    no_work = rebuild_only or interests_only and not interests
    book = _interest_book(root)
    sources = [_interest_context(source, interests, book, now, interests_only)
               if _interest_source(source) and interests else source for source in sources]
    policy_sources = sources
    if interests_only:
        sources = [source for source in sources if _interest_source(source)]
        if not sources and not no_work:
            raise ValueError("No approved RSS source is available for interest collection")
        if no_work:
            sources = []
    if source_mode == "rss":
        sources = [source for source in sources if source.get("type") == "rss"]
    if rebuild_only:
        sources = []
    if not sources and not no_work:
        raise ValueError("No enabled source is available for this capture mode")
    if collector is None:
        from .collectors import collect_source
        collector = collect_source
    stamp = timestamp(now.isoformat())
    path = root / "data/raw.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="weekly-live-", dir=root) as temporary:
        stage = Path(temporary)
        stage_db = stage / "raw.sqlite3"
        if path.exists():
            with closing(sqlite3.connect(path)) as source_db, closing(sqlite3.connect(stage_db)) as target:
                source_db.backup(target)
        db = connect(stage_db)
        try:
            old_sources = previous_sources(db)
            policy_changed = apply_policies(db, policy_sources, companies, topics)
            old_latest_path = root / "site/data/latest.json"
            old_status_path = root / "site/data/status.json"
            previous_published = (old_status_path.exists() and read_json(old_status_path).get("published_at") is not None)
            # An empty earlier snapshot has no article policy rows to change,
            # but configured topic labels still need to migrate on a failed fetch.
            if previous_published and old_latest_path.exists():
                old_latest = read_json(old_latest_path)
                if (old_latest.get("is_demo") is False and old_latest.get("topics") !=
                        [{"id": topic_id, "name": name} for topic_id, name in topics.items()]):
                    policy_changed = True
            run_id = db.execute("INSERT INTO live_collection_runs(started_at,finished_at,status,execution_mode) VALUES(?,?,'running',?)", (stamp, stamp, "mart_rebuild" if rebuild_only else "collection")).lastrowid
            source_states = []
            for source in sources:
                # Manual requests can reuse the latest complete raw representation,
                # regardless of which interest was active then. Their capture cursor
                # never depends on an interest fingerprint or changes normal state.
                previous = old_sources.get(source["id"], {})
                if interests_only:
                    latest_source = db.execute("SELECT state_json FROM live_source_runs WHERE source_id=? ORDER BY source_run_id DESC LIMIT 1",
                                               (source["id"],)).fetchone()
                    if latest_source is not None:
                        previous = json.loads(latest_source[0])
                state, records = _collect_one(collector, source, previous, topics, companies, now)
                if state["status"] != "failed":
                    state["inserted_versions"], state["duplicate_count"] = ingest(db, records, stamp)
                apply_policies(db, policy_sources, companies, topics)
                active_ids = {row[0] for row in db.execute("SELECT article_id FROM live_article_policy WHERE active=1")}
                state["mart_included_count"] = sum("article-" + digest(record["uri"]) in active_ids for record in records)
                state["mart_excluded_count"] = state["accepted_count"] - state["mart_included_count"]
                if interests_only:
                    # An ad hoc interest pass must not advance the normal daily
                    # source cursor, conditional headers, or DART checkpoints.
                    db.execute("INSERT INTO live_source_runs(run_id,source_id,state_json) VALUES(?,?,?)",
                               (run_id, state["source_id"], json.dumps(state, ensure_ascii=False, sort_keys=True)))
                else:
                    save_source(db, run_id, state)
                source_states.append(state)
            apply_policies(db, policy_sources, companies, topics)
            validate_policies(db, policy_sources, companies, topics)
            succeeded = sum(item["status"] != "failed" for item in source_states)
            incomplete_interest_pass = interests_only and any(state["truncated"] for state in source_states)
            outcome = ("success" if no_work or succeeded == len(sources) and not incomplete_interest_pass
                       else "partial_success" if succeeded else "failed")
            collection_ok = bool(succeeded) or no_work
            totals = {key: sum(item[key] for item in source_states) for key in ("fetched_count", "accepted_count", "inserted_versions", "duplicate_count")}
            db.execute("""UPDATE live_collection_runs SET status=?,fetched_count=?,accepted_count=?,inserted_versions=?,duplicate_count=?
                WHERE run_id=?""", (outcome, *totals.values(), run_id))
            policy_filtered = bool(policy_changed and not succeeded)
            db.execute("UPDATE live_collection_runs SET policy_filtered=? WHERE run_id=?", (int(policy_filtered), run_id))
            db.commit()
            retained = False
            if not collection_ok and not policy_filtered and previous_published and old_latest_path.exists() and read_json(old_latest_path).get("is_demo") is False:
                # Verify the prior published snapshot against unchanged live article state.
                latest, details, history = _read_snapshot(root)
                validate_snapshot(db, topics, latest, details, history)
                retained = True
            else:
                latest, details, history = build_snapshot(db, topics, stamp)
            from .finance_all import build as build_finance, markers as finance_markers, validate as validate_finance
            finance = build_finance(db, root, stamp, policy_sources)
            previous_finance = root / "site/data/finance-all.json"
            if previous_finance.exists():
                prior = read_json(previous_finance)
                if prior.get("generation_id") == finance["generation_id"]:
                    finance = build_finance(db, root, prior.get("generated_at"), policy_sources)
            validate_finance(db, root, finance, policy_sources)
            generation = latest["generation_id"]
            successful_run = db.execute("SELECT MAX(finished_at) FROM live_collection_runs WHERE status IN ('success','partial_success')").fetchone()[0]
            status = {"schema_version": "1.0", "generation_id": generation, "published_generation_id": generation,
                      "run_generation_id": "run-" + digest(stamp + str(run_id)), "is_demo": False,
                      "last_run_at": stamp, "last_successful_run_at": successful_run,
                      "published_at": latest["generated_at"] if collection_ok or retained or policy_filtered else None,
                      **counts(db), "issue_count": len(latest["candidates"]), **totals, **finance_markers(finance),
                      "capture_schema_version": CAPTURE_VERSION,
                      "execution_mode": "mart_rebuild" if rebuild_only else "collection",
                      "collection_performed": bool(source_states), "source_mode": source_mode,
                      "analysis_mode": "pending_ai", "collection_mode": "public_metadata",
                      "collection_ok": collection_ok, "collection_status": outcome,
                      "publication_status": "updated" if collection_ok else "filtered" if policy_filtered else "retained" if retained else "unavailable",
                      "policy_filtered": policy_filtered,
                      "interests_count": len(interests), "active_interest_ids": [item["id"] for item in interests],
                      "report_count": 0, "real_collection_enabled": True, "interests_only": interests_only,
                      "ai_enabled": False, **_schedule_metadata(settings),
                      "initial_lookback_days": 30, "overlap_hours": 48, "coverage_note": COVERAGE_NOTE,
                      "sources": [_public_source(item) for item in source_states],
                      "source_inventory": source_inventory(db, [source["id"] for source in policy_sources])}
            if not rebuild_only:
                book = _update_interest_book(book, interests, sources, source_states, now, interests_only, request_url)
            validate_snapshot(db, topics, latest, details, history)
            validate_status(db, latest, status, [source["id"] for source in policy_sources])
        finally:
            db.close()
        mart, state_path = stage / "mart", stage / "state"
        write_json(mart / "latest.json", latest)
        write_json(mart / "finance-all.json", finance)
        write_json(mart / "status.json", status)
        write_json(mart / "report-history.json", history)
        write_json(mart / "reviews.json", reviews)
        from .report_history import load as load_reports
        write_json(mart / "delivered-reports.json", load_reports(root))
        write_json(state_path / "issues.json", {"schema_version": "1.0", "is_demo": False,
                   "generation_id": generation, "issues": latest["candidates"]})
        write_json(state_path / "run_status.json", status)
        for issue_id, detail in details.items():
            write_json(mart / "issues" / f"{issue_id}.json", detail)
            write_json(state_path / "analyses" / f"{issue_id}.json", detail)
        from .ai import stage_analysis
        stage_analysis(root, latest, details, state_path, mart, now=now)
        from .article_analysis import stage_analysis as stage_articles
        stage_articles(root, latest, details, state_path, mart, now=now)
        from .interests import build_public as build_interests
        write_json(mart / "interests.json", build_interests(root, latest, details, now=now, collection=book))
        book_stage = stage / "interests-collection.json"
        write_json(book_stage, book)
        _replace(stage, [(stage_db, path), (state_path, root / "state"), (mart, root / "site/data"),
                         (book_stage, root / "data/interests-collection.json")])
    return {"ok": True, "interests_only": interests_only, **status}


def _read_snapshot(root):
    return (read_json(root / "site/data/latest.json"),
            {path.stem: read_json(path) for path in (root / "site/data/issues").glob("*.json")},
            read_json(root / "site/data/report-history.json"))


def validate_snapshot(db, topics, latest, details, history):
    timestamp(latest["generated_at"])
    expected = build_snapshot(db, topics, latest["generated_at"])
    if (latest, details, history) != expected:
        raise ValueError("Live SQLite, state, and published snapshot differ")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("SQLite integrity failed")


def validate_status(db, latest, status, source_ids=None):
    # Saved snapshots may predate the change from 08:00 to 11:00 KST.
    _schedule_metadata(status, allow_legacy=True)
    if status.get("capture_schema_version") not in {None, CAPTURE_VERSION}:
        raise ValueError("Unsupported raw capture schema")
    expected_counts = counts(db)
    raw_fields = {"raw_article_count", "raw_version_count", "mart_article_count", "mart_excluded_count", "mart_exclusions"}
    if any(status.get(key) != value for key, value in expected_counts.items()
           if status.get("capture_schema_version") == CAPTURE_VERSION or key not in raw_fields):
        raise ValueError("Live SQLite/status count mismatch")
    if (status.get("schema_version") != "1.0" or status.get("is_demo") is not False or
            status.get("real_collection_enabled") is not True or status.get("ai_enabled") is not False or
            status.get("analysis_mode") != "pending_ai" or status.get("collection_mode") != "public_metadata"):
        raise ValueError("Live status must not claim a demo or AI analysis")
    if status.get("generation_id") != latest["generation_id"] or status.get("published_generation_id") != latest["generation_id"]:
        raise ValueError("Live status points at a different published generation")
    interest_count = status.get("interests_count")
    interest_ids = status.get("active_interest_ids", [])
    if (status.get("issue_count") != len(latest["candidates"]) or status.get("report_count") != 0
            or type(interest_count) is not int or not 0 <= interest_count <= 20
            or not isinstance(interest_ids, list)
            or any(not isinstance(value, str) or not re.fullmatch(r"interest-[1-9][0-9]*", value) for value in interest_ids)
            or len(set(interest_ids)) != len(interest_ids)
            or len(interest_ids) != interest_count):
        raise ValueError("Live status contains incorrect counts or fabricated history")
    run = db.execute("SELECT * FROM live_collection_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if run is None or run["status"] != status.get("collection_status") or run["finished_at"] != status.get("last_run_at"):
        raise ValueError("Run status differs from SQLite")
    if status.get("collection_ok") != (run["status"] != "failed"):
        raise ValueError("Failed collection cannot claim success")
    stored_sources = [_public_source(json.loads(row[0])) for row in db.execute(
        "SELECT state_json FROM live_source_runs WHERE run_id=? ORDER BY source_run_id", (run["run_id"],))]
    execution_mode = run["execution_mode"] if "execution_mode" in run.keys() else "collection"
    no_work = (status.get("interests_only") is True and status["interests_count"] == 0 or
               execution_mode == "mart_rebuild") and run["status"] == "success"
    if status.get("capture_schema_version") == CAPTURE_VERSION:
        if (status.get("execution_mode") != execution_mode or
                status.get("collection_performed") != bool(stored_sources)):
            raise ValueError("Execution mode differs from stored run")
    if (not stored_sources and not no_work) or status.get("sources") != stored_sources:
        raise ValueError("Source status differs from SQLite")
    if status.get("capture_schema_version") == CAPTURE_VERSION:
        for source in stored_sources:
            keys = ("raw_saved_count", "mart_included_count", "mart_excluded_count", "accepted_count", "inserted_versions", "duplicate_count")
            if (any(type(source.get(key)) is not int or source[key] < 0 for key in keys) or
                    source["raw_saved_count"] != source["accepted_count"] or
                    source["mart_included_count"] + source["mart_excluded_count"] != source["raw_saved_count"] or
                    source["inserted_versions"] + source["duplicate_count"] != source["accepted_count"]):
                raise ValueError("Raw and mart source counts are inconsistent")
    if status.get("capture_schema_version") == CAPTURE_VERSION and source_ids is not None:
        if status.get("source_inventory") != source_inventory(db, source_ids):
            raise ValueError("Source inventory differs from SQLite")
    latest_success = db.execute("SELECT MAX(finished_at) FROM live_collection_runs WHERE status IN ('success','partial_success')").fetchone()[0]
    if status.get("last_successful_run_at") != latest_success:
        raise ValueError("Last successful run differs from SQLite")
    if status.get("policy_filtered") != bool(run["policy_filtered"]):
        raise ValueError("Policy filtering status differs from SQLite")
    publication = "updated" if status["collection_ok"] else "filtered" if run["policy_filtered"] else "retained" if latest_success else "unavailable"
    if status.get("publication_status") != publication:
        raise ValueError("Publication status must distinguish preserved and unavailable data")
    expected_published_at = latest["generated_at"] if publication != "unavailable" else None
    if status.get("published_at") != expected_published_at:
        raise ValueError("Published timestamp mismatch")
    for key in ("fetched_count", "accepted_count", "inserted_versions", "duplicate_count"):
        if status.get(key) != run[key]:
            raise ValueError("Run counts differ from SQLite")


def verify_live(root):
    from .report_history import verify as verify_reports
    verify_reports(root)
    root = Path(root).resolve()
    from .reviews import validate_mirror
    validate_mirror(root)
    from .ai import check as check_ai
    check_ai(root)
    from .article_analysis import check as check_articles
    check_articles(root)
    latest, details, history = _read_snapshot(root)
    status = read_json(root / "site/data/status.json")
    moment = datetime.fromisoformat(status["last_run_at"])
    topics, companies, sources, _ = load_live_inputs(root, now=moment)
    if "active_interest_ids" in status:
        from .interests import load_interests
        expected = [item["id"] for item in load_interests(root, now=moment, active_only=True)]
        if status["active_interest_ids"] != expected:
            raise ValueError("Published interest count differs from its applied configuration")
    from .interests import check as check_interests
    check_interests(root)
    path = root / "data/raw.sqlite3"
    if not path.is_file():
        raise ValueError("SQLite store is missing")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        validate_policies(db, sources, companies, topics)
        validate_snapshot(db, topics, latest, details, history)
        validate_status(db, latest, status, [source["id"] for source in sources])
    from .finance_all import check as check_finance
    check_finance(root)
    if read_json(root / "state/run_status.json") != status:
        raise ValueError("Live state and mart status differ")
    if read_json(root / "state/issues.json") != {"schema_version": "1.0", "is_demo": False,
            "generation_id": latest["generation_id"], "issues": latest["candidates"]}:
        raise ValueError("Live issue state differs from mart")
    analyses = {path.stem: read_json(path) for path in (root / "state/analyses").glob("*.json")}
    if analyses != details:
        raise ValueError("Live analysis state differs from mart")
    return {"ok": True, "collection_ok": status["collection_ok"], "collection_status": status["collection_status"],
            "publication_status": status["publication_status"], "generation_id": latest["generation_id"],
            **{key: status[key] for key in ("article_count", "version_count", "issue_count", "run_count")}}

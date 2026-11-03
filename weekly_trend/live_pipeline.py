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

from .classification import classify_for_source, plain_title, safe_https_url, source_policy
from .live_storage import (apply_policies, connect, counts, current_records, ingest,
                           previous_sources, save_source, timestamp, validate_policies)
from .pipeline import TOPIC_IDS, read_json, write_json
from .storage import canonical_url, digest

COVERAGE_NOTE = "현재 피드·목록에 남아 있는 항목만 확인하며, 해당 기간 전체 수집을 보장하지 않습니다."
ERROR_MESSAGES = {
    "collector_exception": "출처 수집 중 오류가 발생했습니다. 다음 실행에서 재시도합니다.",
    "invalid_response": "출처 응답 형식을 검증하지 못했습니다.",
    "invalid_records": "출처 항목이 저장 안전성 검사를 통과하지 못했습니다.",
}


def load_live_inputs(root):
    topics = read_json(root / "config/topics.json")["topics"]
    if len(topics) != 5 or {item["id"] for item in topics} != TOPIC_IDS or any(not item.get("enabled", True) for item in topics):
        raise ValueError("Exactly the five agreed topics must remain enabled")
    companies = read_json(root / "config/companies.json")["companies"]
    if len(companies) != 10 or len({item["id"] for item in companies}) != 10:
        raise ValueError("The ten configured companies must remain present")
    if read_json(root / "config/interests.json")["interests"] or read_json(root / "reports/history.json")["reports"]:
        raise ValueError("Executive interests and registered reports must remain empty in this phase")
    settings = read_json(root / "config/pipeline.json")
    if (settings.get("real_collection_enabled") is not True or settings.get("ai_enabled") is not False
            or settings.get("schedule_enabled") is not False):
        raise ValueError("Live collection must be explicitly enabled; AI and schedules must remain disabled")
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
    return {item["id"]: item["name"] for item in topics}, companies, sources, settings


def normalize_record(record, source, topics, companies, start, now):
    """Return a safe scoped record or a counted exclusion; never copy arbitrary fields."""
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
    if known_times and max(known_times) < start:
        return None, "outside_window"
    if not known_times and published_date and published_date < start.date().isoformat():
        return None, "outside_window"
    if known_times and max(known_times) > now + timedelta(days=1):
        raise ValueError("Publication timestamp is implausibly in the future")
    route = classify_for_source(title, source, companies, topics)
    if route is None:
        return None, "outside_content_filter" if source_policy(source)[0] == "earnings_only" else "outside_scope"
    return {"uri": uri, "title": title, "published_at": published,
            "source_updated_at": updated, "published_date": published_date,
            "source_id": source["id"], "source_name": plain_title(source["name"]),
            "content_text": "", "content_level": "metadata_only",
            "retention_basis": plain_title(source["retention_basis"]),
            "retention_url": safe_https_url(source["retention_url"]), **route}, None


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
                     "matched_keywords": item["matched_keywords"], "matched_topic_ids": item["matched_topic_ids"]}
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
              "report_week": f"{iso.year}-W{iso.week:02d}", "is_demo": False, "candidates": candidates}
    history = {"schema_version": "1.0", "generation_id": generation, "is_demo": False, "reports": []}
    return latest, details, history


def _public_source(state):
    return {key: value for key, value in state.items() if key not in {"etag", "last_modified"}}


def _collect_one(collector, source, previous, topics, companies, now):
    content_filter, policy_version = source_policy(source)
    backfill = (previous.get("completed_content_policy_version") != policy_version or
                previous.get("completed_content_filter") != content_filter)
    start = (datetime.fromisoformat(previous["next_collection_start"]) if previous.get("next_collection_start") else
             datetime.fromisoformat(previous["last_success_at"]) - timedelta(hours=48)
             if previous.get("last_success_at") else now - timedelta(days=30))
    if backfill:
        start = (datetime.fromisoformat(previous["policy_backfill_start"])
                 if previous.get("content_policy_version") == policy_version and previous.get("policy_backfill_start")
                 else now - timedelta(days=30))
    stamp = timestamp(now.isoformat())
    state = {"source_id": source["id"], "source_name": plain_title(source["name"]),
             "status": "failed", "last_attempt_at": stamp, "last_success_at": previous.get("last_success_at"),
             "fetched_count": 0, "accepted_count": 0, "inserted_versions": 0, "duplicate_count": 0,
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
    request_previous = {**previous, "etag": None, "last_modified": None} if backfill else previous
    try:
        result = collector({**source, "collection_start": state["coverage_start"], "collection_end": stamp}, now=now, previous_state=request_previous or None)
    except Exception:
        result = {"status": "failed", "error_code": "collector_exception"}
    if not isinstance(result, dict) or result.get("status") not in {"success", "not_modified", "failed"}:
        result = {"status": "failed", "error_code": "invalid_response"}
    if backfill and result["status"] == "not_modified":
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
        else:
            state[excluded + "_count"] += 1
    if records and state["rejected_count"] == len(records):
        state.update(error_code="invalid_records", error_message=ERROR_MESSAGES["invalid_records"])
        return state, []
    truncated = result.get("truncated") is True
    state.update(status=result["status"], last_success_at=stamp, accepted_count=len(accepted), truncated=truncated)
    if isinstance(result.get("total_count"), int) and result["total_count"] >= 0:
        state["total_count"] = result["total_count"]
    if truncated:
        state["coverage_note"] = "출처 페이지 처리 한도에 도달했습니다. 누락 가능성이 있어 다음 실행에서도 같은 시작 범위부터 재확인합니다. " + COVERAGE_NOTE
    else:
        state["coverage_watermark_at"] = stamp
        state["next_collection_start"] = timestamp((now - timedelta(hours=48)).isoformat())
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


def run_collection(root, collector=None, now=None):
    root = Path(root).resolve()
    topics, companies, sources, settings = load_live_inputs(root)
    if collector is None:
        from .collectors import collect_source
        collector = collect_source
    now = datetime.fromisoformat(timestamp((now or datetime.now(timezone.utc)).isoformat()))
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
            policy_changed = apply_policies(db, sources, companies, topics)
            run_id = db.execute("INSERT INTO live_collection_runs(started_at,finished_at,status) VALUES(?,?,'running')", (stamp, stamp)).lastrowid
            source_states = []
            for source in sources:
                state, records = _collect_one(collector, source, old_sources.get(source["id"], {}), topics, companies, now)
                if state["status"] != "failed":
                    state["inserted_versions"], state["duplicate_count"] = ingest(db, records, stamp)
                save_source(db, run_id, state)
                source_states.append(state)
            apply_policies(db, sources, companies, topics)
            validate_policies(db, sources, companies, topics)
            succeeded = sum(item["status"] != "failed" for item in source_states)
            outcome = "success" if succeeded == len(sources) else "partial_success" if succeeded else "failed"
            totals = {key: sum(item[key] for item in source_states) for key in ("fetched_count", "accepted_count", "inserted_versions", "duplicate_count")}
            db.execute("""UPDATE live_collection_runs SET status=?,fetched_count=?,accepted_count=?,inserted_versions=?,duplicate_count=?
                WHERE run_id=?""", (outcome, *totals.values(), run_id))
            policy_filtered = bool(policy_changed and not succeeded)
            db.execute("UPDATE live_collection_runs SET policy_filtered=? WHERE run_id=?", (int(policy_filtered), run_id))
            db.commit()
            retained = False
            old_latest_path = root / "site/data/latest.json"
            old_status_path = root / "site/data/status.json"
            previous_published = (old_status_path.exists() and read_json(old_status_path).get("published_at") is not None)
            if not succeeded and not policy_filtered and previous_published and old_latest_path.exists() and read_json(old_latest_path).get("is_demo") is False:
                # Verify the prior published snapshot against unchanged live article state.
                latest, details, history = _read_snapshot(root)
                validate_snapshot(db, topics, latest, details, history)
                retained = True
            else:
                latest, details, history = build_snapshot(db, topics, stamp)
            generation = latest["generation_id"]
            successful_run = db.execute("SELECT MAX(finished_at) FROM live_collection_runs WHERE status IN ('success','partial_success')").fetchone()[0]
            status = {"schema_version": "1.0", "generation_id": generation, "published_generation_id": generation,
                      "run_generation_id": "run-" + digest(stamp + str(run_id)), "is_demo": False,
                      "last_run_at": stamp, "last_successful_run_at": successful_run,
                      "published_at": latest["generated_at"] if succeeded or retained or policy_filtered else None,
                      **counts(db), "issue_count": len(latest["candidates"]), **totals,
                      "analysis_mode": "pending_ai", "collection_mode": "public_metadata",
                      "collection_ok": bool(succeeded), "collection_status": outcome,
                      "publication_status": "updated" if succeeded else "filtered" if policy_filtered else "retained" if retained else "unavailable",
                      "policy_filtered": policy_filtered,
                      "interests_count": 0, "report_count": 0, "real_collection_enabled": True,
                      "ai_enabled": False, "schedule_enabled": settings.get("schedule_enabled", False),
                      "initial_lookback_days": 30, "overlap_hours": 48, "coverage_note": COVERAGE_NOTE,
                      "sources": [_public_source(item) for item in source_states]}
            validate_snapshot(db, topics, latest, details, history)
            validate_status(db, latest, status)
        finally:
            db.close()
        mart, state_path = stage / "mart", stage / "state"
        write_json(mart / "latest.json", latest)
        write_json(mart / "status.json", status)
        write_json(mart / "report-history.json", history)
        write_json(state_path / "issues.json", {"schema_version": "1.0", "is_demo": False,
                   "generation_id": generation, "issues": latest["candidates"]})
        write_json(state_path / "run_status.json", status)
        for issue_id, detail in details.items():
            write_json(mart / "issues" / f"{issue_id}.json", detail)
            write_json(state_path / "analyses" / f"{issue_id}.json", detail)
        _replace(stage, [(stage_db, path), (state_path, root / "state"), (mart, root / "site/data")])
    return {"ok": True, **status}


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


def validate_status(db, latest, status):
    expected_counts = counts(db)
    if any(status.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("Live SQLite/status count mismatch")
    if (status.get("schema_version") != "1.0" or status.get("is_demo") is not False or
            status.get("real_collection_enabled") is not True or status.get("ai_enabled") is not False or
            status.get("schedule_enabled") is not False or
            status.get("analysis_mode") != "pending_ai" or status.get("collection_mode") != "public_metadata"):
        raise ValueError("Live status must not claim a demo or AI analysis")
    if status.get("generation_id") != latest["generation_id"] or status.get("published_generation_id") != latest["generation_id"]:
        raise ValueError("Live status points at a different published generation")
    if status.get("issue_count") != len(latest["candidates"]) or status.get("report_count") != 0 or status.get("interests_count") != 0:
        raise ValueError("Live status contains incorrect counts or fabricated history")
    run = db.execute("SELECT * FROM live_collection_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if run is None or run["status"] != status.get("collection_status") or run["finished_at"] != status.get("last_run_at"):
        raise ValueError("Run status differs from SQLite")
    if status.get("collection_ok") != (run["status"] != "failed"):
        raise ValueError("Failed collection cannot claim success")
    stored_sources = [_public_source(json.loads(row[0])) for row in db.execute(
        "SELECT state_json FROM live_source_runs WHERE run_id=? ORDER BY source_run_id", (run["run_id"],))]
    if not stored_sources or status.get("sources") != stored_sources:
        raise ValueError("Source status differs from SQLite")
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
    root = Path(root).resolve()
    topics, companies, sources, _ = load_live_inputs(root)
    latest, details, history = _read_snapshot(root)
    status = read_json(root / "site/data/status.json")
    path = root / "data/raw.sqlite3"
    if not path.is_file():
        raise ValueError("SQLite store is missing")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        validate_policies(db, sources, companies, topics)
        validate_snapshot(db, topics, latest, details, history)
        validate_status(db, latest, status)
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

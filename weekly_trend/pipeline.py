"""Phase 1: authored fixtures -> SQLite -> rule-based demo state -> static mart."""

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .storage import canonical_url, connect, digest, ingest

TOPIC_IDS = {"performance", "digital", "expansion", "strategy", "risk"}
KST = timezone(timedelta(hours=9))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must contain a timezone")


def load_inputs(root):
    topics = read_json(root / "config/topics.json")["topics"]
    if {item["id"] for item in topics} != TOPIC_IDS or len(topics) != 5:
        raise ValueError("Only the five agreed topics are supported")
    if read_json(root / "config/interests.json")["interests"]:
        raise ValueError("Phase 1 starts without executive interests")
    if read_json(root / "reports/history.json")["reports"]:
        raise ValueError("Phase 1 starts without delivered reports")
    settings = read_json(root / "config/pipeline.json")
    if any(settings[key] for key in ("real_collection_enabled", "ai_enabled", "schedule_enabled")):
        raise ValueError("Real collection, AI and schedules must stay disabled")
    return {item["id"]: item["name"] for item in topics}


def validate_fixture(data):
    if data.get("is_demo") is not True or not data.get("articles"):
        raise ValueError("Only nonempty, explicitly fictional fixtures are allowed")
    for item in data["articles"]:
        if item["topic_id"] not in TOPIC_IDS:
            raise ValueError("Fixture topic outside agreed scope")
        if not canonical_url(item["uri"]).startswith("demo://"):
            raise ValueError("Fixture sources must be demo:// identifiers")
        if not item["issue_id"].startswith("demo-issue-") or "/" in item["issue_id"] or "\\" in item["issue_id"]:
            raise ValueError("Invalid demo issue ID")
        if not item["company"].startswith("가상 "):
            raise ValueError("Fictional claims must not be attributed to real companies")
        validate_timestamp(item["published_at"])
        validate_timestamp(item["source_updated_at"])
        if not item["title"] or not item["content_text"]:
            raise ValueError("Fixture title/body cannot be empty")


def build_mart(db, topics, now, duplicates):
    current = db.execute("""SELECT v.* FROM articles a JOIN article_versions v
                            ON a.current_version_id=v.version_id ORDER BY v.issue_id,v.version_id""").fetchall()
    groups = {}
    for item in current:
        groups.setdefault(item["issue_id"], []).append(dict(item))
    if not groups:
        raise ValueError("Empty fixture result is not a successful demo")
    generation = "demo-" + digest(now + "|" + "|".join(row["version_id"] for row in current))
    candidates, details = [], {}
    reason = {
        "performance": "자본 배분과 주주정책 변화를 검토하는 가상 사례입니다.",
        "digital": "AI 서비스의 적용 범위와 실행 단계를 비교하는 가상 사례입니다.",
        "expansion": "제휴를 통한 고객 접점 확장을 검토하는 가상 사례입니다.",
        "strategy": "조직과 자원 배분의 변화를 점검하는 가상 사례입니다.",
        "risk": "운영 위험과 고객 영향, 후속 대응을 점검하는 가상 사례입니다.",
    }
    for issue_id, evidence in sorted(groups.items()):
        main = max(evidence, key=lambda value: (datetime.fromisoformat(value["source_updated_at"]),
                                                value["version_id"]))
        topic_id = main["topic_id"]
        version_total = db.execute("SELECT COUNT(*) FROM article_versions WHERE issue_id=?", (issue_id,)).fetchone()[0]
        changed = version_total > len(evidence)
        bullets = [f"가상 사실: {main['content_text']}", f"검토 관점: {reason[topic_id]}",
                   "등록된 실제 보고 이력이 없습니다. 담당자의 최종 판단이 필요합니다."]
        candidate = {
            "issue_id": issue_id, "title": main["title"], "company": main["company"],
            "topic_id": topic_id, "topic": topics[topic_id],
            "priority": "high" if topic_id in {"performance", "digital", "risk"} else "medium",
            "report_status": "no_history", "why_now": reason[topic_id], "bullets": bullets,
            "evidence_ids": [item["version_id"] for item in evidence],
            "detail_path": f"data/issues/{issue_id}.json", "is_demo": True,
            "analysis_status": "demo_rules", "evidence_confidence": "fictional",
        }
        detail = {
            **candidate, "schema_version": "1.0", "generation_id": generation,
            "change_summary": "같은 가상 기사의 수정 버전을 보존했습니다. 실제 기보고 대비 변화는 아닙니다." if changed else "가상 자료 간 중복을 정리했습니다. 등록된 실제 보고 이력은 없습니다.",
            "last_report": None,
            "evidence": [{"evidence_id": item["version_id"], "title": item["title"],
                          "published_at": item["published_at"], "source_updated_at": item["source_updated_at"],
                          "content_level": item["content_level"], "content_text": item["content_text"],
                          "source_name": item["source_name"], "source_url": None,
                          "fixture_ref": "tests/fixtures/demo_articles.json", "is_demo": True}
                         for item in evidence],
        }
        candidates.append(candidate)
        details[issue_id] = detail
    candidates.sort(key=lambda item: (item["priority"] != "high", item["issue_id"]))
    parsed = datetime.fromisoformat(now)
    iso = parsed.isocalendar()
    latest = {"schema_version": "1.0", "generation_id": generation, "generated_at": now,
              "report_week": f"{iso.year}-W{iso.week:02d}", "is_demo": True,
              "candidates": candidates}
    counts = {"article_count": db.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
              "version_count": db.execute("SELECT COUNT(*) FROM article_versions").fetchone()[0],
              "run_count": db.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]}
    status = {"schema_version": "1.0", "generation_id": generation,
              "published_generation_id": generation, "is_demo": True, "last_run_at": now,
              **counts, "issue_count": len(candidates), "duplicate_count": duplicates,
              "analysis_mode": "demo_rules", "collection_mode": "fixture",
              "interests_count": 0, "report_count": 0,
              "real_collection_enabled": False, "ai_enabled": False, "schedule_enabled": False}
    history = {"schema_version": "1.0", "generation_id": generation, "is_demo": True, "reports": []}
    return latest, details, status, history


def validate_mart(db, latest, details, status, history):
    generation = latest["generation_id"]
    if not latest["is_demo"] or not latest["candidates"]:
        raise ValueError("Mart must be a nonempty demo")
    validate_timestamp(latest["generated_at"])
    validate_timestamp(status["last_run_at"])
    if history["reports"] or status["report_count"] != 0 or status["interests_count"] != 0:
        raise ValueError("Demo must not claim real reports or executive interests")
    if status["analysis_mode"] != "demo_rules" or status["collection_mode"] != "fixture":
        raise ValueError("Demo must not claim real collection or AI")
    if any(status.get(key) is not False for key in
           ("real_collection_enabled", "ai_enabled", "schedule_enabled")):
        raise ValueError("Live features must remain disabled")
    ids = [item["issue_id"] for item in latest["candidates"]]
    if len(ids) != len(set(ids)) or set(ids) != set(details):
        raise ValueError("Duplicate or missing issue detail")
    valid_evidence = {row[0] for row in db.execute("SELECT version_id FROM article_versions")}
    for obj in [latest, status, history, *details.values()]:
        if obj.get("generation_id") != generation or obj.get("is_demo") is not True or obj.get("schema_version") != "1.0":
            raise ValueError("Mixed generation, schema, or non-demo data")
    if status["published_generation_id"] != generation:
        raise ValueError("Status generation mismatch")
    for item in latest["candidates"]:
        detail = details[item["issue_id"]]
        if item["topic_id"] not in TOPIC_IDS or item["report_status"] != "no_history" or detail["last_report"] is not None:
            raise ValueError("Invalid topic or invented report history")
        if item["detail_path"] != f"data/issues/{item['issue_id']}.json":
            raise ValueError("Unsafe or incorrect detail path")
        if not item["evidence_ids"] or not set(item["evidence_ids"]).issubset(valid_evidence):
            raise ValueError("Missing evidence reference")
        if set(item["evidence_ids"]) != {entry["evidence_id"] for entry in detail["evidence"]}:
            raise ValueError("Detail evidence mismatch")
        if any(entry["source_url"] is not None for entry in detail["evidence"]):
            raise ValueError("Fictional evidence cannot link to a real news page")
    for table, key in (("articles", "article_count"), ("article_versions", "version_count"), ("collection_runs", "run_count")):
        if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != status[key]:
            raise ValueError("SQLite/status count mismatch")
    if status["issue_count"] != len(ids):
        raise ValueError("Issue count mismatch")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("SQLite integrity failed")


def run_demo(root, fixture_path=None):
    root = Path(root).resolve()
    topics = load_inputs(root)
    fixture = read_json(fixture_path or root / "tests/fixtures/demo_articles.json")
    validate_fixture(fixture)
    now = datetime.now(KST).isoformat(timespec="microseconds")
    db_path = root / "data/raw.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="weekly-demo-", dir=root) as temporary:
        stage = Path(temporary)
        stage_db = stage / "raw.sqlite3"
        if db_path.exists():
            with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(stage_db)) as target:
                source.backup(target)
        db = connect(stage_db)
        try:
            inserted, duplicates = ingest(db, fixture["articles"], now)
            latest, details, status, history = build_mart(db, topics, now, duplicates)
            validate_mart(db, latest, details, status, history)
        finally:
            db.close()
        mart_stage = stage / "mart"
        write_json(mart_stage / "latest.json", latest)
        write_json(mart_stage / "status.json", status)
        write_json(mart_stage / "report-history.json", history)
        for issue_id, detail in details.items():
            write_json(mart_stage / "issues" / f"{issue_id}.json", detail)
        state_stage = stage / "state"
        write_json(state_stage / "issues.json", {"schema_version": "1.0", "is_demo": True,
                   "generation_id": latest["generation_id"], "issues": latest["candidates"]})
        write_json(state_stage / "run_status.json", status)
        for issue_id, detail in details.items():
            write_json(state_stage / "analyses" / f"{issue_id}.json", detail)
        # Everything has been validated before replacing tracked artifacts. Backups allow
        # rollback on an ordinary filesystem failure; no publication occurs until git push.
        destinations = [(stage_db, db_path), (state_stage, root / "state"),
                        (mart_stage, root / "site/data")]
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
    return {"ok": True, **status, "inserted_versions": inserted}


def verify(root):
    root = Path(root).resolve()
    load_inputs(root)
    latest = read_json(root / "site/data/latest.json")
    status = read_json(root / "site/data/status.json")
    history = read_json(root / "site/data/report-history.json")
    details = {path.stem: read_json(path) for path in (root / "site/data/issues").glob("*.json")}
    db_path = root / "data/raw.sqlite3"
    if not db_path.is_file():
        raise ValueError("SQLite store is missing")
    db = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        validate_mart(db, latest, details, status, history)
    finally:
        db.close()
    state = read_json(root / "state/run_status.json")
    if state != status:
        raise ValueError("State and mart status differ")
    issues_path = root / "state/issues.json"
    if not issues_path.is_file():
        raise ValueError("Analysis state issues are missing")
    expected_issues = {"schema_version": "1.0", "is_demo": True,
                       "generation_id": latest["generation_id"], "issues": latest["candidates"]}
    if read_json(issues_path) != expected_issues:
        raise ValueError("Analysis state issues differ from mart")
    analyses = {path.stem: read_json(path) for path in (root / "state/analyses").glob("*.json")}
    if analyses != details:
        raise ValueError("Analysis state is missing, stale, or differs from mart")
    return {"ok": True, "generation_id": latest["generation_id"],
            "article_count": status["article_count"], "version_count": status["version_count"],
            "issue_count": status["issue_count"], "run_count": status["run_count"]}

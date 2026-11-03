"""Persistent metadata-only live store, isolated from the authored demo tables."""

import json
from datetime import datetime, timezone

from .storage import connect as connect_demo, digest
from .classification import classify_for_source, source_policy

METADATA_FIELDS = ("uri", "title", "published_at", "source_updated_at", "published_date", "source_id",
                   "source_name", "content_text", "content_level", "retention_basis", "retention_url")


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect(path):
    db = connect_demo(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS live_articles (
            article_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL UNIQUE,
            current_version_id TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS live_article_versions (
            version_id TEXT PRIMARY KEY,
            article_id TEXT NOT NULL REFERENCES live_articles(article_id),
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            source_updated_at TEXT,
            published_at TEXT,
            first_fetched_at TEXT NOT NULL,
            UNIQUE(article_id, content_hash)
        );
        CREATE TABLE IF NOT EXISTS live_collection_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            fetched_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            inserted_versions INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS live_source_state (
            source_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS live_source_runs (
            source_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES live_collection_runs(run_id),
            source_id TEXT NOT NULL,
            state_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS live_article_policy (
            article_id TEXT PRIMARY KEY REFERENCES live_articles(article_id),
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            content_filter TEXT NOT NULL,
            content_policy_version TEXT NOT NULL,
            inactive_reason TEXT,
            route_json TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(live_collection_runs)")}
    if "policy_filtered" not in columns:
        db.execute("ALTER TABLE live_collection_runs ADD COLUMN policy_filtered INTEGER NOT NULL DEFAULT 0")
    return db


def previous_sources(db):
    return {row["source_id"]: json.loads(row["state_json"])
            for row in db.execute("SELECT * FROM live_source_state")}


def ingest(db, records, now):
    inserted = 0
    for record in records:
        article_id = "article-" + digest(record["uri"])
        metadata = {key: record.get(key) for key in METADATA_FIELDS}
        content_hash = digest(json_text(metadata))
        version_id = "version-" + digest(article_id + content_hash)
        db.execute("""INSERT INTO live_articles VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
            (article_id, record["uri"], now, now))
        existing = db.execute("SELECT payload_json FROM live_article_versions WHERE article_id=?", (article_id,)).fetchall()
        # Older stores hashed routing hints too. Reuse the preserved source version
        # when only our classification policy changes; do not invent an article edit.
        same_metadata = any({key: json.loads(row["payload_json"]).get(key) for key in METADATA_FIELDS} == metadata for row in existing)
        if not same_metadata:
            cursor = db.execute("""INSERT OR IGNORE INTO live_article_versions
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (version_id, article_id, content_hash, json_text(record),
                 record["source_updated_at"], record["published_at"], now))
            inserted += cursor.rowcount
        versions = db.execute("SELECT * FROM live_article_versions WHERE article_id=?", (article_id,)).fetchall()
        # Feed replay must not roll a current revision back. Unknown dates use first
        # observation, never a fabricated publication timestamp.
        latest = max(versions, key=lambda row: (row["source_updated_at"] or row["published_at"] or row["first_fetched_at"],
                                                row["first_fetched_at"], row["version_id"]))
        db.execute("UPDATE live_articles SET current_version_id=? WHERE article_id=?", (latest["version_id"], article_id))
    return inserted, len(records) - inserted


def current_records(db, include_inactive=False):
    records = []
    for row in db.execute("""SELECT v.*,a.canonical_url,a.first_seen_at,a.last_seen_at,
        p.active,p.content_filter,p.content_policy_version,p.route_json
        FROM live_articles a JOIN live_article_versions v ON a.current_version_id=v.version_id
        LEFT JOIN live_article_policy p ON p.article_id=a.article_id
        WHERE ? OR COALESCE(p.active,1)=1 ORDER BY a.canonical_url""", (include_inactive,)):
        records.append({**json.loads(row["payload_json"]),
                        **(json.loads(row["route_json"]) if row["route_json"] else {}),
                        "content_filter": row["content_filter"], "content_policy_version": row["content_policy_version"],
                        "article_id": row["article_id"],
                        "version_id": row["version_id"], "fetched_at": row["first_fetched_at"],
                        "first_seen_at": row["first_seen_at"], "last_seen_at": row["last_seen_at"]})
    return records


def counts(db):
    active = db.execute("SELECT COUNT(*) FROM live_articles a JOIN live_article_policy p ON a.article_id=p.article_id WHERE p.active=1").fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM live_articles").fetchone()[0]
    return {"article_count": active, "active_article_count": active, "archived_article_count": total - active,
            "total_article_count": total,
            "version_count": db.execute("SELECT COUNT(*) FROM live_article_versions v JOIN live_article_policy p ON p.article_id=v.article_id WHERE p.active=1").fetchone()[0],
            "total_version_count": db.execute("SELECT COUNT(*) FROM live_article_versions").fetchone()[0],
            "run_count": db.execute("SELECT COUNT(*) FROM live_collection_runs").fetchone()[0]}


def expected_policies(db, sources, companies, topics):
    by_id = {source["id"]: source for source in sources}
    rows = []
    for record in current_records(db, include_inactive=True):
        source = by_id.get(record["source_id"])
        if source is None:
            rows.append((record["article_id"], 0, "disabled", "disabled", "source_disabled", "{}"))
            continue
        content_filter, version = source_policy(source)
        route = classify_for_source(record["title"], source, companies, topics)
        rows.append((record["article_id"], int(route is not None), content_filter, version,
                     None if route else "outside_content_filter" if content_filter == "earnings_only" else "outside_scope",
                     json_text(route or {})))
    return rows


def apply_policies(db, sources, companies, topics):
    """Archive out-of-scope current records without deleting source history."""
    changed = False
    for row in expected_policies(db, sources, companies, topics):
        previous = db.execute("SELECT * FROM live_article_policy WHERE article_id=?", (row[0],)).fetchone()
        if previous is None or tuple(previous) != row:
            changed = True
            db.execute("INSERT OR REPLACE INTO live_article_policy VALUES(?,?,?,?,?,?)", row)
    return changed


def validate_policies(db, sources, companies, topics):
    expected = sorted(expected_policies(db, sources, companies, topics))
    actual = sorted(tuple(row) for row in db.execute("SELECT * FROM live_article_policy"))
    if actual != expected:
        raise ValueError("Stored visibility policy differs from configured collection scope")


def save_source(db, run_id, state):
    payload = json_text(state)
    db.execute("INSERT INTO live_source_state VALUES (?, ?) ON CONFLICT(source_id) DO UPDATE SET state_json=excluded.state_json",
               (state["source_id"], payload))
    db.execute("INSERT INTO live_source_runs(run_id,source_id,state_json) VALUES(?,?,?)",
               (run_id, state["source_id"], payload))

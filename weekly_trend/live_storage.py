"""Persistent metadata-only live store, isolated from the authored demo tables."""

import json
from datetime import datetime, timezone

from .storage import connect as connect_demo, digest


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
    """)
    return db


def previous_sources(db):
    return {row["source_id"]: json.loads(row["state_json"])
            for row in db.execute("SELECT * FROM live_source_state")}


def ingest(db, records, now):
    inserted = 0
    for record in records:
        article_id = "article-" + digest(record["uri"])
        content_hash = digest(json_text(record))
        version_id = "version-" + digest(article_id + content_hash)
        db.execute("""INSERT INTO live_articles VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
            (article_id, record["uri"], now, now))
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


def current_records(db):
    records = []
    for row in db.execute("""SELECT v.*,a.canonical_url,a.first_seen_at,a.last_seen_at
        FROM live_articles a JOIN live_article_versions v ON a.current_version_id=v.version_id
        ORDER BY a.canonical_url"""):
        records.append({**json.loads(row["payload_json"]), "article_id": row["article_id"],
                        "version_id": row["version_id"], "fetched_at": row["first_fetched_at"],
                        "first_seen_at": row["first_seen_at"], "last_seen_at": row["last_seen_at"]})
    return records


def counts(db):
    return {key: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table, key in (("live_articles", "article_count"),
                               ("live_article_versions", "version_count"),
                               ("live_collection_runs", "run_count"))}


def save_source(db, run_id, state):
    payload = json_text(state)
    db.execute("INSERT INTO live_source_state VALUES (?, ?) ON CONFLICT(source_id) DO UPDATE SET state_json=excluded.state_json",
               (state["source_id"], payload))
    db.execute("INSERT INTO live_source_runs(run_id,source_id,state_json) VALUES(?,?,?)",
               (run_id, state["source_id"], payload))

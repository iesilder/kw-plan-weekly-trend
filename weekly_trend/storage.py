"""Small SQLite store. There are deliberately no network dependencies."""

import hashlib
import json
import sqlite3
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def canonical_url(value):
    """Remove known tracking fields; preserve article-identifying query fields."""
    parts = urlsplit(value)
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_")
             and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                      urlencode(query), ""))


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        db.close()
        raise ValueError(f"Unsupported SQLite schema version: {version}")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            article_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL UNIQUE,
            current_version_id TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS article_versions (
            version_id TEXT PRIMARY KEY,
            article_id TEXT NOT NULL REFERENCES articles(article_id),
            content_hash TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source_updated_at TEXT NOT NULL,
            content_text TEXT NOT NULL,
            content_level TEXT NOT NULL,
            source_name TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            company TEXT NOT NULL,
            first_fetched_at TEXT NOT NULL,
            retention_basis TEXT NOT NULL,
            UNIQUE(article_id, content_hash)
        );
        CREATE TABLE IF NOT EXISTS collection_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            fetched_count INTEGER NOT NULL,
            inserted_versions INTEGER NOT NULL,
            duplicate_count INTEGER NOT NULL
        );
        PRAGMA user_version = 1;
    """)
    return db


def ingest(db, records, now):
    inserted = 0
    for record in records:
        url = canonical_url(record["uri"])
        article_id = "demo-article-" + digest(url)
        payload = {key: record[key] for key in
                   ("title", "published_at", "source_updated_at", "content_text",
                    "topic_id", "issue_id", "company")}
        content_hash = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        version_id = "demo-version-" + digest(article_id + content_hash)
        db.execute("""INSERT INTO articles VALUES (?, ?, NULL, ?, ?)
                      ON CONFLICT(article_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                   (article_id, url, now, now))
        cursor = db.execute("""INSERT OR IGNORE INTO article_versions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, article_id, content_hash, record["title"], record["published_at"],
             record["source_updated_at"], record["content_text"], "full_text",
             "가상 시나리오", record["topic_id"], record["issue_id"], record["company"],
             now, "project-authored fictional demonstration"))
        inserted += cursor.rowcount
        # Old fixtures reappear on every run. Never roll the current version backwards.
        versions = db.execute("""SELECT version_id,source_updated_at FROM article_versions
            WHERE article_id=?""", (article_id,)).fetchall()
        latest = max(versions, key=lambda row: (datetime.fromisoformat(row["source_updated_at"]),
                                                row["version_id"]))["version_id"]
        db.execute("UPDATE articles SET current_version_id=? WHERE article_id=?",
                   (latest, article_id))
    duplicates = len(records) - inserted
    db.execute("""INSERT INTO collection_runs
        (started_at,finished_at,status,fetched_count,inserted_versions,duplicate_count)
        VALUES (?,?,'success',?,?,?)""", (now, now, len(records), inserted, duplicates))
    db.commit()
    return inserted, duplicates

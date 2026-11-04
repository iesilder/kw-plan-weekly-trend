"""An independent, metadata-only view of named financial institutions in raw news.

This module reads the saved SQLite store. It never collects sources, reads article
bodies, requests AI output, or adds these rows to the five-topic reporting mart.
"""

from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
import re
import sqlite3
import tempfile

from .article_sources import ArticleError, article_url
from .classification import plain_title
from .financial_institutions import load_matcher, match_title
from .live_storage import current_records, json_text, timestamp
from .pipeline import read_json, write_json
from .storage import digest

SCHEMA_VERSION = "1.0"
MATCH_BASIS = "title_institution_name"
MARKER_KEYS = ("finance_all_schema_version", "finance_all_generation_id", "finance_all_count")


def _supported(source):
    return (source.get("enabled") is True and source.get("policy_status") == "verified"
            and source.get("retention") == "metadata_only" and
            ((source.get("type") == "rss" and source.get("adapter") in {"einfomax_rss", "newswire_rss"}) or
             (source.get("type") == "html" and source.get("adapter") == "fsc_html")))


def build(db, root, generated_at, sources=None):
    """Derive current article metadata from all raw rows, including excluded rows."""
    root = Path(root)
    stamp = timestamp(generated_at)
    if stamp is None:
        raise ValueError("Financial article view requires a generation time")
    matcher = load_matcher(root)
    configured = sources if sources is not None else read_json(root / "config/sources.json")["sources"]
    by_id = {source["id"]: source for source in configured if _supported(source)}
    articles = []
    for record in current_records(db, include_inactive=True):
        source = by_id.get(record["source_id"])
        if source is None:
            continue
        # Old disabled/changed sources cannot supply a URL to this view. Exact
        # publisher endpoints also prevent attachment or unrelated-host links.
        try:
            url = article_url(record["uri"], source)
        except ArticleError:
            continue
        title = plain_title(record["title"])
        if title != record["title"] or record.get("content_level") != "metadata_only" or record.get("content_text") not in (None, ""):
            raise ValueError("Financial article view requires sanitized metadata")
        names = match_title(title, matcher)
        if not names:
            continue
        if (not re.fullmatch(r"article-[a-f0-9]{20}", record["article_id"]) or
                not re.fullmatch(r"version-[a-f0-9]{20}", record["version_id"])):
            raise ValueError("Invalid raw article identity")
        published_date = record.get("published_date")
        if published_date is not None and date.fromisoformat(published_date).isoformat() != published_date:
            raise ValueError("Invalid raw publication date")
        item = {key: record[key] for key in ("article_id", "version_id", "source_id", "source_name")}
        item.update(title=title, source_url=url, published_date=published_date,
                    published_at=timestamp(record.get("published_at")),
                    source_updated_at=timestamp(record.get("source_updated_at")),
                    fetched_at=timestamp(record["fetched_at"]), institutions=names)
        if item["fetched_at"] is None:
            raise ValueError("Missing raw observation time")
        articles.append(item)
    def order(item):
        known = [value for value in (item["source_updated_at"], item["published_at"], item["published_date"]) if value]
        return (max(known) if known else item["fetched_at"], item["article_id"])
    articles.sort(key=order, reverse=True)
    generation = "finance-" + digest(json_text({"matcher": matcher, "articles": articles}))
    return {"schema_version": SCHEMA_VERSION, "generation_id": generation,
            "generated_at": stamp, "is_demo": False, "match_basis": MATCH_BASIS,
            "count": len(articles), "articles": articles}


def markers(value):
    return {"finance_all_schema_version": SCHEMA_VERSION,
            "finance_all_generation_id": value["generation_id"], "finance_all_count": value["count"]}


def validate(db, root, value, sources=None):
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or value.get("is_demo") is not False or value.get("match_basis") != MATCH_BASIS
            or type(value.get("count")) is not int):
        raise ValueError("Invalid financial article view")
    expected = build(db, root, value.get("generated_at"), sources)
    if value != expected:
        raise ValueError("Financial article view differs from current raw metadata")
    return value


def _connect_readonly(root):
    path = (Path(root) / "data/raw.sqlite3").resolve()
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def prepare(root, now=None):
    """Publish an offline view without changing collection cursors or AI state."""
    root = Path(root).resolve()
    path, status_path = root / "site/data/finance-all.json", root / "site/data/status.json"
    if not (root / "data/raw.sqlite3").is_file() or not status_path.is_file():
        return {"ok": True, "mode": "not_prepared", "count": 0}
    status = read_json(status_path)
    if status.get("is_demo") is not False:
        return {"ok": True, "mode": "not_applicable", "count": 0}
    stamp = timestamp((now or datetime.now(timezone.utc)).isoformat())
    with closing(_connect_readonly(root)) as db:
        value = build(db, root, stamp)
        if path.exists():
            old = read_json(path)
            if old.get("generation_id") == value["generation_id"]:
                value = build(db, root, old.get("generated_at"))
        validate(db, root, value)
    status.update(markers(value))
    with tempfile.TemporaryDirectory(prefix="weekly-finance-", dir=root) as directory:
        stage = Path(directory)
        write_json(stage / "finance-all.json", value)
        write_json(stage / "status.json", status)
        write_json(stage / "run_status.json", status)
        from .live_pipeline import _replace
        _replace(stage, [(stage / "finance-all.json", path), (stage / "status.json", status_path),
                         (stage / "run_status.json", root / "state/run_status.json")])
    return {"ok": True, "mode": "prepared", "count": value["count"], "generation_id": value["generation_id"], "request_count": 0}


def check(root):
    root = Path(root).resolve()
    path, status_path = root / "site/data/finance-all.json", root / "site/data/status.json"
    if not status_path.exists():
        return {"ok": True, "mode": "not_prepared", "request_count": 0}
    status = read_json(status_path)
    present = [key in status for key in MARKER_KEYS]
    if not path.exists() and not any(present):
        return {"ok": True, "mode": "not_prepared", "request_count": 0}
    if not path.exists() or not all(present):
        raise ValueError("Financial article view or status marker is missing")
    value = read_json(path)
    with closing(_connect_readonly(root)) as db:
        validate(db, root, value)
    if type(status["finance_all_count"]) is not int or any(status[key] != expected for key, expected in markers(value).items()):
        raise ValueError("Financial article status marker differs")
    return {"ok": True, "mode": "verified", "generation_id": value["generation_id"], "count": value["count"], "request_count": 0}

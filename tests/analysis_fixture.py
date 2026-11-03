"""Install fixed public metadata, never live AI output, into temporary tests."""

import json
from pathlib import Path


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install_analysis_fixture(root, *, seed_store=False):
    fixture = json.loads((Path(__file__).parent / "fixtures/analysis/metadata.json").read_text(encoding="utf-8"))
    latest = fixture["latest"]
    for path in (root / "site/data/issues").glob("*.json"):
        path.unlink()
    _write(root / "site/data/latest.json", latest)
    for candidate in latest["candidates"]:
        _write(root / "site" / candidate["detail_path"], fixture["issues"][candidate["issue_id"]])
    _write(root / "reports/history.json", {"schema_version": "1.0", "reports": []})
    reviews = {"schema_version": "1.0", "revision": 0, "updated_at": None, "decisions": {}}
    _write(root / "reports/reviews.json", reviews)
    _write(root / "site/data/reviews.json", reviews)
    _write(root / "site/data/report-history.json", {"schema_version": "1.0", "generation_id": latest["generation_id"], "is_demo": False, "reports": []})
    _write(root / "state/ai-analysis.json", {"schema_version": "1.0", "entries": {}, "last_attempt": None})
    _write(root / "state/article-analysis.json", {"schema_version": "1.0", "entries": {}, "checks": {}, "last_attempt": None})
    for name in ("ai-analysis.json", "article-analysis.json"):
        (root / "site/data" / name).unlink(missing_ok=True)
    if seed_store:
        # A fixed metadata-only store makes collection-preservation tests
        # independent of later production growth and article corrections.
        from weekly_trend.live_storage import connect, json_text
        path = root / "data/raw.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        db = connect(path)
        try:
            for row in fixture["source_records"]:
                db.execute("INSERT INTO live_articles VALUES (?,?,?,?,?)", (
                    row["article_id"], row["canonical_url"], row["version_id"], row["first_fetched_at"], row["first_fetched_at"]))
                db.execute("INSERT INTO live_article_versions VALUES (?,?,?,?,?,?,?)", (
                    row["version_id"], row["article_id"], row["content_hash"], json_text(row["payload"]),
                    row["source_updated_at"], row["published_at"], row["first_fetched_at"]))
            db.commit()
        finally:
            db.close()
    return latest

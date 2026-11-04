"""Raw financial headlines stay independent of reports, bodies and AI analysis."""

from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_live_pipeline as fixtures
from test_live_pipeline import NOW, record, source
from weekly_trend import finance_all
from weekly_trend.live_pipeline import run_collection
from weekly_trend.pipeline import read_json, verify, write_json


def news_source(**changes):
    return source("finance-news", adapter="einfomax_rss", scope="competitors_and_industry",
                  name="연합인포맥스(기사) · 증권", url="https://news.einfomax.co.kr/rss/S1N2.xml",
                  allowed_hosts=["news.einfomax.co.kr"], **changes)


def news(number=1, **changes):
    return record(uri=f"https://news.einfomax.co.kr/news/articleView.html?idxno={1000000 + number}", **changes)


class FinanceAllTests(unittest.TestCase):
    setUp = fixtures.LivePipelineTests.setUp
    sources = fixtures.LivePipelineTests.sources
    run_records = fixtures.LivePipelineTests.run_records
    rows = fixtures.LivePipelineTests.rows
    latest = fixtures.LivePipelineTests.latest

    def finance(self):
        return read_json(self.root / "site/data/finance-all.json")

    def test_excluded_raw_news_and_noncompetitor_institutions_are_visible_without_ai(self):
        self.sources([news_source()])
        with patch("weekly_trend.ai.request_once", side_effect=AssertionError("no AI")), \
                patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("no body")):
            result = self.run_records([
                news(1, title="미래에셋증권 고객 경품 이벤트 실시", content_text="Never store this body"),
                news(2, title="한화생명 고객 시상식 개최"),
                news(3, title="가상솔은행 정기예금 특판 행사 개최"),
                news(4, title="일반 제조사 공장 준공"),
                news(5, title="보험사기 예방 정책 발표"),
                news(6, title="국내은행 실적 개선 전망"),
                news(7, title="금융위기 우려 확산"),
            ])
        data = self.finance()
        self.assertEqual(result["mart_article_count"], 0)
        self.assertEqual(data["count"], 3)
        self.assertEqual({row["title"] for row in data["articles"]}, {
            "미래에셋증권 고객 경품 이벤트 실시", "한화생명 고객 시상식 개최", "가상솔은행 정기예금 특판 행사 개최"})
        encoded = json.dumps(data, ensure_ascii=False)
        for forbidden in ("Never store", "summary", "bullets", "why_candidate", "review_status", "content_text", "topic_id"):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(all(row["institutions"] for row in data["articles"]))
        self.assertTrue(verify(self.root)["ok"])

    def test_dart_and_source_identity_do_not_substitute_for_a_name_in_the_title(self):
        self.sources([news_source(), source("dart", type="dart_html", adapter="dart_html",
                     content_filter="earnings_only", url="https://dart.fss.or.kr/dsab001/search.ax",
                     allowed_hosts=["dart.fss.or.kr"])])
        def collect(item, **kwargs):
            records = ([record(uri="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261101000001", title="미래에셋증권 영업실적")]
                       if item["id"] == "dart" else [news(title="은행권 정책 지원 계획 발표")])
            return {"status": "success", "records": records, "fetched_count": len(records)}
        run_collection(self.root, collector=collect, now=NOW)
        self.assertEqual(self.finance()["articles"], [])

    def test_all_stored_dates_are_visible_even_when_outside_the_reporting_capture_window(self):
        self.sources([news_source()])
        result = self.run_records([news(title="한화생명 고객 행사 개최",
                                       published_at="2020-01-01T00:00:00Z", published_date="2020-01-01")])
        self.assertEqual(result["mart_article_count"], 0)
        self.assertEqual(self.finance()["count"], 1)
        self.assertEqual(self.finance()["articles"][0]["published_date"], "2020-01-01")

    def test_offline_prepare_and_replay_preserve_collection_ai_reviews_and_generation(self):
        self.sources([news_source()])
        self.run_records([news(title="한화생명 행사 개최")])
        before = {name: (self.root / name).read_bytes() for name in (
            "data/raw.sqlite3", "state/ai-analysis.json", "state/article-analysis.json", "reports/reviews.json", "reports/history.json")}
        original = self.finance()
        with patch("weekly_trend.collectors.collect_source", side_effect=AssertionError("no collection")), \
                patch("weekly_trend.ai.request_once", side_effect=AssertionError("no AI")), \
                patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("no body")):
            finance_all.prepare(self.root, now=NOW + timedelta(days=1))
        self.assertEqual(self.finance(), original)
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content, name)
        again = self.run_records([news(title="한화생명 행사 개최")], now=NOW + timedelta(days=1))
        self.assertEqual(self.finance(), original)
        self.assertEqual(again["finance_all_count"], 1)
        self.assertTrue(verify(self.root)["ok"])

    def test_current_revision_replaces_old_headline_without_leaking_old_version(self):
        self.sources([news_source()])
        self.run_records([news(title="한화생명 행사 개최")])
        self.run_records([news(title="일반 제조사 공장 준공", source_updated_at="2026-11-02T07:00:00Z")],
                         now=NOW + timedelta(hours=2))
        self.assertEqual(self.finance()["count"], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM live_article_versions")[0][0], 2)

    def test_artifact_and_marker_corruption_are_detected(self):
        self.sources([news_source()])
        self.run_records([news(title="한화생명 행사 개최")])
        path = self.root / "site/data/finance-all.json"
        original = self.finance()
        for change in (lambda value: value.update(count=True),
                       lambda value: value.update(generation_id="finance-" + "0" * 20),
                       lambda value: value["articles"][0].update(institutions=["무관기관"]),
                       lambda value: value["articles"][0].update(source_url="https://evil.example/article")):
            value = json.loads(json.dumps(original))
            change(value)
            write_json(path, value)
            with self.assertRaises(ValueError):
                finance_all.check(self.root)
        write_json(path, original)
        path.unlink()
        with self.assertRaises(ValueError):
            finance_all.check(self.root)

    def test_legacy_absence_can_be_prepared_and_then_becomes_required(self):
        self.sources([news_source()])
        self.run_records([news(title="한화생명 행사 개최")])
        path = self.root / "site/data/finance-all.json"
        path.unlink()
        for name in ("site/data/status.json", "state/run_status.json"):
            status = read_json(self.root / name)
            for key in finance_all.MARKER_KEYS:
                status.pop(key)
            write_json(self.root / name, status)
        self.assertEqual(finance_all.check(self.root)["mode"], "not_prepared")
        self.assertEqual(finance_all.prepare(self.root, now=NOW)["count"], 1)
        self.assertTrue(verify(self.root)["ok"])

    def test_failed_capture_retains_financial_rows_and_prepare_write_rolls_back(self):
        self.sources([news_source()])
        self.run_records([news(title="한화생명 행사 개최")])
        original = self.finance()
        result = run_collection(self.root, collector=lambda *a, **k: {"status": "failed", "error_code": "http_error"},
                                now=NOW + timedelta(hours=1))
        self.assertFalse(result["collection_ok"])
        self.assertEqual(self.finance(), original)
        files = ("site/data/finance-all.json", "site/data/status.json", "state/run_status.json")
        before = {name: (self.root / name).read_bytes() for name in files}
        import os
        replace = os.replace
        failed = False
        def fault(source_path, target_path):
            nonlocal failed
            if Path(target_path) == self.root / "site/data/status.json" and not failed:
                failed = True
                raise OSError("fixture replace failure")
            return replace(source_path, target_path)
        with patch("weekly_trend.live_pipeline.os.replace", side_effect=fault):
            with self.assertRaises(OSError):
                finance_all.prepare(self.root, now=NOW + timedelta(days=1))
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertTrue(verify(self.root)["ok"])

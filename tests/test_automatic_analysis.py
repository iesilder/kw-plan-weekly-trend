"""Scheduled inference must not resend unchanged inputs that failed validation."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from analysis_fixture import install_analysis_fixture

from weekly_trend import ai, article_analysis as articles
from weekly_trend.article_sources import ArticleError

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 7, tzinfo=timezone.utc)
ENV = {"GEMINI_API_KEY": "automatic-test-key", "AI_FREE_PROJECT_CONFIRMED": "true"}


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def response(items):
    return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({"items": items}, ensure_ascii=False)}]}]}


class AutomaticAnalysisTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="automatic-analysis-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for directory in ("config", "site/data", "reports", "state"):
            shutil.copytree(ROOT / directory, self.root / directory)
        install_analysis_fixture(self.root)
        self.latest = read(self.root / "site/data/latest.json")
        self.news = next(row for row in self.latest["candidates"] if row["content_filter"] == "topics")
        self.latest["candidates"] = [row for row in self.latest["candidates"] if row["issue_id"] == self.news["issue_id"] or row["content_filter"] == "earnings_only"]
        write(self.root / "site/data/latest.json", self.latest)
        write(self.root / "state/ai-analysis.json", ai.empty_state())
        write(self.root / "state/article-analysis.json", articles.empty_state())
        self.calls, self.fetches = [], []
        self.body_text = "기사에 따르면 신규 투자정보 서비스 출시를 추진한다. 투자 규모는 500억원으로 제시했다."
        for name in ("ai", "articles"):
            self.config(name, manual_only=False)

    def config(self, name, **changes):
        path = self.root / f"config/{name}.json"
        value = read(path)
        value.update(changes)
        write(path, value)

    def fetch(self, url, source):
        self.fetches.append(url)
        title = self.news["title"]
        for candidate in self.latest["candidates"]:
            if candidate["content_filter"] == "topics":
                detail = read(self.root / "site" / candidate["detail_path"])
                if detail["evidence"][0]["source_url"] == url:
                    title = candidate["title"]
                    break
        return {"title": title, "paragraphs": [{"id": "p1", "text": self.body_text}],
                "content_hash": hashlib.sha256(self.body_text.encode()).hexdigest(), "fetched_at": NOW.isoformat(), "truncated": False}

    def title_transport(self, payload, key, timeout):
        self.calls.append(payload)
        items = json.loads(payload["input"])["items"]
        self.assertTrue(all("news_metadata" in row and "disclosure_form" not in row for row in items))
        return response([{"issue_id": row["issue_id"], "suggested_topic_id": "digital", "relevance": "relevant",
                          "rationale": "기사 제목에 사업 변화가 나타나 검토 후보입니다.", "check_points": ["원문에서 추진 사항을 확인하세요."],
                          "duplicate_issue_ids": []} for row in items])

    def body_transport(self, payload, key, timeout, invalid=False):
        self.calls.append(payload)
        return response([{"issue_id": row["issue_id"], "key_change": {"text": "신규 투자정보 서비스 출시 추진", "evidence_ids": ["p1"]},
                          "facts": [{"text": "투자 규모 " + ("900억원" if invalid else "500억원"), "evidence_ids": ["p1"]}],
                          "stage": "추진·협약", "why_candidate": "신사업 추진 관점에서 검토 가능"}
                         for row in json.loads(payload["input"])["items"]])

    def body_run(self, **kwargs):
        return articles.run(self.root, environ=ENV, fetcher=kwargs.pop("fetcher", self.fetch),
                            transport=kwargs.pop("transport", self.body_transport), now=kwargs.pop("now", NOW), **kwargs)

    def invalid_body(self, payload, key, timeout):
        return self.body_transport(payload, key, timeout, invalid=True)

    def title_failure(self, payload, key, timeout):
        self.calls.append(payload)
        return response([])

    def test_automatic_requires_explicit_config_but_manual_runs_remain_available(self):
        for name, module in (("ai", ai), ("articles", articles)):
            self.config(name, manual_only=True)
            with self.assertRaises(ValueError):
                module.run(self.root, automatic=True, environ=ENV, now=NOW)
            self.assertFalse(module.prepare(self.root, now=NOW).get("auto_analysis_enabled", False))
            public = read(self.root / ("site/data/ai-analysis.json" if name == "ai" else "site/data/article-analysis.json"))
            self.assertFalse(public["auto_analysis_enabled"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fetches, [])

    def test_scheduling_flag_does_not_invalidate_successful_body_cache(self):
        self.config("articles", manual_only=True)
        self.body_run()
        self.config("articles", manual_only=False)
        result = self.body_run(automatic=True, now=NOW + timedelta(hours=1))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(result["last_attempt"]["cached_count"], 1)
        self.assertTrue(read(self.root / "site/data/article-analysis.json")["auto_analysis_enabled"])

    def test_new_article_is_not_starved_by_five_successful_caches_expiring_each_day(self):
        latest = json.loads((ROOT / "tests/fixtures/analysis/metadata.json").read_text())["latest"]
        news = [row for row in latest["candidates"] if row["content_filter"] == "topics"][:6]
        self.assertEqual(len(news), 6)
        self.latest["candidates"] = news + [row for row in self.latest["candidates"] if row["content_filter"] == "earnings_only"]
        write(self.root / "site/data/latest.json", self.latest)
        first = self.body_run(automatic=True)
        self.assertEqual(first["last_attempt"]["completed_count"], 5)
        self.assertEqual(first["counts"]["pending"], 1)
        first_ids = {row["issue_id"] for row in json.loads(self.calls[0]["input"])["items"]}
        waiting = {row["issue_id"] for row in news} - first_ids
        second = self.body_run(automatic=True, now=NOW + timedelta(hours=25))
        second_ids = {row["issue_id"] for row in json.loads(self.calls[1]["input"])["items"]}
        self.assertEqual(second_ids, waiting)
        self.assertEqual(second["last_attempt"]["fetch_count"], 5)
        self.assertEqual(second["last_attempt"]["completed_count"], 1)
        self.assertEqual(second["last_attempt"]["cached_count"], 4)
        self.assertEqual(second["counts"]["completed"], 6)

    def test_title_validation_failure_is_durable_automatic_skip_but_manual_retry_succeeds(self):
        ai.run(self.root, environ=ENV, transport=self.title_failure, now=NOW, automatic=True)
        result = ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW + timedelta(days=1), automatic=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["suppressed_count"], 1)
        self.assertEqual(result["last_attempt"]["cached_count"], 0)
        self.assertEqual(result["counts"]["failed"], 1)
        ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW + timedelta(days=2), automatic=True)
        self.assertEqual(len(self.calls), 1)
        manual = ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW + timedelta(days=2))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(manual["counts"]["completed"], 1)

    def test_title_prompt_change_releases_quarantine_and_transient_errors_are_retried(self):
        ai.run(self.root, environ=ENV, transport=self.title_failure, now=NOW, automatic=True)
        self.config("ai", prompt_version="metadata-auto-next")
        result = ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW, automatic=True)
        self.assertEqual(result["last_attempt"]["completed_count"], 1)
        write(self.root / "state/ai-analysis.json", ai.empty_state())
        def limited(*args):
            raise ai.AIError("rate_limited")
        ai.run(self.root, environ=ENV, transport=limited, now=NOW, automatic=True)
        result = ai.run(self.root, environ=ENV, transport=self.title_transport, now=NOW + timedelta(days=1), automatic=True)
        self.assertEqual(result["last_attempt"]["completed_count"], 1)

    def test_body_validation_failure_skips_then_refetches_without_api_until_body_changes(self):
        self.body_run(automatic=True, transport=self.invalid_body)
        skipped = self.body_run(automatic=True, now=NOW + timedelta(hours=1))
        self.assertEqual(skipped["suppressed_count"], 1)
        self.assertEqual(len(self.fetches), 1)
        rechecked = self.body_run(automatic=True, now=NOW + timedelta(hours=25))
        self.assertEqual(rechecked["suppressed_count"], 1)
        self.assertEqual(rechecked["last_attempt"]["cached_count"], 0)
        self.assertEqual(len(self.fetches), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(rechecked["counts"]["failed"], 1)
        self.body_text += "후속 추가 정보로 제공 대상을 확대했다."
        result = self.body_run(automatic=True, now=NOW + timedelta(hours=50))
        self.assertEqual(result["last_attempt"]["completed_count"], 1)
        self.assertEqual(len(self.calls), 2)

    def test_body_manual_retry_and_prompt_edit_release_failure(self):
        self.body_run(automatic=True, transport=self.invalid_body)
        manual = self.body_run(now=NOW + timedelta(hours=1))
        self.assertEqual(manual["last_attempt"]["completed_count"], 1)
        self.body_run(now=NOW + timedelta(hours=25), transport=self.invalid_body)  # successful unchanged body is reused
        # Force a changed body to exercise a fresh validation failure.
        self.body_text += "사업 범위를 추가 검토한다."
        self.body_run(now=NOW + timedelta(hours=50), transport=self.invalid_body)
        with (self.root / "config/article_prompt.txt").open("a") as handle:
            handle.write("\n추가 지시: 문장을 간결하게 작성.\n")
        result = self.body_run(automatic=True, now=NOW + timedelta(hours=51))
        self.assertEqual(result["last_attempt"]["completed_count"], 1)

    def test_legacy_failure_captures_body_hash_without_resending_and_keeps_failure_visible(self):
        self.body_run(transport=self.invalid_body)
        path = self.root / "state/article-analysis.json"
        state = read(path)
        failure = state["checks"][self.news["issue_id"]]
        failure.pop("input_fingerprint")
        failure.pop("body_hash")
        write(path, state)
        result = self.body_run(automatic=True, now=NOW + timedelta(hours=25))
        self.assertEqual(result["suppressed_count"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["last_attempt"]["cached_count"], 0)
        self.assertIn("input_fingerprint", read(path)["checks"][self.news["issue_id"]])

    def test_missing_key_or_confirmation_does_not_erase_invalid_body_quarantine(self):
        self.body_run(transport=self.invalid_body)
        path = self.root / "state/article-analysis.json"
        failed = read(path)
        for env in ({"AI_FREE_PROJECT_CONFIRMED": "true"}, {"GEMINI_API_KEY": ENV["GEMINI_API_KEY"]}):
            with self.subTest(env_keys=list(env)):
                write(path, failed)
                blocked = articles.run(self.root, environ=env, fetcher=self.fetch, transport=self.body_transport,
                                       now=NOW + timedelta(hours=25), automatic=True)
                self.assertEqual(blocked["last_attempt"]["status"], "blocked")
                self.assertEqual(blocked["counts"]["failed"], 1)
                self.assertEqual(read(path)["checks"][self.news["issue_id"]], failed["checks"][self.news["issue_id"]])
                restored = self.body_run(automatic=True, now=NOW + timedelta(hours=50))
                self.assertEqual(restored["last_attempt"]["request_count"], 0)
                self.assertEqual(restored["last_attempt"]["cached_count"], 0)
                self.assertEqual(restored["suppressed_count"], 1)
                self.assertEqual(len(self.calls), 1)

    def test_fetch_and_provider_transient_errors_do_not_erase_or_permanently_lock_results(self):
        self.body_run(transport=self.invalid_body)
        def unavailable(*args):
            raise ArticleError("network_error")
        self.body_run(automatic=True, fetcher=unavailable, now=NOW + timedelta(hours=25))
        self.body_run(automatic=True, now=NOW + timedelta(hours=50))
        self.assertEqual(len(self.calls), 1)
        write(self.root / "state/article-analysis.json", articles.empty_state())
        def limited(*args):
            raise ai.AIError("rate_limited")
        self.body_run(automatic=True, transport=limited)
        result = self.body_run(automatic=True, now=NOW + timedelta(days=1))
        self.assertEqual(result["last_attempt"]["completed_count"], 1)


if __name__ == "__main__":
    unittest.main()

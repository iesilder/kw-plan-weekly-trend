"""Exercise source grounding, cache freshness, errors, and persisted-data bounds."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from analysis_fixture import install_analysis_fixture

from weekly_trend import ai, article_analysis as articles
from weekly_trend.article_sources import ArticleError

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
ENV = {"AI_FREE_PROJECT_CONFIRMED": "true", "GEMINI_API_KEY": "unit-test-key"}
PARAGRAPHS = [{"id": "p1", "text": "가상증권은 2026년 11월 3일 신규 서비스를 출시했다고 밝혔다."},
              {"id": "p2", "text": "투자 규모는 500억원이며, 기관 고객을 대상으로 서비스를 제공한다."},
              {"id": "p3", "text": "이 문장은 전문이 저장되면 안 되는 테스트용 본문 비밀표식입니다."}]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def brief():
    return {"key_change": {"text": "가상증권이 신규 서비스를 출시했다고 밝혔다.", "evidence_ids": ["p1"]},
            "facts": [{"text": "투자 규모는 500억원이다.", "evidence_ids": ["p2"]}], "stage": "출시·시행",
            "why_candidate": "기관 고객 대상 서비스 확대 관점에서 검토할 수 있습니다.",
            "evidence": [{"id": "p1", "quote": "신규 서비스를 출시했다고 밝혔다."},
                         {"id": "p2", "quote": "투자 규모는 500억원이며"}]}


class ArticleAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="article-brief-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for folder in ["config", "site/data", "reports", "state", "data"]:
            shutil.copytree(ROOT / folder, self.root / folder)
        install_analysis_fixture(self.root, seed_store=True)
        ai.prepare(self.root, now=NOW)
        write(self.root / "state/article-analysis.json", articles.empty_state())
        (self.root / "site/data/article-analysis.json").unlink(missing_ok=True)
        self.fetches, self.calls = [], []
        self.latest = read(self.root / "site/data/latest.json")
        self.news = [row for row in self.latest["candidates"] if row["content_filter"] == "topics"]
        self.id = min(self.news, key=lambda row: (row.get("published_date") or row["published_at"][:10], row["issue_id"]))["issue_id"]
        self.body = {"title": "테스트용 기사", "paragraphs": copy.deepcopy(PARAGRAPHS),
                     "content_hash": hashlib.sha256(json.dumps(PARAGRAPHS).encode()).hexdigest(),
                     "fetched_at": NOW.isoformat(), "truncated": False}

    def fetch(self, url, source):
        self.fetches.append(url)
        value = copy.deepcopy(self.body)
        for candidate in self.news:
            detail = read(self.root / "site" / candidate["detail_path"])
            if detail["evidence"][0]["source_url"] == url:
                value["title"] = candidate["title"]
                break
        return value

    def transport(self, payload, key, timeout):
        self.calls.append((payload, key, timeout))
        return self.answer(payload)

    def answer(self, payload, change=None):
        results = []
        for item in json.loads(payload["input"])["items"]:
            value = {"issue_id": item["issue_id"], **brief()}
            if change:
                change(value)
            results.append(value)
        return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({"items": results}, ensure_ascii=False)}]}]}

    def run_good(self, **kwargs):
        return articles.run(self.root, environ=kwargs.pop("environ", ENV), fetcher=self.fetch,
                            transport=self.transport, now=kwargs.pop("now", NOW), **kwargs)

    def public(self):
        return read(self.root / "site/data/article-analysis.json")

    def add_fsc_news(self):
        candidate = copy.deepcopy(self.news[0])
        detail = read(self.root / "site" / candidate["detail_path"])
        source = next(row for row in read(self.root / "config/sources.json")["sources"] if row["adapter"] == "fsc_html")
        issue_id, evidence_id = "issue-" + "f" * 20, "version-" + "e" * 20
        for value in (candidate, detail):
            value.update(issue_id=issue_id, detail_path=f"data/issues/{issue_id}.json", evidence_ids=[evidence_id],
                         title="토큰증권 시행령 개정안 공개", source_name=source["name"])
        detail["evidence"][0].update(evidence_id=evidence_id, title=candidate["title"], source_id=source["id"],
            source_name=source["name"], source_url="https://www.fsc.go.kr/no010101/99999")
        self.latest["candidates"].append(candidate)
        self.news.append(candidate)
        write(self.root / "site/data/latest.json", self.latest)
        write(self.root / "site" / candidate["detail_path"], detail)
        return candidate

    def test_fsc_summary_is_body_grounded_without_reanalyzing_cached_news_or_dart(self):
        self.run_good()
        old_entries = copy.deepcopy(read(self.root / "state/article-analysis.json")["entries"])
        fsc = self.add_fsc_news()
        self.fetches.clear()
        self.calls.clear()
        result = self.run_good(automatic=True, issue_ids=[fsc["issue_id"]])
        self.assertEqual(self.fetches, ["https://www.fsc.go.kr/no010101/99999"])
        self.assertEqual(result["last_attempt"]["request_count"], 1)
        sent = json.loads(self.calls[0][0]["input"])["items"]
        self.assertEqual([item["issue_id"] for item in sent], [fsc["issue_id"]])
        self.assertEqual(sent[0]["source"], "금융위원회 · 보도자료")
        self.assertEqual([{key: row[key] for key in ("id", "text")} for row in sent[0]["paragraphs"]], PARAGRAPHS)
        self.assertEqual(sent[0]["format_rules"], articles.FSC_BODY_FORMAT_RULES)
        for row in sent[0]["paragraphs"]:
            self.assertEqual(row["allowed_numeric_tokens"], sorted(articles._numbers(row["text"])))
        state = read(self.root / "state/article-analysis.json")
        self.assertTrue(all(state["entries"][key] == value for key, value in old_entries.items()))
        self.assertEqual(self.public()["items"][fsc["issue_id"]]["status"], "completed")
        self.assertEqual(self.public()["counts"]["not_applicable"], 1)
        self.assertNotIn(PARAGRAPHS[2]["text"], (self.root / "state/article-analysis.json").read_text())
        self.assertTrue(articles.check(self.root)["ok"])
        command = ["node", "-e", "const fs=require('fs'),a=require(process.argv[1]);a.validateSidecar(JSON.parse(fs.readFileSync(process.argv[2])),JSON.parse(fs.readFileSync(process.argv[3])));",
                   str(ROOT / "site/article.js"), str(self.root / "site/data/article-analysis.json"), str(self.root / "site/data/latest.json")]
        checked = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_fsc_format_guidance_preserves_a_valid_legacy_cache_after_expiry(self):
        fsc = self.add_fsc_news()
        self.run_good(issue_ids=[fsc["issue_id"]])
        state = read(self.root / "state/article-analysis.json")
        entry = state["entries"][fsc["issue_id"]]
        original_metadata = entry["metadata_fingerprint"]
        legacy_fingerprint = articles._hash({"metadata": original_metadata, "body_hash": self.body["content_hash"],
            "paragraphs": self.body["paragraphs"], "truncated": False})
        self.assertNotEqual(legacy_fingerprint, entry["input_fingerprint"])
        entry["input_fingerprint"] = legacy_fingerprint
        old_result = copy.deepcopy(entry["result"])
        write(self.root / "state/article-analysis.json", state)
        self.calls.clear()
        result = self.run_good(issue_ids=[fsc["issue_id"]], now=NOW + timedelta(days=2))
        self.assertEqual(self.calls, [])
        self.assertEqual(result["last_attempt"]["cached_count"], 1)
        after = read(self.root / "state/article-analysis.json")["entries"][fsc["issue_id"]]
        self.assertEqual(after["metadata_fingerprint"], original_metadata)
        self.assertEqual(after["input_fingerprint"], legacy_fingerprint)
        self.assertEqual(after["result"], old_result)
        self.assertTrue(articles.check(self.root)["ok"])

    def test_fsc_failed_repair_gets_new_scoped_input_without_changing_other_entries(self):
        self.run_good()
        old_entries = copy.deepcopy(read(self.root / "state/article-analysis.json")["entries"])
        fsc = self.add_fsc_news()
        latest, details = ai._snapshot(self.root)
        inputs = articles._inputs(self.root, latest, details, articles.load_config(self.root))
        data = inputs[fsc["issue_id"]]
        legacy_fingerprint = articles._hash({"metadata": data["metadata_fingerprint"], "body_hash": self.body["content_hash"],
            "paragraphs": self.body["paragraphs"], "truncated": False})
        state = read(self.root / "state/article-analysis.json")
        state["checks"][fsc["issue_id"]] = {"metadata_fingerprint": data["metadata_fingerprint"], "checked_at": NOW.isoformat(),
            "reason_code": "output_number_mismatch", "status": "failed", "input_fingerprint": legacy_fingerprint,
            "body_hash": self.body["content_hash"]}
        write(self.root / "state/article-analysis.json", state)
        self.calls.clear()
        self.run_good(issue_ids=[fsc["issue_id"]], automatic=False)
        after = read(self.root / "state/article-analysis.json")
        self.assertEqual(len(self.calls), 1)
        self.assertNotEqual(after["entries"][fsc["issue_id"]]["input_fingerprint"], legacy_fingerprint)
        self.assertTrue(all(after["entries"][key] == value for key, value in old_entries.items()))
        self.assertEqual(after["entries"][fsc["issue_id"]]["metadata_fingerprint"], data["metadata_fingerprint"])

    def test_prepare_never_fetches_or_calls_ai_and_does_not_invent_summaries(self):
        with patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("network forbidden")), patch("weekly_trend.ai.request_once", side_effect=AssertionError("AI forbidden")):
            result = articles.prepare(self.root, now=NOW)
        self.assertEqual(result["counts"]["eligible"], len(self.news))
        self.assertEqual(result["counts"]["pending"], len(self.news))
        self.assertEqual(result["counts"]["not_applicable"], 1)
        self.assertTrue(all(item["result"] is None for item in self.public()["items"].values()))
        self.assertTrue(articles.check(self.root)["ok"])

    def test_single_bounded_inference_preserves_sources_reviews_and_stores_only_short_evidence(self):
        paths = ["site/data/latest.json", "site/data/ai-analysis.json", "reports/reviews.json", "reports/history.json", "data/raw.sqlite3"]
        before = {path: (self.root / path).read_bytes() for path in paths}
        result = self.run_good()
        self.assertEqual(result["last_attempt"]["completed_count"], 5)
        self.assertEqual(len(self.fetches), 5)
        self.assertEqual(len(self.calls), 1)
        payload, key, timeout = self.calls[0]
        self.assertEqual(key, ENV["GEMINI_API_KEY"])
        self.assertEqual(timeout, 45)
        self.assertIs(payload["store"], False)
        self.assertEqual(len(json.loads(payload["input"])["items"]), 5)
        self.assertNotIn("source_url", payload["input"])
        for file in ["state/article-analysis.json", "site/data/article-analysis.json"]:
            text = (self.root / file).read_text()
            self.assertNotIn("본문 비밀표식", text)
            self.assertNotIn("unit-test-key", text)
            self.assertNotIn('"paragraphs"', text)
        self.assertEqual(before, {path: (self.root / path).read_bytes() for path in paths})
        self.assertTrue(articles.check(self.root)["ok"])

    def test_cached_repeat_needs_no_key_fetch_or_api_and_retains_analysis_time(self):
        self.run_good()
        stamp = self.public()["items"][self.id]["analyzed_at"]
        result = self.run_good(environ={}, now=NOW + timedelta(hours=1))
        self.assertEqual(result["last_attempt"]["request_count"], 0)
        self.assertEqual(result["last_attempt"]["fetch_count"], 0)
        self.assertEqual(result["last_attempt"]["cached_count"], 5)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.fetches), 5)
        self.assertEqual(self.public()["items"][self.id]["analyzed_at"], stamp)

    def test_expired_cache_rechecks_bodies_and_reuses_same_content_without_ai(self):
        self.run_good()
        result = self.run_good(now=NOW + timedelta(days=1))
        self.assertEqual(result["last_attempt"]["fetch_count"], 5)
        self.assertEqual(result["last_attempt"]["request_count"], 0)
        self.assertEqual(result["last_attempt"]["cached_count"], 5)
        self.assertEqual(len(self.calls), 1)

    def test_changed_body_or_prompt_reanalyzes_and_changed_source_is_hidden_before_run(self):
        self.run_good()
        original = self.public()["items"][self.id]["input_fingerprint"]
        self.body["content_hash"] = "f" * 64
        self.run_good(now=NOW + timedelta(days=1))
        self.assertNotEqual(self.public()["items"][self.id]["input_fingerprint"], original)
        self.assertEqual(len(self.calls), 2)
        config = read(self.root / "config/articles.json")
        config["prompt_version"] = "article-brief-test-v3"
        write(self.root / "config/articles.json", config)
        articles.prepare(self.root, now=NOW)
        item = self.public()["items"][self.id]
        self.assertEqual(item["status"], "stale")
        self.assertIsNone(item["result"])
        self.assertEqual(len(read(self.root / "state/article-analysis.json")["entries"]), 5)

    def test_missing_key_or_free_confirmation_never_fetches_bodies(self):
        for env in [{}, {"AI_FREE_PROJECT_CONFIRMED": "true"}, {**ENV, "GEMINI_API_KEY": "bad\nkey"}]:
            result = self.run_good(environ=env)
            self.assertEqual(result["last_attempt"]["status"], "blocked")
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.calls, [])

    def test_editable_korean_bullet_prompt_reaches_api_and_changes_cache_fingerprint(self):
        self.run_good()
        prompt = (self.root / "config/article_prompt.txt").read_text().strip()
        self.assertEqual(self.calls[0][0]["system_instruction"], prompt)
        before = self.public()["items"][self.id]["input_fingerprint"]
        with (self.root / "config/article_prompt.txt").open("a") as handle:
            handle.write("\n검토 시 주요 조건의 누락을 다시 확인한다.\n")
        articles.prepare(self.root, now=NOW)
        self.assertEqual(self.public()["items"][self.id]["status"], "stale")
        self.run_good(now=NOW + timedelta(minutes=1))
        self.assertNotEqual(self.public()["items"][self.id]["input_fingerprint"], before)

    def test_whole_fetched_article_up_to_ten_thousand_characters_is_passed_including_ending(self):
        self.body["paragraphs"] = [*copy.deepcopy(PARAGRAPHS[:2]),
                                   *[{"id": f"p{i}", "text": "기사 본문의 주요 배경과 적용 조건을 설명한다. " * 70} for i in range(3, 7)],
                                   {"id": "p7", "text": "본문 후반부의 중요한 적용 제외 조건과 후속 일정 설명."}]
        self.run_good()
        inputs = json.loads(self.calls[0][0]["input"])["items"]
        self.assertTrue(all(source["paragraphs"][-1]["id"] == "p7" for source in inputs))
        self.assertTrue(all(source["truncated"] is False for source in inputs))

    def test_bullet_generation_schema_keeps_eight_bullets_and_reference_union_consistent(self):
        properties = articles.response_schema()["properties"]["items"]["items"]["properties"]
        self.assertEqual(properties["facts"]["maxItems"], 7)
        self.assertEqual(properties["key_change"]["properties"]["evidence_ids"]["maxItems"], 2)
        self.assertLessEqual(8 * 2, articles.MAX_EVIDENCE_PARAGRAPHS)

    def test_one_fetch_failure_leaves_other_briefs_and_existing_title_analysis_available(self):
        title_before = read(self.root / "site/data/ai-analysis.json")
        def one_failure(url, source):
            if not self.fetches:
                self.fetches.append(url)
                raise ArticleError("access_restricted")
            return self.fetch(url, source)
        result = articles.run(self.root, environ=ENV, fetcher=one_failure, transport=self.transport, now=NOW)
        self.assertEqual(result["counts"]["unavailable"], 1)
        self.assertEqual(result["counts"]["completed"], 4)
        self.assertEqual(result["last_attempt"]["status"], "partial_success")
        self.assertEqual(read(self.root / "site/data/ai-analysis.json"), title_before)

    def test_wrong_article_title_is_not_sent_to_ai(self):
        def wrong(url, source):
            return copy.deepcopy(self.body)
        result = articles.run(self.root, environ=ENV, fetcher=wrong, transport=self.transport, now=NOW)
        self.assertEqual(result["counts"]["unavailable"], 5)
        self.assertEqual(result["last_attempt"]["request_count"], 0)
        self.assertEqual(self.calls, [])

    def test_later_access_failure_preserves_canonical_cache_but_hides_old_public_brief(self):
        self.run_good()
        old = copy.deepcopy(read(self.root / "state/article-analysis.json")["entries"])
        def forbidden(*args):
            raise ArticleError("access_restricted")
        articles.run(self.root, environ=ENV, fetcher=forbidden, transport=self.transport, now=NOW + timedelta(days=1))
        self.assertEqual(read(self.root / "state/article-analysis.json")["entries"], old)
        item = self.public()["items"][self.id]
        self.assertEqual(item["status"], "unavailable")
        self.assertIsNone(item["result"])
        self.assertIsNone(item["analyzed_at"])

    def test_wrong_quote_paragraph_numbers_and_excessive_quotation_reject_output(self):
        variants = [lambda r: r["evidence"][0].update(quote="원문에 없는 인용"),
                    lambda r: r["key_change"].update(evidence_ids=["p99"]),
                    lambda r: r["facts"][0].update(text="투자 규모는 900억원이다."),
                    lambda r: r["facts"][0].update(text="투자 규모는 500만원이다."),
                    lambda r: r["facts"][0].update(evidence_ids=["p1"]),
                    lambda r: r["evidence"][0].update(quote="x" * 81),
                    lambda r: r.update(stage="완료했다고 추정"),
                    lambda r: r.update(extra="unexpected")]
        for change in variants:
            with self.subTest(change=change):
                result = articles.run(self.root, environ=ENV, fetcher=self.fetch,
                                      transport=lambda payload, *args: self.answer(payload, change), now=NOW)
                self.assertEqual(result["last_attempt"]["error_code"], "invalid_output")
                self.assertEqual(result["counts"]["completed"], 0)
                self.assertEqual(read(self.root / "state/article-analysis.json")["entries"], {})

    def test_numerical_tokens_do_not_match_substrings_or_drop_negative_signs(self):
        value = brief()
        value["facts"][0]["text"] = "투자 규모는 50억원이다."
        with self.assertRaises(ValueError):
            articles.validate_result(value, {row["id"]: row["text"] for row in PARAGRAPHS})
        value["facts"][0]["text"] = "변동률은 2.8%이다."
        paragraphs = {"p1": PARAGRAPHS[0]["text"], "p2": "변동률은 -2.8%이며 기관 고객 대상이다."}
        value["evidence"][1]["quote"] = "변동률은 -2.8%이며"
        with self.assertRaises(ValueError):
            articles.validate_result(value, paragraphs)

    def test_ai_failure_never_leaks_credentials_or_provider_response(self):
        def failure(*args):
            raise RuntimeError("unit-test-key private provider response")
        result = articles.run(self.root, environ=ENV, fetcher=self.fetch, transport=failure, now=NOW)
        self.assertEqual(result["last_attempt"]["error_code"], "network_error")
        self.assertNotIn("unit-test-key", json.dumps(result))
        self.assertNotIn("private provider response", (self.root / "state/article-analysis.json").read_text())

    def test_partial_response_saves_normal_briefs_and_only_marks_invalid_item_failed(self):
        def partial(payload, key, timeout):
            values = []
            for index, source in enumerate(json.loads(payload["input"])["items"]):
                result = brief()
                result.pop("evidence")
                if index == 0:
                    result["facts"][0]["text"] = "투자 규모는 900억원이다."
                values.append({"issue_id": source["issue_id"], **result})
            return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({"items": values}, ensure_ascii=False)}]}]}
        result = articles.run(self.root, environ=ENV, fetcher=self.fetch, transport=partial, now=NOW)
        self.assertEqual(result["last_attempt"]["status"], "partial_success")
        self.assertEqual(result["counts"]["completed"], 4)
        self.assertEqual(result["counts"]["failed"], 1)
        failed = next(item for item in self.public()["items"].values() if item["status"] == "failed")
        self.assertEqual(failed["reason_code"], "output_number_mismatch")
        self.assertEqual(len(read(self.root / "state/article-analysis.json")["entries"]), 4)
        self.assertTrue(articles.check(self.root)["ok"])

    def test_collection_rebuild_preserves_article_cache_and_never_fetches_or_calls_ai(self):
        from weekly_trend.live_pipeline import run_collection
        self.run_good()
        cached = read(self.root / "state/article-analysis.json")
        empty = {"status": "success", "records": [], "fetched_count": 0, "etag": None, "last_modified": None}
        with patch("weekly_trend.article_analysis.fetch_article", side_effect=AssertionError("no fetch")), patch("weekly_trend.ai.request_once", side_effect=AssertionError("no AI")):
            run_collection(self.root, collector=lambda *args, **kwargs: copy.deepcopy(empty), now=NOW)
        self.assertEqual(read(self.root / "state/article-analysis.json"), cached)
        self.assertEqual(self.public()["counts"]["completed"], 5)
        self.assertTrue(articles.check(self.root)["ok"])

    def test_body_failure_state_and_success_state_validate_in_real_browser_sidecar_code(self):
        self.run_good()
        def validate():
            command = ["node", "-e", "const fs=require('fs'),a=require(process.argv[1]);a.validateSidecar(JSON.parse(fs.readFileSync(process.argv[2])),JSON.parse(fs.readFileSync(process.argv[3])));",
                       str(ROOT / "site/article.js"), str(self.root / "site/data/article-analysis.json"), str(self.root / "site/data/latest.json")]
            return subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(validate().returncode, 0)
        def forbidden(*args):
            raise ArticleError("access_restricted")
        articles.run(self.root, environ=ENV, fetcher=forbidden, now=NOW + timedelta(days=1))
        validation = validate()
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_inconsistent_sidecar_and_atomic_write_failure_are_detected(self):
        self.run_good()
        before = [(self.root / path).read_bytes() for path in ["state/article-analysis.json", "site/data/article-analysis.json"]]
        real_replace = articles.os.replace
        calls = []
        def broken(source, dest):
            calls.append(dest)
            if len(calls) == 2:
                raise OSError("simulated replace failure")
            return real_replace(source, dest)
        with patch("weekly_trend.article_analysis.os.replace", side_effect=broken):
            with self.assertRaises(OSError):
                articles.prepare(self.root, now=NOW + timedelta(hours=1))
        self.assertEqual(before, [(self.root / path).read_bytes() for path in ["state/article-analysis.json", "site/data/article-analysis.json"]])
        data = self.public()
        data["counts"]["completed"] = 900
        write(self.root / "site/data/article-analysis.json", data)
        with self.assertRaises(ValueError):
            articles.check(self.root)


if __name__ == "__main__":
    unittest.main()

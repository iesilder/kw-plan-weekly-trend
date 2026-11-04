"""A single revised-input FSC correction never retries unrelated or valid news."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weekly_trend import fsc_repair

NOW = datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc)
ENV = {"AI_FREE_PROJECT_CONFIRMED": "true", "GEMINI_API_KEY": "synthetic-not-a-secret"}


def issue(number):
    return f"issue-{number:020x}"


class FscRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "data/fsc-ai-format-repair.json"
        self.title_states = {issue(i): "failed" for i in range(1, 7)}
        self.article_states = {issue(i): "completed" if i < 5 else "failed" for i in range(1, 7)}
        self.calls, self.verified = [], []
        self.keep_failed = set()

    def snapshot(self, *args):
        return {"titles": sorted(key for key, status in self.title_states.items() if status != "completed"),
                "articles": sorted(key for key, status in self.article_states.items() if status != "completed"),
                "title_states": dict(self.title_states), "article_states": dict(self.article_states)}

    def runner(self, kind):
        def execute(root, *, issue_ids, automatic, **kwargs):
            self.assertFalse(automatic)
            self.calls.append((kind, list(issue_ids)))
            states = self.title_states if kind == "titles" else self.article_states
            for key in issue_ids:
                states[key] = "failed" if key in self.keep_failed else "completed"
            return {"last_attempt": {"status": "partial_success" if self.keep_failed & set(issue_ids) else "success",
                    "error_code": "invalid_output" if self.keep_failed & set(issue_ids) else None,
                    "request_count": 1, "fetch_count": 0 if kind == "titles" else len(issue_ids),
                    "completed_count": len(set(issue_ids) - self.keep_failed)}}
        return execute

    def execute(self, environ=ENV):
        with patch.object(fsc_repair, "_snapshot", side_effect=self.snapshot):
            return fsc_repair.run(self.root, environ=environ, now=NOW,
                title_runner=self.runner("titles"), article_runner=self.runner("articles"),
                verifier=lambda root: self.verified.append(True))

    def test_only_six_failed_titles_two_failed_bodies_receive_one_call_each(self):
        preserved = ["data/fsc-ai-backfill.json", "data/daily-run.json", "reports/reviews.json", "reports/history.json"]
        for name in preserved:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("preserve-existing-state")
        result = self.execute()
        self.assertEqual(self.calls, [("titles", [issue(i) for i in range(1, 7)]),
                                     ("articles", [issue(5), issue(6)])])
        self.assertEqual(result["record"]["status"], "success")
        self.assertEqual((result["record"]["completed_titles"], result["record"]["completed_articles"]), (6, 2))
        for name in preserved:
            self.assertEqual((self.root / name).read_text(), "preserve-existing-state")
        self.calls.clear()
        saved = self.path.read_bytes()
        self.assertEqual(self.execute()["reason_code"], "already_attempted")
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(self.calls, [])

    def test_strict_guard_failure_is_recorded_without_any_second_pass(self):
        self.keep_failed = {issue(6)}
        result = self.execute()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result["record"]["status"], "partial_success")
        self.assertEqual(result["record"]["remaining_articles"], 1)
        self.calls.clear()
        self.execute()
        self.assertEqual(self.calls, [])

    def test_empty_initial_failures_claim_checkpoint_so_future_pushes_do_not_retry(self):
        self.title_states = {issue(1): "completed"}
        self.article_states = {issue(1): "completed"}
        self.assertEqual(self.execute()["record"]["status"], "success")
        self.assertEqual(self.calls, [])
        self.title_states[issue(2)] = self.article_states[issue(2)] = "failed"
        self.assertEqual(self.execute()["reason_code"], "already_attempted")
        self.assertEqual(self.calls, [])

    def test_missing_credentials_leave_correction_unclaimed(self):
        self.assertEqual(self.execute(environ={})["reason_code"], "ai_configuration_blocked")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.calls, [])

    def test_scope_caps_and_remainder_are_explicit(self):
        self.title_states = {issue(i): "failed" for i in range(1, 9)}
        self.article_states = {issue(i): "failed" for i in range(1, 4)}
        record = self.execute()["record"]
        self.assertEqual([len(scope) for _, scope in self.calls], [6, 2])
        self.assertEqual((record["untargeted_titles"], record["untargeted_articles"]), (2, 1))

    def test_running_checkpoint_resumes_only_unattempted_body_stage(self):
        self.execute()
        record = json.loads(self.path.read_text())
        record.update(status="running", completed_at=None)
        del record["stages"]["articles"]
        self.path.write_text(json.dumps(record))
        self.article_states[issue(5)] = self.article_states[issue(6)] = "failed"
        self.calls.clear()
        self.execute()
        self.assertEqual(self.calls, [("articles", [issue(5), issue(6)])])

    def test_invalid_checkpoint_cannot_widen_the_failed_scope(self):
        self.execute()
        record = json.loads(self.path.read_text())
        record["stages"]["articles"]["issue_ids"] = [issue(1)]
        self.path.write_text(json.dumps(record))
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_scope_filter_excludes_other_news_dart_good_cache_and_wrong_failure(self):
        body_inputs = {issue(i): {"reason": None, "source": {"adapter": "fsc_html"}, "metadata_fingerprint": "current"} for i in range(1, 7)}
        body_inputs[issue(2)]["source"]["adapter"] = "einfomax_rss"
        body_inputs[issue(3)] = {"reason": "not_article", "source": {"adapter": "dart_html"}}
        body_state = {"checks": {issue(i): {"status": "failed", "reason_code": "output_number_mismatch", "metadata_fingerprint": "current"} for i in range(1, 7)}}
        body_state["checks"][issue(5)]["reason_code"] = "network_error"
        body_state["checks"][issue(6)]["metadata_fingerprint"] = "old"
        body_public = {"items": {issue(i): {"status": "completed" if i == 4 else "failed"} for i in range(1, 7)}}
        title_state = {"failures": {str(i): {"issue_id": issue(i), "error_code": "invalid_output"} for i in range(1, 7)}}
        title_public = {"items": {issue(i): {"status": "completed" if i == 4 else "failed"} for i in range(1, 7)}}
        title_inputs = {issue(i): {"reason": None} for i in range(1, 7)}
        with patch.object(fsc_repair.ai, "_snapshot", return_value=({}, {})), \
             patch.object(fsc_repair.ai, "build_public", return_value=(title_state, title_public, title_inputs, [])), \
             patch.object(fsc_repair, "_original_migration", return_value={"stages": {"titles": {"error_code": "invalid_output", "issue_ids": [issue(i) for i in range(1, 6)]}}}), \
             patch.object(fsc_repair.article_analysis, "load_state", return_value=body_state), \
             patch.object(fsc_repair.article_analysis, "_public", return_value=(body_public, body_inputs)):
            value = fsc_repair._snapshot(self.root, NOW)
        self.assertEqual(value["titles"], [issue(1), issue(5)])
        self.assertEqual(value["articles"], [issue(1)])

    def test_workflow_corrective_pass_is_push_only(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/rss.yml").read_text()
        block = workflow.split("- name: Correct rejected FSC", 1)[1].split("- name: Verify the publishable", 1)[0]
        self.assertIn("if: github.event_name == 'push'", block)
        self.assertIn("python scripts/run_fsc_repair.py --root .", block)

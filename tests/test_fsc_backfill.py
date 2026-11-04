"""Finite FSC deployment migration; ordinary RSS remains metadata-only."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weekly_trend import fsc_backfill

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
ENV = {"AI_FREE_PROJECT_CONFIRMED": "true", "GEMINI_API_KEY": "synthetic-not-a-secret"}


def issue(number):
    return f"issue-{number:020x}"


class FscBackfillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.statuses = {issue(i): "pending" for i in range(1, 7)}
        self.calls, self.verified = [], []
        self.fail = set()
        self.path = self.root / "data/fsc-ai-backfill.json"

    def titles(self, root, *, issue_ids, automatic, **kwargs):
        self.assertTrue(automatic)
        self.calls.append(("titles", list(issue_ids)))
        return {"last_attempt": {"status": "success", "error_code": None,
                "request_count": 1, "completed_count": len(issue_ids)}}

    def articles(self, root, *, issue_ids, automatic, **kwargs):
        self.assertTrue(automatic)
        self.assertLessEqual(len(issue_ids), 5)
        self.calls.append(("articles", list(issue_ids)))
        failed = self.fail & set(issue_ids)
        for key in issue_ids:
            self.statuses[key] = "failed" if key in failed else "completed"
        return {"last_attempt": {"status": "partial_success" if failed else "success",
                "error_code": "partial_invalid_output" if failed else None,
                "request_count": 1, "fetch_count": len(issue_ids),
                "completed_count": len(issue_ids) - len(failed)}}

    def execute(self, **kwargs):
        with patch.object(fsc_backfill, "_fsc_items", side_effect=lambda *args: dict(self.statuses)):
            return fsc_backfill.run(self.root, now=NOW, environ=kwargs.pop("environ", ENV),
                title_runner=kwargs.pop("title_runner", self.titles), article_runner=self.articles,
                verifier=lambda root: self.verified.append(True), **kwargs)

    def test_six_items_use_one_title_two_disjoint_body_passes_and_never_repeat(self):
        for path in ("data/daily-run.json", "reports/reviews.json", "reports/history.json"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic-preserved-state")
        result = self.execute()
        self.assertEqual([name for name, _ in self.calls], ["titles", "articles", "articles"])
        self.assertEqual([len(scope) for name, scope in self.calls if name == "articles"], [5, 1])
        self.assertFalse(set(self.calls[1][1]) & set(self.calls[2][1]))
        self.assertEqual((result["record"]["completed_count"], result["record"]["pending_count"]), (6, 0))
        self.assertEqual(result["record"]["status"], "success")
        saved = self.path.read_bytes()
        self.calls.clear()
        self.assertEqual(self.execute()["reason_code"], "already_attempted")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), saved)
        for path in ("data/daily-run.json", "reports/reviews.json", "reports/history.json"):
            self.assertEqual((self.root / path).read_text(), "synthetic-preserved-state")

    def test_completed_fsc_is_not_selected_for_reanalysis(self):
        self.statuses[issue(1)] = self.statuses[issue(2)] = "completed"
        result = self.execute()
        expected = {issue(i) for i in range(3, 7)}
        self.assertEqual(set(result["record"]["target_issue_ids"]), expected)
        self.assertEqual([name for name, _ in self.calls], ["titles", "articles"])
        self.assertTrue(all(set(scope) == expected for _, scope in self.calls))

    def test_first_chunk_failure_is_not_retried_by_second_or_later_push(self):
        self.fail = {issue(1)}
        result = self.execute()
        self.assertEqual(sum(issue(1) in scope for name, scope in self.calls if name == "articles"), 1)
        self.assertEqual((result["record"]["status"], result["record"]["pending_count"]), ("partial_success", 1))
        self.calls.clear()
        self.execute()
        self.assertEqual(self.calls, [])

    def test_ten_target_cap_records_remainder_without_extra_calls(self):
        self.statuses = {issue(i): "pending" for i in range(1, 13)}
        result = self.execute()
        self.assertEqual(len(result["record"]["target_issue_ids"]), 10)
        self.assertEqual(result["record"]["untargeted_count"], 2)
        self.assertEqual([len(scope) for name, scope in self.calls if name == "articles"], [5, 5])

    def test_missing_configuration_does_not_consume_checkpoint(self):
        self.assertEqual(self.execute(environ={})["reason_code"], "ai_configuration_blocked")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.calls, [])

    def test_already_completed_initial_snapshot_claims_empty_migration(self):
        self.statuses = {issue(1): "completed"}
        self.assertEqual(self.execute()["reason_code"], "no_pending_fsc")
        self.assertEqual(json.loads(self.path.read_text())["target_issue_ids"], [])
        self.statuses[issue(2)] = "pending"
        self.assertEqual(self.execute()["reason_code"], "already_attempted")
        self.assertEqual(self.calls, [])

    def test_resume_only_unattempted_disjoint_stage(self):
        self.execute()
        record = json.loads(self.path.read_text())
        record.update(status="running", completed_at=None, completed_count=0, pending_count=6)
        del record["stages"]["articles_2"]
        self.path.write_text(json.dumps(record))
        self.statuses[issue(6)] = "pending"
        self.calls.clear()
        self.execute()
        self.assertEqual(self.calls, [("articles", [issue(6)])])

    def test_corrupt_or_overlapping_checkpoint_fails_before_network(self):
        self.path.parent.mkdir()
        self.path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.execute()
        self.path.unlink()
        self.execute()
        record = json.loads(self.path.read_text())
        record["stages"]["articles_2"]["issue_ids"] = [issue(1)]
        self.path.write_text(json.dumps(record))
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_unexpected_runner_failure_does_not_expose_exception_or_claim_zero_requests(self):
        def broken(*args, **kwargs):
            raise RuntimeError("SECRET provider response token=123")
        result = self.execute(title_runner=broken)
        stage = result["record"]["stages"]["titles"]
        self.assertEqual(stage["error_code"], "analysis_step_failed")
        self.assertIsNone(stage["request_count"])
        self.assertNotIn("SECRET", self.path.read_text())

    def test_only_validated_fsc_inputs_enter_scope(self):
        inputs = {issue(1): {"reason": None, "source": {"adapter": "fsc_html"}},
                  issue(2): {"reason": None, "source": {"adapter": "einfomax_rss"}},
                  issue(3): {"reason": "not_article", "source": {"adapter": "dart_html"}},
                  issue(4): {"reason": "unsafe_url", "source": {"adapter": "fsc_html"}}}
        public = {"items": {key: {"status": "pending"} for key in inputs}}
        with patch.object(fsc_backfill.ai, "_snapshot", return_value=({}, {})), \
             patch.object(fsc_backfill.article_analysis, "load_state", return_value={}), \
             patch.object(fsc_backfill.article_analysis, "_public", return_value=(public, inputs)):
            self.assertEqual(fsc_backfill._fsc_items(self.root, NOW), {issue(1): "pending"})

    def test_workflow_migration_step_is_push_only(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/rss.yml").read_text()
        block = workflow.split("- name: Summarize existing FSC", 1)[1].split("- name: Verify the publishable", 1)[0]
        self.assertIn("if: github.event_name == 'push'", block)
        self.assertIn("python scripts/run_fsc_backfill.py --root .", block)
        self.assertNotIn("schedule", block)

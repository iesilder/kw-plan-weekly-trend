import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch as mock_patch

from weekly_trend.reviews import (apply_patch, empty_reviews, load_reviews, parse_patch,
                                  validate_mirror, validate_reviews)

PROJECT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)
FIRST = "issue-" + "a" * 20
SECOND = "issue-" + "b" * 20
ARCHIVED = "issue-" + "c" * 20


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def change(issue_id=FIRST, status="accepted"):
    return {"issue_id": issue_id, "status": status}


def review_patch(*changes, revision=0):
    return {"schema_version": "1.0", "base_revision": revision, "changes": list(changes)}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="weekly-review-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        write_json(self.root / "reports/reviews.json", empty_reviews())
        write_json(self.root / "site/data/reviews.json", empty_reviews())
        write_json(self.root / "site/data/latest.json", {"is_demo": False, "candidates": [
            {"issue_id": FIRST, "is_demo": False}, {"issue_id": SECOND, "is_demo": False}]})
        write_json(self.root / "reports/history.json", {"schema_version": "1.0", "reports": []})
        write_json(self.root / "site/data/report-history.json", {"schema_version": "1.0", "reports": []})
        write_json(self.root / "config/pipeline.json", {"ai_enabled": False, "schedule_enabled": False})
        (self.root / "data").mkdir()
        (self.root / "data/raw.sqlite3").write_bytes(b"do not change article storage")

    def test_save_choices_and_public_mirror_without_touching_facts_or_reports(self):
        untouched = ["site/data/latest.json", "reports/history.json", "site/data/report-history.json",
                     "config/pipeline.json", "data/raw.sqlite3"]
        before = {name: (self.root / name).read_bytes() for name in untouched}
        result = apply_patch(self.root, review_patch(change(), change(SECOND, "held")), now=NOW)
        self.assertEqual(result, {"ok": True, "changed": True, "revision": 1, "change_count": 2})
        current = validate_mirror(self.root)
        self.assertEqual(current["decisions"][FIRST]["status"], "accepted")
        self.assertEqual(current["decisions"][SECOND]["status"], "held")
        self.assertEqual(current["updated_at"], "2026-11-03T02:00:00.000000+00:00")
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in untouched})

    def test_all_choice_transitions_and_reset(self):
        for revision, status in enumerate(["accepted", "held", "excluded", "unreviewed"]):
            result = apply_patch(self.root, review_patch(change(status=status), revision=revision),
                                 now=NOW + timedelta(minutes=revision))
            self.assertEqual(result["revision"], revision + 1)
        self.assertEqual(load_reviews(self.root)["decisions"], {})

    def test_same_choices_noop_does_not_rewrite_or_add_revision(self):
        apply_patch(self.root, review_patch(change()), now=NOW)
        before = (self.root / "reports/reviews.json").read_bytes()
        result = apply_patch(self.root, review_patch(change(), revision=1), now=NOW + timedelta(minutes=1))
        self.assertFalse(result["changed"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual((self.root / "reports/reviews.json").read_bytes(), before)

    def test_empty_patch_and_unreviewed_existing_default_are_noops(self):
        for value in [review_patch(), review_patch(change(status="unreviewed"))]:
            self.assertFalse(apply_patch(self.root, value, now=NOW)["changed"])
        self.assertEqual(load_reviews(self.root), empty_reviews())

    def test_stale_patch_rejected_even_if_it_repeats_saved_choice(self):
        value = review_patch(change())
        apply_patch(self.root, value, now=NOW)
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            apply_patch(self.root, value, now=NOW)
        self.assertEqual(load_reviews(self.root)["revision"], 1)

    def test_future_revision_rejected(self):
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            apply_patch(self.root, review_patch(change(), revision=99), now=NOW)

    def test_unavailable_ids_rejected_before_any_write(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            apply_patch(self.root, review_patch(change(), change(ARCHIVED)), now=NOW)
        self.assertEqual(load_reviews(self.root), empty_reviews())

    def test_archived_saved_choices_preserved_when_other_live_choice_changes(self):
        stored = {"schema_version": "1.0", "revision": 1, "updated_at": NOW.isoformat(),
                  "decisions": {ARCHIVED: {"status": "accepted", "updated_at": NOW.isoformat()}}}
        write_json(self.root / "reports/reviews.json", stored)
        write_json(self.root / "site/data/reviews.json", stored)
        apply_patch(self.root, review_patch(change(), revision=1), now=NOW)
        self.assertEqual(load_reviews(self.root)["decisions"][ARCHIVED], stored["decisions"][ARCHIVED])

    def test_demo_snapshot_and_demo_candidate_rejected(self):
        for snapshot in [{"is_demo": True, "candidates": [{"issue_id": FIRST, "is_demo": False}]},
                         {"is_demo": False, "candidates": [{"issue_id": FIRST, "is_demo": True}]}]:
            write_json(self.root / "site/data/latest.json", snapshot)
            with self.assertRaises(ValueError):
                apply_patch(self.root, review_patch(change()), now=NOW)

    def test_patch_rejects_extra_fields_and_injection_as_data(self):
        values = [
            {**review_patch(change()), "reports": [{"title": "fabricated"}]},
            review_patch({**change(), "title": "$(touch /tmp/not-a-command)"}),
            review_patch(change(status="reported")),
            review_patch(change(status={"accepted": True})),
            review_patch(change(issue_id="../../reports/history.json")),
            review_patch(change(), change()),
            review_patch(change(), revision=True),
            review_patch(change(), revision=-1),
            {**review_patch(), "schema_version": "2.0"},
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_patch(json.dumps(value))
        self.assertEqual(load_reviews(self.root), empty_reviews())

    def test_patch_rejects_oversized_input_and_change_count(self):
        with self.assertRaisesRegex(ValueError, "too large"):
            parse_patch(" " * 60_001)
        with self.assertRaisesRegex(ValueError, "at most 200"):
            parse_patch(json.dumps(review_patch(*[change() for _ in range(201)])))

    def test_duplicate_json_keys_and_invalid_json_rejected(self):
        for text in ['{"schema_version":"1.0","base_revision":0,"base_revision":1,"changes":[]}',
                     '{"schema_version":"1.0","base_revision":0,"changes":[{"issue_id":"x","status":"accepted","status":"held"}]}',
                     '{bad json}', '[]', 'null']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_patch(text)

    def test_canonical_store_rejects_invalid_metadata(self):
        valid = {"schema_version": "1.0", "revision": 1, "updated_at": NOW.isoformat(),
                 "decisions": {FIRST: {"status": "accepted", "updated_at": NOW.isoformat()}}}
        bad_values = [
            {**empty_reviews(), "revision": True},
            {**valid, "updated_at": None},
            {**valid, "updated_at": "2026-11-03T00:00:00"},
            {**valid, "revision": 0},
            {**valid, "reports": []},
            {**valid, "decisions": {FIRST: {"status": "reported", "updated_at": NOW.isoformat()}}},
            {**valid, "decisions": {FIRST: {"status": [], "updated_at": NOW.isoformat()}}},
            {**valid, "decisions": {FIRST: {"status": "accepted", "updated_at": (NOW + timedelta(days=1)).isoformat()}}},
        ]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_reviews(value)

    def test_corrupt_public_mirror_is_not_overwritten(self):
        write_json(self.root / "site/data/reviews.json", {"broken": True})
        with self.assertRaises(ValueError):
            apply_patch(self.root, review_patch(change()), now=NOW)
        self.assertEqual(load_reviews(self.root), empty_reviews())

    def test_missing_store_compatibility_but_missing_existing_mirror_rejected(self):
        (self.root / "site/data/reviews.json").unlink()
        with self.assertRaisesRegex(ValueError, "mirror is missing"):
            validate_mirror(self.root)
        (self.root / "reports/reviews.json").unlink()
        self.assertEqual(validate_mirror(self.root), empty_reviews())
        self.assertTrue(apply_patch(self.root, review_patch(change()), now=NOW)["changed"])
        self.assertEqual(validate_mirror(self.root)["revision"], 1)

    def test_timestamp_cannot_move_backwards_or_be_naive(self):
        apply_patch(self.root, review_patch(change()), now=NOW)
        for stamp in [NOW.replace(tzinfo=None), NOW - timedelta(minutes=1)]:
            with self.assertRaises(ValueError):
                apply_patch(self.root, review_patch(change(status="held"), revision=1), now=stamp)
        self.assertEqual(load_reviews(self.root)["decisions"][FIRST]["status"], "accepted")

    def test_second_file_replace_failure_rolls_back_first_file(self):
        original_replace = os.replace
        count = 0

        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("simulated mirror write failure")
            return original_replace(source, target)

        before = {name: (self.root / name).read_bytes() for name in ["reports/reviews.json", "site/data/reviews.json"]}
        with mock_patch("weekly_trend.reviews.os.replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated"):
                apply_patch(self.root, review_patch(change()), now=NOW)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})

    def test_cli_reads_stdin_and_reports_clear_conflict_without_patch_echo(self):
        command = [sys.executable, str(PROJECT / "scripts/apply_reviews.py"), "--root", str(self.root)]
        raw = json.dumps(review_patch(change()))
        result = subprocess.run(command, input=raw, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["revision"], 1)
        again = subprocess.run(command, input=raw, text=True, capture_output=True)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("revision conflict", again.stderr)
        self.assertNotIn(FIRST, again.stderr)

    def test_live_collection_preserves_reviews_and_empty_report_history(self):
        from weekly_trend.live_pipeline import run_collection
        from weekly_trend.pipeline import verify
        # Start from a valid copied config and create a new, isolated live store.
        shutil.rmtree(self.root / "data")
        shutil.rmtree(self.root / "config")
        shutil.rmtree(self.root / "site/data")
        shutil.copytree(PROJECT / "config", self.root / "config")
        stored = {"schema_version": "1.0", "revision": 1, "updated_at": NOW.isoformat(),
                  "decisions": {ARCHIVED: {"status": "held", "updated_at": NOW.isoformat()}}}
        write_json(self.root / "reports/reviews.json", stored)
        success = {"status": "success", "records": [], "fetched_count": 0, "etag": None, "last_modified": None}
        run_collection(self.root, collector=lambda *args, **kwargs: copy.deepcopy(success), now=NOW)
        self.assertEqual(validate_mirror(self.root), stored)
        self.assertTrue(verify(self.root)["ok"])
        self.assertEqual(json.loads((self.root / "site/data/report-history.json").read_text())["reports"], [])
        failure = {"status": "failed", "records": [], "fetched_count": 0, "error_code": "network_error"}
        run_collection(self.root, collector=lambda *args, **kwargs: copy.deepcopy(failure), now=NOW + timedelta(minutes=1))
        self.assertEqual(validate_mirror(self.root), stored)
        self.assertTrue(verify(self.root)["ok"])


if __name__ == "__main__":
    unittest.main()

"""Synthetic fixtures keep registry/sidecar checks independent of live data."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from weekly_trend import interests

NOW = datetime(2026, 11, 3, 16, 30, tzinfo=timezone.utc)  # Nov 4 in Korea.
STAMP = NOW.isoformat()
NEWS_ID = "issue-" + "a" * 20
DART_ID = "issue-" + "b" * 20
UNKNOWN_ID = "issue-" + "c" * 20
GENERATION = "live-synthetic-interests"


def record(number=1, **changes):
    result = {"id": f"interest-{number}", "issue_number": number, "title": "AI 고객 서비스", "question": "경쟁사의 AI 서비스는 어떻게 바뀌는가?",
              "keywords": ["AI", "챗봇"], "exclude_keywords": ["채용"], "topic_id": "digital", "active_from": "2026-11-01", "active_until": None,
              "updated_at": STAMP, "source_url": interests.ISSUE_URL + str(number), "enabled": True}
    result.update(changes)
    return result


def form(**changes):
    fields = {"관심 주제": "AI 고객 서비스", "보고 질문": "경쟁사의 AI 서비스는\n어떻게 바뀌는가?", "검색어": "AI, 챗봇", "제외 검색어": "채용",
              "분류": "digital", "시작일": "_No response_", "종료일": "_No response_", "공개 저장 동의": "- [X] " + interests.CONSENT}
    fields.update(changes)
    return "\n\n".join("### " + heading + "\n\n" + fields[heading] for heading in interests.HEADINGS)


def issue(number=1, **changes):
    result = {"number": number, "title": "[관심사] AI 고객 서비스", "body": form(), "state": "open", "user": {"login": "iesilder"},
              "html_url": interests.ISSUE_URL + str(number), "updated_at": STAMP}
    result.update(changes)
    return result


class InterestsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.save("config/interests.json", {"schema_version": "1.0", "interests": []})
        self.save("config/sources.json", {"schema_version": "1.0", "sources": [
            {"id": "rss", "type": "rss", "adapter": "einfomax_rss", "enabled": True, "policy_status": "verified", "retention": "metadata_only"},
            {"id": "dart", "type": "dart_html", "adapter": "dart_html", "enabled": True, "policy_status": "verified", "retention": "metadata_only"}]})
        self.latest = {"schema_version": "1.0", "generation_id": GENERATION, "is_demo": False, "candidates": []}
        self.details = {}
        for candidate_id, source_id in ((NEWS_ID, "rss"), (DART_ID, "dart"), (UNKNOWN_ID, "unknown")):
            candidate = {"issue_id": candidate_id, "title": "증권사 AI 챗봇 서비스 도입", "topic_id": "strategy", "content_filter": "topics", "is_demo": False,
                         "fetched_at": STAMP,
                         "detail_path": "data/issues/" + candidate_id + ".json"}
            detail = {**candidate, "generation_id": GENERATION, "evidence": [{"source_id": source_id, "is_demo": False, "fetched_at": STAMP}]}
            self.latest["candidates"].append(candidate)
            self.details[candidate_id] = detail
            self.save("site/data/issues/" + candidate_id + ".json", detail)
        self.save("site/data/latest.json", self.latest)

    def save(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def records(self, values):
        self.save("config/interests.json", {"schema_version": "1.0", "interests": values})

    def sync(self, issues, now=NOW, **kwargs):
        return interests.sync_from_github(self.root, environ={"GITHUB_TOKEN": "synthetic-test-token"},
                                          transport=lambda url, token, timeout: copy.deepcopy(issues), now=now, **kwargs)

    def test_exact_record_schema_bounded_values_and_legacy_empty_only(self):
        self.records([record()])
        self.assertEqual(interests.load_interests(self.root), [record()])
        invalid = [record(extra="no"), record(id="interest-2"), record(issue_number=True), record(title="x" * 121),
                   record(question="x" * 1001), record(keywords=[]), record(keywords=[str(i) for i in range(11)]),
                   record(exclude_keywords=["x" * 81]), record(keywords=["AI", "ai"]), record(topic_id="all"), record(enabled=1),
                   record(active_from="2026-02-30"), record(active_until="2026-10-31"), record(updated_at="yesterday"),
                   record(source_url="https://github.com/another/repo/issues/1")]
        for value in invalid:
            with self.subTest(value=value):
                self.records([value])
                with self.assertRaises((ValueError, interests.InterestError)):
                    interests.load_interests(self.root)
        self.save("config/interests.json", {"interests": []})
        self.assertEqual(interests.load_interests(self.root), [])
        self.save("config/interests.json", {"interests": [record()]})
        with self.assertRaises(ValueError):
            interests.load_interests(self.root)

    def test_blank_start_uses_issue_registration_date_even_when_next_batch_is_later(self):
        saved = issue(7, created_at="2026-11-02T16:00:00Z")  # Nov 3 in Korea.
        self.assertTrue(self.sync([saved])["ok"])
        self.assertEqual(interests.load_interests(self.root)[0]["active_from"], "2026-11-03")
        self.assertTrue(self.sync([saved], now=NOW + timedelta(days=1))["ok"])
        self.assertEqual(interests.load_interests(self.root)[0]["active_from"], "2026-11-03")

    def test_inclusive_korean_dates_and_cap_concurrent_not_historical(self):
        values = [record(1, active_from="2026-11-04", active_until="2026-11-04"), record(2, active_until="2026-11-03"),
                  record(3, active_from="2026-11-05"), record(4, enabled=False)]
        self.records(values)
        self.assertEqual([item["id"] for item in interests.load_interests(self.root, NOW, active_only=True)], ["interest-1"])
        historical = [record(i, active_from="2026-01-01", active_until="2026-01-02") for i in range(1, 21)]
        self.records(historical + [record(21)])
        self.assertEqual(len(interests.load_interests(self.root)), 21)
        self.records([record(i) for i in range(1, 22)])
        with self.assertRaisesRegex(interests.InterestError, "active_limit"):
            interests.load_interests(self.root)

    def test_casefold_ascii_boundaries_korean_spacing_and_exclusions(self):
        values = [record(1), record(2, keywords=["자본 시장"], exclude_keywords=["해외 시장"]), record(3, keywords=["T+1"], exclude_keywords=[])]
        self.assertEqual(interests.match_title("증권사 ai 챗봇 도입", values), [{"interest_id": "interest-1", "keywords": ["AI", "챗봇"]}])
        self.assertEqual(interests.match_title("AIs와 XAI 서비스", values), [])
        self.assertEqual(interests.match_title("AI 채용 공고", values), [])
        self.assertEqual(interests.match_title("자본시장 개편", values), [{"interest_id": "interest-2", "keywords": ["자본 시장"]}])
        self.assertEqual(interests.match_title("해외시장 자본시장 개편", values), [])
        self.assertEqual(interests.match_title("T+1 결제 도입", values), [{"interest_id": "interest-3", "keywords": ["T+1"]}])
        self.assertEqual(interests.match_title("T+10 결제", values), [])

    def test_fingerprint_ignores_question_dates_and_title_but_tracks_matching_fields(self):
        original = record()
        digest = interests.interest_fingerprint(original)
        self.assertRegex(digest, r"^[a-f0-9]{64}$")
        self.assertEqual(digest, interests.interest_fingerprint(record(title="다른 설명", question="질문 변경", active_from="2027-01-01", enabled=False)))
        for changes in ({"keywords": ["ETF"]}, {"exclude_keywords": []}, {"topic_id": "risk"}):
            self.assertNotEqual(digest, interests.interest_fingerprint(record(**changes)))

    def test_sync_owner_form_without_labels_only_and_never_pr_or_request(self):
        items = [issue(), issue(2, user={"login": "someone"}, body="bad unauthorized body"),
                 issue(3, pull_request={"url": "unused"}, html_url="https://github.com/iesilder/kw-plan-weekly-trend/pull/3"),
                 issue(4, title=interests.REQUEST_TITLE, body="- [X] 수집 요청"), issue(5, title="ordinary issue", body=None)]
        calls = []
        def transport(url, token, timeout):
            calls.append((url, token, timeout))
            return items
        result = interests.sync_from_github(self.root, environ={"GITHUB_TOKEN": "synthetic-test-token"}, transport=transport, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(calls, [(interests.API_URL + "?state=open&creator=iesilder&per_page=100&page=1", "synthetic-test-token", interests.TIMEOUT)])
        values = interests.load_interests(self.root)
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["active_from"], "2026-11-04")
        self.assertIsNone(values[0]["active_until"])
        self.assertEqual(values[0]["question"], "경쟁사의 AI 서비스는 어떻게 바뀌는가?")
        self.assertEqual(values[0]["keywords"], ["AI", "챗봇"])
        metadata = json.loads((self.root / "data/interests-registry.json").read_text())
        self.assertEqual(metadata["status"], "success")
        self.assertNotIn("body", metadata)
        self.assertNotIn("synthetic-test-token", (self.root / "data/interests-registry.json").read_text())

    def test_sync_blank_start_stable_edit_then_close_disables_preserves_history(self):
        self.assertTrue(self.sync([issue()])["ok"])
        edit = issue(body=form(**{"보고 질문": "바뀐 질문"}), updated_at=(NOW + timedelta(days=1)).isoformat())
        self.assertTrue(self.sync([edit], now=NOW + timedelta(days=1))["ok"])
        value = interests.load_interests(self.root)[0]
        self.assertEqual(value["active_from"], "2026-11-04")
        self.assertEqual(value["question"], "바뀐 질문")
        self.assertTrue(self.sync([], now=NOW + timedelta(days=2))["ok"])
        closed = interests.load_interests(self.root)[0]
        self.assertFalse(closed["enabled"])
        self.assertEqual(closed["question"], "바뀐 질문")
        self.assertEqual(interests.load_interests(self.root, now=NOW, active_only=True), [])
        stamp = closed["updated_at"]
        self.sync([], now=NOW + timedelta(days=3))
        self.assertEqual(interests.load_interests(self.root)[0]["updated_at"], stamp)

    def test_unchanged_form_title_and_prefix_only_use_required_subject_field(self):
        for title in ("[관심사] 관심사항 등록", "[관심사] ", "[관심사]"):
            with self.subTest(title=title):
                result = self.sync([issue(title=title)])
                self.assertTrue(result["ok"])
                self.assertEqual(interests.load_interests(self.root)[0]["title"], "AI 고객 서비스")

    def test_korean_form_topic_labels_normalize_to_existing_ids(self):
        for label, topic_id in interests.TOPIC_LABELS.items():
            with self.subTest(label=label):
                self.assertTrue(self.sync([issue(body=form(**{"분류": label}))])["ok"])
                self.assertEqual(interests.load_interests(self.root)[0]["topic_id"], topic_id)

    def test_bad_owner_form_consent_or_api_never_partially_overwrites_snapshot(self):
        self.records([record(20)])
        original = (self.root / "config/interests.json").read_bytes()
        invalid = [issue(body=form(**{"공개 저장 동의": "- [ ] " + interests.CONSENT})),
                   issue(body=form().replace("### 검색어", "### 검색어\n\nAI\n\n### 검색어")),
                   issue(body=form() + "\n\n### 알 수 없는 항목\n\nno"),
                   issue(body=form(**{"검색어": ""})), issue(body=form(**{"시작일": "2026-11-04", "종료일": "2026-11-03"})),
                   issue(html_url="https://github.com/evil/repo/issues/1"), issue(updated_at="bad-time")]
        for bad in invalid:
            with self.subTest(bad=bad):
                result = self.sync([issue(2), bad])
                self.assertFalse(result["ok"])
                self.assertEqual((self.root / "config/interests.json").read_bytes(), original)
        result = self.sync([issue(i) for i in range(1, 22)])
        self.assertEqual(result["error_code"], "active_limit")
        self.assertEqual((self.root / "config/interests.json").read_bytes(), original)

    def test_missing_key_and_network_failure_keep_config_and_last_success(self):
        self.sync([issue()])
        original = (self.root / "config/interests.json").read_bytes()
        def fail(*args):
            raise OSError("sensitive arbitrary response never logged")
        failure = interests.sync_from_github(self.root, environ={"GITHUB_TOKEN": "test"}, transport=fail, now=NOW + timedelta(hours=1))
        self.assertEqual(failure["error_code"], "network_error")
        self.assertEqual(failure["last_success_at"], interests._stamp(NOW))
        blocked = interests.sync_from_github(self.root, environ={}, transport=fail, now=NOW + timedelta(hours=2))
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["request_count"], 0)
        self.assertEqual((self.root / "config/interests.json").read_bytes(), original)
        self.assertNotIn("sensitive", (self.root / "data/interests-registry.json").read_text())

    def test_bounded_pagination_requires_complete_validated_snapshot(self):
        self.records([record(900)])
        original = (self.root / "config/interests.json").read_bytes()
        calls = []
        def pages(url, token, timeout):
            calls.append(url)
            page = len(calls)
            return [issue(i, title="unrelated") for i in range((page - 1) * 100 + 1, page * 100 + 1)] if page == 1 else [issue(101)]
        result = interests.sync_from_github(self.root, environ={"GITHUB_TOKEN": "test"}, transport=pages, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["request_count"], 2)
        self.assertEqual([value["id"] for value in interests.load_interests(self.root) if value["enabled"]], ["interest-101"])
        self.records([record(900)])
        calls.clear()
        def never_complete(url, token, timeout):
            page = len(calls) + 1
            calls.append(url)
            return [issue(i, title="unrelated") for i in range((page - 1) * 100 + 1, page * 100 + 1)]
        result = interests.sync_from_github(self.root, environ={"GITHUB_TOKEN": "test"}, transport=never_complete, now=NOW)
        self.assertEqual(result["error_code"], "pagination_limit")
        self.assertEqual(len(calls), interests.MAX_PAGES)
        self.assertEqual((self.root / "config/interests.json").read_bytes(), original)
        for payload in ({"message": "bad API"}, [issue(), issue()], [None]):
            self.assertFalse(self.sync(payload)["ok"])
            self.assertEqual((self.root / "config/interests.json").read_bytes(), original)

    def test_success_save_rolls_back_config_on_registry_replace_failure(self):
        self.records([record(30)])
        old = (self.root / "config/interests.json").read_bytes()
        real_replace = interests.os.replace
        def replace(source, target):
            if str(target).endswith("data/interests-registry.json"):
                raise OSError("injected save failure")
            return real_replace(source, target)
        with mock.patch.object(interests.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.sync([issue()])
        self.assertEqual((self.root / "config/interests.json").read_bytes(), old)
        self.assertFalse((self.root / "data/interests-registry.json").exists())

    def test_sidecar_only_news_no_dart_unknown_or_topic_equality_requirement(self):
        self.records([record()])
        public = interests.build_public(self.root, self.latest, self.details, now=NOW)
        self.assertEqual(public["generation_id"], GENERATION)
        self.assertEqual(public["registry"]["status"], "never_synced")
        self.assertIsNone(public["last_collection"])
        value = public["interests"][0]
        self.assertEqual(value["matches"], [{"issue_id": NEWS_ID, "keywords": ["AI", "챗봇"]}])
        self.assertEqual(value["match_count"], 1)
        self.assertEqual(value["collection_status"], "never_collected")
        # A misleading topics flag cannot permit mixed DART evidence.
        self.details[NEWS_ID]["evidence"].append({"source_id": "dart", "is_demo": False})
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW)["interests"][0]["matches"], [])

    def test_sidecar_collection_status_staleness_expiry_and_disabled(self):
        values = [record(), record(2, active_until="2026-11-03"), record(3, enabled=False), record(4, active_from="2026-12-01")]
        self.records(values)
        book = {"schema_version": "1.0", "entries": {"interest-1": {"filter_fingerprint": interests.interest_fingerprint(values[0]),
                "last_success_at": STAMP, "sources": {}, "status": "partial_success"}}, "last_attempt": {"mode": "manual", "started_at": STAMP,
                "completed_at": STAMP, "status": "partial_success", "request_url": interests.ISSUE_URL + "50", "error_code": "network_error"}}
        public = interests.build_public(self.root, self.latest, self.details, now=NOW, collection=book)
        self.assertEqual(public["last_collection"], book["last_attempt"])
        self.assertEqual([value["collection_status"] for value in public["interests"]], ["partial_success", "expired", "disabled", "disabled"])
        self.assertEqual([value["match_count"] for value in public["interests"]], [1, 0, 0, 0])
        self.assertEqual(public["interests"][0]["last_collected_at"], STAMP)
        book["entries"]["interest-1"]["filter_fingerprint"] = "f" * 64
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW, collection=book)["interests"][0]["collection_status"], "stale")

    def test_recent_window_uses_source_update_and_never_refresh_poll_for_old_news(self):
        self.records([record()])
        evidence = self.details[NEWS_ID]["evidence"][0]
        evidence["published_date"] = "2026-08-01"
        # A recent polling time does not make old publication dates current.
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW)["interests"][0]["match_count"], 0)
        evidence["source_updated_at"] = STAMP
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW)["interests"][0]["match_count"], 1)
        del evidence["source_updated_at"]
        # Date-only cutoff is inclusive in Korea, where NOW is Nov 4.
        evidence["published_date"] = "2026-10-05"
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW)["interests"][0]["match_count"], 1)
        evidence["published_date"] = "2026-10-04"
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW)["interests"][0]["match_count"], 0)
        del evidence["published_date"]
        evidence["first_fetched_at"] = "2026-08-01T00:00:00Z"
        self.assertEqual(interests.build_public(self.root, self.latest, self.details, now=NOW)["interests"][0]["match_count"], 0)

    def test_prepare_check_offline_binds_config_registry_book_and_snapshot(self):
        self.assertEqual(interests.check(self.root)["mode"], "not_prepared")
        self.records([record()])
        with mock.patch.object(interests, "request_page", side_effect=AssertionError("offline only")):
            self.assertEqual(interests.prepare(self.root, now=NOW)["request_count"], 0)
            self.assertTrue(interests.check(self.root)["ok"])
        self.records([record(question="새 질문")])
        with self.assertRaises(ValueError):
            interests.check(self.root)
        interests.prepare(self.root, now=NOW)
        self.latest["generation_id"] = "different-generation"
        self.save("site/data/latest.json", self.latest)
        with self.assertRaises(ValueError):
            interests.check(self.root)

    def test_bad_detail_path_or_generation_and_duplicate_json_rejected(self):
        self.records([record()])
        self.latest["candidates"][0]["detail_path"] = "../../outside.json"
        with self.assertRaises(ValueError):
            interests.build_public(self.root, self.latest, now=NOW)
        self.details[NEWS_ID]["generation_id"] = "stale"
        with self.assertRaises(ValueError):
            interests.build_public(self.root, self.latest, self.details, now=NOW)
        (self.root / "config/interests.json").write_text('{"schema_version":"1.0","interests":[],"interests":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            interests.load_interests(self.root)

    def test_form_templates_keep_contract_and_separate_request_confirmation(self):
        root = Path(__file__).resolve().parents[1]
        registration = (root / ".github/ISSUE_TEMPLATE/executive-interest.yml").read_text()
        manual = (root / ".github/ISSUE_TEMPLATE/interest-collection.yml").read_text()
        for heading in interests.HEADINGS:
            self.assertIn("label: " + heading, registration)
        self.assertIn(interests.CONSENT, registration)
        self.assertIn('title: "[관심사] 관심사항 등록"', registration)
        self.assertIn('title: "[관심사 수집 요청]"', manual)
        self.assertIn("활성 최근 관심 주제 수집을 요청합니다.", manual)
        self.assertNotIn("label: 검색어", manual)
        self.assertNotIn("labels:", registration)


if __name__ == "__main__":
    unittest.main()

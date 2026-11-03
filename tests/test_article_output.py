"""Regression tests for real-output punctuation and independent item validation."""

import copy
import json
import unittest

from weekly_trend import article_analysis as articles

P = [{"id": "p1", "text": "증권 결제주기를 T+1, 즉 하루 뒤로 단축하는 방안을 추진한다."},
     {"id": "p2", "text": "투자 규모는 1,500억원이며, 변동률은 -2.8%로 제시됐다."}]
A, B = "issue-" + "a" * 20, "issue-" + "b" * 20


def item(issue_id=A):
    return {"issue_id": issue_id,
            "key_change": {"text": "기사에서 T+1, 결제주기 단축 방안을 추진한다고 밝혔다.", "evidence_ids": ["p1"]},
            "facts": [{"text": "투자 규모는 1,500억원이다.", "evidence_ids": ["p2"]}],
            "stage": "추진·협약", "why_candidate": "결제 인프라 변경 관점에서 검토할 수 있습니다."}


def response(items):
    return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({"items": items}, ensure_ascii=False)}]}]}


class ArticleOutputTests(unittest.TestCase):
    def batch(self):
        return [{"issue_id": key, "paragraphs": copy.deepcopy(P)} for key in [A, B]]

    def test_punctuation_is_not_part_of_number_and_thousands_decimal_signs_remain_exact(self):
        self.assertEqual(articles._numbers("T+1, 단축"), articles._numbers("T+1을 단축"))
        self.assertEqual(articles._numbers("T+1."), {"+1"})
        self.assertEqual(articles._numbers("1,500억원, -2.8%이다."), {"1,500억원", "-2.8%"})
        self.assertNotEqual(articles._numbers("1,500억원"), articles._numbers("150억원"))
        self.assertNotEqual(articles._numbers("-2.8%"), articles._numbers("2.8%"))

    def test_model_selects_paragraphs_and_program_builds_exact_bounded_excerpts(self):
        valid, errors = articles._parse(response([item(A), item(B)]), self.batch())
        self.assertEqual(set(valid), {A, B})
        self.assertEqual(errors, {})
        paragraphs = {row["id"]: row["text"] for row in P}
        for value in valid.values():
            self.assertLessEqual(sum(len(row["quote"]) for row in value["evidence"]), 200)
            for row in value["evidence"]:
                self.assertIn(row["quote"], paragraphs[row["id"]])
            articles.validate_result(value, paragraphs)

    def test_one_bad_amount_only_rejects_that_article(self):
        bad = item(B)
        bad["facts"][0]["text"] = "투자 규모는 9,000억원이다."
        valid, errors = articles._parse(response([item(A), bad]), self.batch())
        self.assertEqual(set(valid), {A})
        self.assertEqual(errors, {B: "output_number_mismatch"})

    def test_unknown_paragraph_and_malformed_shape_have_safe_per_article_codes(self):
        bad = item(B)
        bad["key_change"]["evidence_ids"] = ["p99"]
        valid, errors = articles._parse(response([item(A), bad]), self.batch())
        self.assertEqual(set(valid), {A})
        self.assertEqual(errors[B], "output_evidence_mismatch")
        valid, errors = articles._parse(response([item(A), {"issue_id": B}]), self.batch())
        self.assertEqual(set(valid), {A})
        self.assertEqual(errors[B], "output_invalid_shape")

    def test_missing_duplicate_and_foreign_items_never_steal_another_articles_result(self):
        valid, errors = articles._parse(response([item(A), item(A)]), self.batch())
        self.assertFalse(valid)
        self.assertEqual(errors, {A: "output_duplicate_item", B: "output_missing_item"})
        valid, errors = articles._parse(response([None, item(A), item("issue-" + "c" * 20)]), self.batch())
        self.assertEqual(set(valid), {A})
        self.assertEqual(errors, {B: "output_missing_item"})

    def test_excerpt_budget_is_deterministic_for_four_long_paragraphs(self):
        paragraphs = {f"p{i}": f"검증 문단의 실제 정보입니다. {i}번째 데이터 확인 내용입니다. " * 10 for i in range(1, 5)}
        value = {"key_change": {"text": "검증 문단 정보입니다.", "evidence_ids": ["p1"]},
                 "facts": [{"text": "데이터 확인 내용입니다.", "evidence_ids": [f"p{i}"]} for i in range(2, 5)],
                 "stage": "불명", "why_candidate": "담당자 검토가 필요합니다."}
        value["evidence"] = articles._source_excerpts(value, paragraphs)
        self.assertLessEqual(sum(len(row["quote"]) for row in value["evidence"]), 200)
        self.assertTrue(all(len(row["quote"]) <= 50 for row in value["evidence"]))
        articles.validate_result(value, paragraphs)

    def test_new_model_schema_does_not_require_generated_quotations(self):
        props = articles.response_schema()["properties"]["items"]["items"]["properties"]
        self.assertNotIn("evidence", props)
        self.assertIn("evidence_ids", props["key_change"]["properties"])


if __name__ == "__main__":
    unittest.main()

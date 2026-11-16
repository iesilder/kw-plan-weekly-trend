"""Regression tests for real-output punctuation and independent item validation."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
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
        self.assertEqual(errors[B], "output_evidence_unknown")
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

    def test_shared_paragraph_preview_prefers_both_claims_when_they_fit(self):
        paragraph = "배경 설명입니다. " * 15 + "고객자산 578조7천억원, 고객 수 52만4천명으로 증가했다. " + "추가 설명입니다. " * 15
        claims = ["고객자산 578조7천억원", "고객 수 52만4천명"]
        quote = articles._paragraph_preview(paragraph, claims, 80)
        self.assertIn("578조7천억원", quote)
        self.assertIn("52만4천명", quote)
        self.assertIn(quote, paragraph)
        self.assertLessEqual(len(quote), 80)

    def test_preview_is_identical_across_independent_hash_seeds(self):
        code = """from weekly_trend.article_analysis import _paragraph_preview
p = '매출은 100억원이고 순이익은 200억원이며 전년 대비 10.0% 증가했다. ' + '배경 설명. ' * 30
print(_paragraph_preview(p, ['매출 100억원, 순이익 200억원, 증가율 10.0%'], 80))
"""
        outputs = [subprocess.check_output([sys.executable, "-c", code],
                   cwd=Path(__file__).resolve().parents[1],
                   env={**os.environ, "PYTHONHASHSEED": str(seed)}, text=True)
                   for seed in (1, 2, 3, 42)]
        self.assertEqual(len(set(outputs)), 1)
        self.assertIn("100억원", outputs[0])
        self.assertIn("200억원", outputs[0])
        self.assertIn("10.0%", outputs[0])

    def test_limited_preview_does_not_replace_full_paragraph_numeric_validation(self):
        paragraph = "매출은 100억원으로 집계됐다. " + "사업 배경 설명입니다. " * 30 + "순이익은 200억원으로 집계됐다."
        result = {"key_change": {"text": "매출은 100억원이다.", "evidence_ids": ["p1"]},
                  "facts": [{"text": "순이익은 200억원이다.", "evidence_ids": ["p1"]}],
                  "stage": "성과", "why_candidate": "실적 변화 검토 자료입니다."}
        paragraphs = {"p1": paragraph}
        result["evidence"] = articles._source_excerpts(result, paragraphs)
        quote = result["evidence"][0]["quote"]
        self.assertFalse("100억원" in quote and "200억원" in quote)
        articles.validate_result(result, paragraphs)
        result["facts"][0]["text"] = "순이익은 999억원이다."
        with self.assertRaises(articles.OutputError) as raised:
            articles.validate_result(result, paragraphs)
        self.assertEqual(raised.exception.code, "output_number_mismatch")

    def test_new_model_schema_does_not_require_generated_quotations(self):
        props = articles.response_schema()["properties"]["items"]["items"]["properties"]
        self.assertNotIn("evidence", props)
        self.assertIn("evidence_ids", props["key_change"]["properties"])

    def test_five_valid_paragraphs_are_not_rejected_by_a_global_four_reference_limit(self):
        paragraphs = [{"id": f"p{i}", "text": "정책 변화와 사업 추진에 관한 기사 내용이며 검토가 필요한 상세 사실을 설명한다."} for i in range(1, 6)]
        result = {"issue_id": A, "key_change": {"text": "정책 변화와 사업 추진을 설명한다.", "evidence_ids": ["p1", "p2"]},
                  "facts": [{"text": "검토에 필요한 상세 사실을 설명한다.", "evidence_ids": [f"p{i}"]} for i in range(3, 6)],
                  "stage": "추진·협약", "why_candidate": "사업 추진 관점에서 검토할 수 있습니다."}
        valid, errors = articles._parse(response([result]), [{"issue_id": A, "paragraphs": paragraphs}])
        self.assertFalse(errors)
        self.assertEqual(len(valid[A]["evidence"]), 5)
        self.assertLessEqual(sum(len(row["quote"]) for row in valid[A]["evidence"]), 200)

    def test_twenty_union_references_are_supported_but_each_claim_remains_bounded(self):
        paragraphs = [{"id": f"p{i}", "text": "검토 대상의 정책 변화와 사업 추진 사실을 설명하는 충분한 길이의 원문 문단입니다."} for i in range(1, 21)]
        claims = [{"text": "정책 변화와 사업 추진 사실을 설명한다.", "evidence_ids": [f"p{i}" for i in range(start, start + 4)]} for start in range(1, 21, 4)]
        value = {"issue_id": A, "key_change": claims[0], "facts": claims[1:], "stage": "불명", "why_candidate": "사업 변화 관점에서 검토할 수 있습니다."}
        batch = [{"issue_id": A, "paragraphs": paragraphs}]
        valid, errors = articles._parse(response([value]), batch)
        self.assertFalse(errors)
        self.assertEqual(len(valid[A]["evidence"]), 20)
        self.assertLessEqual(sum(len(row["quote"]) for row in valid[A]["evidence"]), 200)
        value["key_change"]["evidence_ids"].append("p5")
        valid, errors = articles._parse(response([value]), batch)
        self.assertFalse(valid)
        self.assertEqual(errors[A], "output_reference_limit")

    def test_duplicate_reference_is_normalized_without_changing_facts(self):
        value = item(A)
        value["key_change"]["evidence_ids"] = ["p1", "p1"]
        valid, errors = articles._parse(response([value]), self.batch())
        self.assertFalse(errors.get(A))
        self.assertEqual(valid[A]["key_change"]["evidence_ids"], ["p1"])
        self.assertEqual(valid[A]["key_change"]["text"], value["key_change"]["text"])

    def test_schema_references_are_selected_from_supplied_paragraph_ids(self):
        batch = [{"issue_id": A, "paragraphs": [*P, {"id": "p9", "text": "짧음"}]}]
        schema = articles.response_schema(batch)
        enum = schema["properties"]["items"]["items"]["properties"]["key_change"]["properties"]["evidence_ids"]["items"]["enum"]
        self.assertEqual(enum, ["p1", "p2"])
        self.assertNotIn("p999", enum)
        self.assertNotIn("p01", enum)


if __name__ == "__main__":
    unittest.main()

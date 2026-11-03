"""Synthetic examples only: no internal reporting titles or financial figures."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from weekly_trend.classification import (classify, classify_for_source, earnings_form,
    load_classification_rules, source_policy, validate_classification_rules)

ROOT = Path(__file__).resolve().parents[1]


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_classification_rules()
        self.companies = [{"id": "synthetic-a", "name": "가상솔증권", "aliases": ["가상솔투자"], "enabled": True},
                          {"id": "synthetic-b", "name": "가상달증권", "enabled": True}]
        self.topics = {item["id"]: item["name"] for item in json.loads((ROOT / "config/topics.json").read_text())["topics"]}
        self.rules["subject_scope"]["related_groups"].append({"name": "가상금융지주", "aliases": ["가상금융"]})
        self.rules["subject_scope"]["market_infrastructure"].append({"name": "가상결제원", "aliases": ["가상결제원"]})

    def route(self, title):
        return classify(title, self.companies, self.topics, self.rules)

    def test_thirteen_companies_and_agreed_topic_labels(self):
        companies = json.loads((ROOT / "config/companies.json").read_text())["companies"]
        self.assertEqual(len(companies), 13)
        self.assertEqual([item["name"] for item in companies[-3:]], ["다올투자증권", "교보증권", "한화투자증권"])
        self.assertEqual(self.topics["digital"], "신사업·상품·고객전략")
        self.assertEqual(self.topics["risk"], "정책·인허가·리스크")

    def test_subject_topic_and_change_are_all_required(self):
        for title in ["가상솔증권 AI 플랫폼", "무관유통사 AI 서비스 출시", "가상솔증권 사무실 화분 교체", "가상솔증권 소개"]:
            with self.subTest(title=title):
                self.assertIsNone(self.route(title))
        route = self.route("가상솔증권, AI 고객지원 서비스 도입")
        self.assertEqual(route["topic_id"], "digital")
        self.assertIn("AI", route["tags"])

    def test_primary_topic_priorities_with_multiple_tags(self):
        cases = [
            ("가상솔증권, 토큰증권 정산 플랫폼 MOU 체결", "expansion", "양해각서", ["디지털자산", "제휴", "양해각서"]),
            ("가상솔증권, AI 전담조직 신설", "strategy", "조직 변경", ["AI", "조직개편"]),
            ("가상솔증권, 발행어음 사업 인가 획득", "risk", "인가·승인", ["인허가", "발행어음"]),
            ("가상솔증권, 초고액 고객 전용 자산관리서비스 출시", "digital", "출시·사업 개시", ["상품·고객전략"]),
            ("가상솔증권, 해외 금융회사 인수 검토", "expansion", "검토", ["해외사업", "인수합병"]),
        ]
        for title, topic, stage, tags in cases:
            with self.subTest(title=title):
                route = self.route(title)
                self.assertIsNotNone(route)
                self.assertEqual(route["topic_id"], topic)
                self.assertEqual(route["event_stage"], stage)
                self.assertTrue(set(tags) <= set(route["tags"]))
                self.assertEqual(route["classification_rules_version"], "news-events-v2")

    def test_uncertain_and_interim_steps_never_become_final_outcomes(self):
        cases = [
            ("가상솔증권, AI 상담서비스 출시 예정", "계획·추진"),
            ("가상솔증권, 발행어음 인가 신청", "신청"),
            ("가상솔증권, 발행어음 인가안 증선위 통과", "심사 통과"),
            ("가상솔증권, 발행어음 예비인가 심사", "심사"),
            ("가상솔증권, 해외회사 인수설 사실무근이라고 부인", "부인"),
            ("가상솔증권, AI 서비스 도입 검토 중단", "중단·철회"),
        ]
        for title, stage in cases:
            with self.subTest(title=title):
                self.assertEqual(self.route(title)["event_stage"], stage)

    def test_current_launch_after_historical_approval_is_business_not_licensing(self):
        route = self.route("가상솔증권, 인수업 인가 후 새 플랫폼 출시로 IB 사업 본격화")
        self.assertEqual(route["topic_id"], "digital")
        self.assertEqual(route["event_stage"], "출시·사업 개시")
        self.assertIn("인허가", route["tags"])
        self.assertNotIn("인수합병", route["tags"])
        self.assertNotIn("expansion", route["matched_topic_ids"])
        plain_permission = self.route("가상솔증권 인수업 인가 획득")
        self.assertEqual(plain_permission["topic_id"], "risk")

    def test_actual_numeric_results_without_announcement_keyword(self):
        # Arbitrary invented figures exercise formatting, not real company facts.
        route = self.route("가상솔증권, 분기 영업이익 17억원·순이익 9억원")
        self.assertEqual((route["topic_id"], route["event_stage"]), ("performance", "성과"))
        self.assertIsNone(self.route("가상솔증권, 다음 분기 영업이익 17억원 예상"))
        self.assertIsNone(self.route("가상솔증권, 다른 제조사 순이익 9억원 전망"))

    def test_new_business_performance_is_not_routine_product_marketing(self):
        included = self.route("가상솔증권, 종합투자계좌 IMA 누적 발행액 성장")
        self.assertEqual(included["topic_id"], "digital")
        self.assertEqual(included["event_stage"], "성과")
        self.assertIn("종합투자계좌", included["tags"])
        for title in ["가상솔증권, 일반 채권 판매 개시", "가상솔증권, 특판 발행어음 판매",
                      "가상솔증권, 신규 ETF 출시", "가상솔증권 AI 투자 세미나 개최",
                      "가상솔증권, 신규계좌 가입 이벤트 실시", "가상솔증권, 수수료 무료 행사 확대"]:
            with self.subTest(title=title):
                self.assertIsNone(self.route(title))

    def test_customer_dividend_and_stock_commentary_are_not_company_earnings(self):
        for title in ["가상솔증권, 배당 수령 고객 증가", "가상솔증권 고객 배당 실적 성장",
                      "가상솔증권, 무관종목 목표주가 상향 발표", "가상솔증권 시황 보고서 공개"]:
            with self.subTest(title=title):
                self.assertIsNone(self.route(title))
        self.assertEqual(self.route("가상솔증권, 주주환원 자사주 소각 결정")["topic_id"], "performance")

    def test_group_requires_connected_business_context(self):
        self.assertIsNone(self.route("가상금융지주, 고객 축제 개최"))
        self.assertIsNone(self.route("가상금융지주, 일반 소비재 신상품 출시"))
        capital = self.route("가상금융지주, 증권 자회사 주식 장내매수 예정")
        self.assertEqual((capital["topic_id"], capital["event_stage"]), ("performance", "계획·추진"))
        overseas = self.route("가상금융지주, 일본 현지 사무소 설립 추진")
        self.assertEqual(overseas["topic_id"], "expansion")
        self.assertEqual(overseas["subject_scope"], "financial_group")

    def test_regulators_and_infrastructure_require_financial_contact(self):
        self.assertIsNone(self.route("금융위원회, 직원 체육행사 계획 발표"))
        self.assertIsNone(self.route("가상결제원, 사내 동호회 신설"))
        for title in ["금융당국, 증권사 내부통제 지침 개정 추진",
                      "가상결제원, 토큰증권 공동결제 서비스 승인 신청",
                      "KDX와 NXT, 증권시장 정산 컨소시엄 출자 승인 신청"]:
            with self.subTest(title=title):
                route = self.route(title)
                self.assertIsNotNone(route)
                self.assertEqual(route["topic_id"], "risk")
                self.assertEqual(route["company_ids"], [])

    def test_external_partners_need_strong_financial_intersection(self):
        included = ["가상클라우드, 증권사 AI 주문 플랫폼 공동협약 체결",
                    "가상결제기술사, 스테이블코인 결제 정산 파트너십 추진"]
        for title in included:
            route = self.route(title)
            self.assertIsNotNone(route)
            self.assertEqual(route["subject_scope"], "sector_contact")
            self.assertEqual(route["topic_id"], "expansion")
        for title in ["가상클라우드, 일반 AI 플랫폼 출시", "가상게임사, 가상자산 가격 전망 발표",
                      "해외 ETF 시장 성장 전망", "가상유통사, 해외 AI 협약 체결"]:
            self.assertIsNone(self.route(title), title)

    def test_sto_legal_change_and_nominal_policy_announcements(self):
        self.assertEqual(self.route("가상솔증권, STO 고객 서비스 도입")["topic_id"], "digital")
        for title in ["금융위원회, 토큰증권 시행령 개정안 공개",
                      "금융당국, 특정금융정보법 가상자산 제도 개정 추진",
                      "금융위원회, 분산원장 기반 자본시장 정책방향 주요내용"]:
            self.assertEqual(self.route(title)["topic_id"], "risk", title)
        self.assertEqual(self.route("가상솔증권, 국내 신사업 확대")["event_stage"], "변경·확대")

    def test_overseas_cooperation_and_ascii_keywords_are_case_insensitive(self):
        for title in ["가상솔증권, 해외 협력 확대 논의", "가상솔증권, 글로벌 운용사와 전략적 협력 추진"]:
            self.assertEqual(self.route(title)["topic_id"], "expansion")
        for title in ["가상솔증권 ai 서비스 출시", "가상솔증권 AI 서비스 출시"]:
            self.assertIn("AI", self.route(title)["tags"])
        self.assertIsNone(self.route("가상솔증권, 일반 chair 서비스 출시"))

    def test_company_aliases_multi_company_and_source_issuer_precedence(self):
        route = self.route("가상솔투자와 가상달증권, AI 플랫폼 MOU 체결")
        self.assertEqual(route["company_ids"], ["synthetic-a", "synthetic-b"])
        route = classify_for_source("가상달증권과 AI 플랫폼 제휴 체결", {
            "scope": "company", "company_id": "synthetic-a", "_classification_rules": self.rules}, self.companies, self.topics)
        self.assertEqual(route["company_ids"], ["synthetic-a"])

    def test_editing_rules_at_same_path_is_immediately_effective(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rules.json"
            path.write_text(json.dumps(self.rules, ensure_ascii=False))
            before = load_classification_rules(path)
            changed = deepcopy(self.rules)
            changed["tag_keywords"]["가상기술"] = ["합성전환"]
            path.write_text(json.dumps(changed, ensure_ascii=False))
            after = load_classification_rules(path)
        title = "가상솔증권, AI 합성전환 서비스 출시"
        self.assertNotIn("가상기술", classify(title, self.companies, self.topics, before)["tags"])
        self.assertIn("가상기술", classify(title, self.companies, self.topics, after)["tags"])

    def test_dart_allowlist_is_unchanged_and_independent_of_news_rules(self):
        source = {"adapter": "dart_html", "scope": "company", "company_id": "synthetic-a",
                  "content_filter": "earnings_only", "_classification_rules": {"invalid": True}}
        for title in ["[기재정정]영업(잠정)실적(공정공시)", "분기보고서(2026.09)", "연결감사보고서"]:
            route = classify_for_source(title, source, self.companies, self.topics)
            self.assertEqual(route["topic_id"], "performance")
            self.assertEqual(route["classification_rules_version"], "dart-earnings-v1")
            self.assertEqual(route["event_stage"], "실적 공시")
        for title in ["결산실적공시예고", "임원ㆍ주요주주특정증권등소유상황보고서", "기업설명회(IR)안내", "영업실적전망"]:
            self.assertIsNone(earnings_form(title))
            self.assertIsNone(classify_for_source(title, source, self.companies, self.topics))

    def test_rules_validation_and_effective_policy_version(self):
        bad = deepcopy(self.rules)
        bad["topic_keywords"]["sixth_topic"] = ["extra"]
        with self.assertRaises(ValueError):
            validate_classification_rules(bad)
        bad = deepcopy(self.rules)
        bad["event_stages"][0]["keywords"] = "not-a-list"
        with self.assertRaises(ValueError):
            validate_classification_rules(bad)
        self.assertEqual(source_policy({"content_policy_version": "news-events-v2", "_effective_content_policy_version": "news-events-v2-abc123"})[1], "news-events-v2-abc123")
        with self.assertRaises(ValueError):
            source_policy({"adapter": "dart_html", "content_filter": "topics"})

    def test_rejected_uncertain_and_disputed_permission_never_claims_approval(self):
        for text, expected in [("불허", "불허·반려"), ("승인 거절", "불허·반려"), ("보류", "보류"),
                               ("여부 논란", "쟁점·논의")]:
            route = self.route("가상솔증권, 발행어음 인가 " + text)
            self.assertEqual(route["event_stage"], expected)
        self.assertIsNone(self.route("가상솔증권, 발행어음 인가 가능성 확대"))

    def test_forecasting_services_and_tokenized_financial_services_are_included(self):
        for title in ["가상솔증권, AI 투자 전망 서비스 출시", "가상솔증권, 토큰화 채권 서비스 출시",
                      "가상솔증권, STO 채권 플랫폼 출시"]:
            with self.subTest(title=title):
                self.assertEqual(self.route(title)["topic_id"], "digital")
        for title in ["가상솔증권, 국내 제조사 영업이익 증가 분석",
                      "가상솔증권, 국내 제조사 실적 발표에 따른 주가 상승 분석"]:
            self.assertIsNone(self.route(title))

    def test_nameless_policy_group_tokenization_and_cooperation_without_contract_claim(self):
        self.assertEqual(self.route("토큰증권 분산원장 정책방향 주요 내용")["topic_id"], "risk")
        self.assertIsNotNone(self.route("가상금융지주, 토큰화 금융 협력 논의"))
        cooperation = self.route("가상솔증권, 글로벌 운용사와 전략적 협력")
        self.assertEqual(cooperation["event_stage"], "협력·제휴")
        self.assertIn("제휴", cooperation["tags"])
        self.assertIn("자본", self.route("가상금융지주, 증권 자회사 주식 장내매수 예정")["tags"])

    def test_foreign_general_crypto_authority_is_not_domestic_scope(self):
        self.assertIsNone(self.route("미국 금융당국, 가상자산 사업자 인가 완료"))
        self.assertIsNotNone(self.route("미국 금융당국, 국내 증권사 가상자산 사업 영향 조사"))
        partner = self.route("가상해외기술사, 스테이블코인 결제 사업 전략적 협력 추진")
        self.assertEqual(partner["topic_id"], "expansion")

    def test_current_regulatory_inspection_and_settlement_changes_are_retained(self):
        inspection = self.route("금융위, 가상자산 플랫폼 해킹 발생에 긴급 점검 착수")
        self.assertEqual((inspection["topic_id"], inspection["event_stage"]), ("risk", "점검·조사"))
        self.assertEqual(self.route("금감원, ETF 광고 준수 여부 조사 착수")["event_stage"], "점검·조사")
        settlement = self.route("증권시장 결제주기 T+1 단축 전환 추진")
        self.assertEqual((settlement["topic_id"], settlement["event_stage"]), ("risk", "계획·추진"))
        self.assertIn("시장인프라", settlement["tags"])

    def test_source_can_limit_news_to_competitors_or_expand_to_industry(self):
        title = "금융위, 토큰증권 운영 지침 개정 추진"
        restricted = {"scope": "competitors", "_classification_rules": self.rules}
        expanded = {"scope": "competitors_and_industry", "_classification_rules": self.rules}
        self.assertIsNone(classify_for_source(title, restricted, self.companies, self.topics))
        self.assertEqual(classify_for_source(title, expanded, self.companies, self.topics)["topic_id"], "risk")
        self.assertEqual(classify_for_source("가상솔증권 AI 서비스 출시", restricted, self.companies, self.topics)["topic_id"], "digital")

    def test_permission_not_obtained_never_claims_completion(self):
        route = self.route("가상솔증권, 발행어음 인가 받지 못해")
        self.assertEqual(route["event_stage"], "미승인")

    def test_rejected_application_is_rejection_rather_than_application(self):
        route = self.route("가상솔증권, 발행어음 인가 신청 반려")
        self.assertEqual(route["event_stage"], "불허·반려")

    def test_abbreviated_foreign_authority_is_not_domestic_scope(self):
        self.assertIsNone(self.route("美 금융당국, 가상자산 사업자 인가 완료"))


if __name__ == "__main__":
    unittest.main()

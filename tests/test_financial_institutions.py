"""Synthetic title examples exercise literal name matching, not AI inference."""

from pathlib import Path
import json
import tempfile
import unittest

from weekly_trend.financial_institutions import load_matcher, match_title

ROOT = Path(__file__).resolve().parents[1]


class FinancialInstitutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = load_matcher(ROOT)

    def test_existing_company_aliases_regulators_and_infrastructure_are_merged(self):
        self.assertEqual(match_title('한투증권과 하나證, 금융위·금감원·금투협 공동 회의',self.matcher),
                         ['한국투자증권','하나증권','금융위원회','금융감독원','금융투자협회'])
        self.assertEqual(match_title('KRX와 NXT, 공동 점검',self.matcher),['한국거래소','넥스트레이드'])

    def test_broad_financial_names_and_unknown_proper_suffixes(self):
        self.assertEqual(match_title('신한은행·한화생명·삼성카드·KB자산운용·국민연금',self.matcher),
                         ['신한은행','한화생명','삼성카드','KB자산운용','국민연금공단'])
        self.assertEqual(match_title('가상솔은행, 가상달손해보험 및 SK증권 공동 행사',self.matcher),
                         ['가상솔은행','가상달손해보험','SK증권'])

    def test_generic_financial_topics_and_nonfinancial_names_do_not_match(self):
        for title in ['국내은행과 중앙은행의 신용카드 및 토큰증권 시장 전망',
                      '금융위기 우려와 글로벌은행 규제', '국회 정무위, 금융당국 점검',
                      '무관전자, 신규 기술 발표', '토스카나 여행과 SECURE 시스템 소개',
                      'AI증권 서비스와 전자금융 확대', '주요은행 및 대형증권사 실적 전망', '한진칼 시총 육박하는데…증권가 적정가치 전망',
                      '수은 오염과 날씨 예보, 신보 발매 및 산은 높다',
                      '현대캐피탈리즘과 가상은행잎 연구']:
            with self.subTest(title=title):
                self.assertEqual(match_title(title,self.matcher),[])

    def test_longer_institution_wins_same_span_but_independent_mentions_survive(self):
        self.assertEqual(match_title('토스증권과 카카오페이증권 공동 출시',self.matcher),['토스증권','카카오페이증권'])
        self.assertEqual(match_title('토스, 토스증권과 공동 출시',self.matcher),['토스','토스증권'])
        self.assertEqual(match_title('한화생명과 한화금융 공동 행사',self.matcher),['한화생명','한화금융'])

    def test_ascii_alias_boundaries_case_and_dedup_are_stable(self):
        self.assertEqual(match_title('msci와 FTSE 러셀, MarketVector 및 드라이브웰스',self.matcher),
                         ['MSCI','FTSE Russell','MarketVector','DriveWealth'])
        self.assertEqual(match_title('NH투자증권·엔에이치투자증권·NH투자증권',self.matcher),['NH투자증권'])
        self.assertEqual(match_title('XMSCI와 NXTAI는 무관한 문자열',self.matcher),[])

    def test_observed_financial_ecosystem_names_and_instrument_types(self):
        self.assertEqual(match_title('SC그룹·빗썸·바이낸스·업비트·코인원·코빗·DAXA 공동회의',self.matcher),
                         ['스탠다드차타드','빗썸','바이낸스','업비트','코인원','코빗','디지털자산거래소 공동협의체'])
        for title in ['자본증권, 파생결합증권, 주가연계증권, 예탁증권, 담보부증권, 자산유동화증권',
                      '신종자본증권 소개 및 신규 발행']:
            self.assertEqual(match_title(title,self.matcher),[])

    def test_product_card_names_and_generic_distress_do_not_invent_institutions(self):
        self.assertEqual(match_title('서울교통카드·청년신용카드·신규체크카드·어린이선불카드 출시',self.matcher),[])
        self.assertEqual(match_title('부실은행 구조조정과 부실금융지주 위험',self.matcher),[])
        self.assertEqual(match_title('삼성카드·현대카드와 가상솔카드 공동 서비스',self.matcher),
                         ['삼성카드','현대카드','가상솔카드'])

    def test_known_institution_head_titles_keep_the_underlying_name(self):
        self.assertEqual(match_title('금융정보분석원장, FATF 운영위원회 참여',self.matcher),
                         ['금융정보분석원','국제자금세탁방지기구'])
        self.assertEqual(match_title('금감원장과 금융감독원장 인사',self.matcher),['금융감독원'])
        self.assertEqual(match_title('현대캐피탈리즘 및 금융정보분석원장식 연구',self.matcher),[])

    def test_registry_is_serializable_and_reloads_edits(self):
        json.dumps(self.matcher,ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir()
            for name in ('financial_institutions.json','companies.json','classification_rules.json'):
                (root/'config'/name).write_bytes((ROOT/'config'/name).read_bytes())
            config=json.loads((root/'config/financial_institutions.json').read_text())
            config['institutions'].append({'name':'가상결제기관','aliases':['합성결제원']})
            (root/'config/financial_institutions.json').write_text(json.dumps(config,ensure_ascii=False))
            changed=load_matcher(root)
            self.assertEqual(match_title('합성결제원 공동 사업',changed),['가상결제기관'])
            self.assertEqual(match_title('합성결제원 공동 사업',self.matcher),[])

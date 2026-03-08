"""파서 단위 테스트."""
import pytest
from datetime import date
from parser.repostory import RepostoryParser
from parser.generic import GenericParser
from parser.registry import parse_message


CHANNEL = "@repostory123"
parser = RepostoryParser()


class TestRepostoryParser:

    def test_standard_stock_report(self):
        text = "▶ 삼성전자(005930) 반도체 업황 개선 지속 - 미래에셋증권\nhttps://example.com/r.pdf\n- 목표가: 85,000원 (매수)"
        result = parser.parse(text, CHANNEL, message_id=1)

        assert result is not None
        assert result.stock_name == "삼성전자"
        assert result.stock_code == "005930"
        assert result.title == "반도체 업황 개선 지속"
        assert result.broker == "미래에셋증권"
        assert result.target_price == 85000
        assert result.opinion == "매수"
        assert result.pdf_url == "https://example.com/r.pdf"

    def test_industry_report_no_stock(self):
        text = "▶ 반도체 섹터 점검 - 삼성증권"
        result = parser.parse(text, CHANNEL)

        assert result is not None
        assert result.stock_name is None
        assert result.stock_code is None
        assert result.broker == "삼성증권"
        assert result.title == "반도체 섹터 점검"

    def test_target_price_change(self):
        text = "▶ SK하이닉스(000660) HBM 전망 - 키움증권\n- 목표가: 180,000원 → 200,000원 (매수)"
        result = parser.parse(text, CHANNEL)

        assert result is not None
        assert result.target_price == 200000
        assert result.prev_target_price == 180000

    def test_no_target_price(self):
        text = "▶ 삼성전자(005930) 실적 리뷰 - KB증권\n- 투자의견: 매수 유지"
        result = parser.parse(text, CHANNEL)
        assert result is not None
        assert result.target_price is None

    def test_standalone_opinion(self):
        text = "▶ 삼성전자(005930) 실적 리뷰 - KB증권\n- 투자의견: 매수"
        result = parser.parse(text, CHANNEL)
        assert result is not None
        assert result.opinion == "매수"

    def test_analyst_extraction(self):
        text = "▶ 삼성전자(005930) 반도체 전망 - 미래에셋증권 홍길동"
        result = parser.parse(text, CHANNEL)
        assert result is not None
        assert result.analyst == "홍길동"

    def test_empty_text_returns_none(self):
        assert parser.parse("", CHANNEL) is None
        assert parser.parse("   ", CHANNEL) is None

    def test_title_normalized(self):
        text = "▶ 삼성전자(005930) 반도체 업황 개선 지속! - 미래에셋증권"
        result = parser.parse(text, CHANNEL)
        assert result.title_normalized == "반도체업황개선지속"

    def test_url_as_pdf_fallback(self):
        text = "▶ 삼성전자(005930) 리포트 - KB증권\nhttps://example.com/report"
        result = parser.parse(text, CHANNEL)
        assert result.pdf_url == "https://example.com/report"


class TestGenericParser:

    def test_fallback_extracts_basics(self):
        text = "삼성전자(005930) 리포트 - 키움증권\n목표가: 70,000원 (매수)"
        result = parse_message(text, "@unknown_channel")
        assert result is not None
        assert result.stock_code == "005930"

    def test_generic_extracts_opinion(self):
        text = "SK하이닉스(000660) 분석 - NH증권\n매수 의견"
        result = parse_message(text, "@some_channel")
        assert result is not None
        assert result.opinion == "매수"


class TestNormalizer:

    def test_broker_aliases(self):
        from parser.normalizer import normalize_broker
        assert normalize_broker("미래에셋") == "미래에셋증권"
        assert normalize_broker("한투") == "한국투자증권"
        assert normalize_broker("NH") == "NH투자증권"

    def test_parse_price_comma(self):
        from parser.normalizer import parse_price
        assert parse_price("85,000원") == 85000
        assert parse_price("200,000") == 200000

    def test_parse_price_man(self):
        from parser.normalizer import parse_price
        assert parse_price("8.5만원") == 85000

    def test_normalize_title_removes_special(self):
        from parser.normalizer import normalize_title
        assert normalize_title("반도체 업황 개선!") == "반도체업황개선"
        assert normalize_title("HBM3E 양산 본격화") == "hbm3e양산본격화"


class TestFixtureSamples:
    """실제 수집 샘플 파일 기반 테스트 (fixtures 파일 있을 때만)."""

    @pytest.fixture
    def samples(self):
        import json
        from pathlib import Path
        path = Path("tests/fixtures/repostory_samples.json")
        if not path.exists():
            pytest.skip("fixtures 파일 없음 - STEP 03 먼저 실행")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_parse_rate(self, samples):
        """실제 샘플의 파싱 성공률 70% 이상."""
        success = 0
        for s in samples:
            result = parser.parse(s["text"], CHANNEL, s["id"])
            if result and result.broker and result.title:
                success += 1
        rate = success / len(samples)
        print(f"\n파싱 성공률: {rate:.1%} ({success}/{len(samples)})")
        assert rate >= 0.7, f"파싱 성공률 미달: {rate:.1%}"

    def test_no_crash_on_any_sample(self, samples):
        """어떤 샘플도 예외 없이 처리."""
        for s in samples:
            try:
                parser.parse(s["text"], CHANNEL, s["id"])
            except Exception as e:
                pytest.fail(f"메시지 ID {s['id']} 처리 중 예외: {e}")

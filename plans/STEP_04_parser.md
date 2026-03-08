# STEP 04 — 메시지 파서

## 목표
- @repostory123 파서 정확도 검증 및 보완
- 증권사명 정규화 완성도 높이기
- 파싱 실패율 측정 기준 수립

## 사전 조건
- STEP 03 완료 (실제 메시지 샘플 수집 완료)

## 실제 메시지 샘플 확보 방법

```python
# 실제 채널에서 최근 50개 메시지 텍스트를 파일로 저장
import asyncio, json
from collector.telegram_client import get_client

async def dump_samples():
    client = get_client()
    await client.start()
    samples = []
    async for msg in client.iter_messages("@repostory123", limit=50):
        if msg.text:
            samples.append({"id": msg.id, "date": str(msg.date), "text": msg.text})
    with open("tests/fixtures/repostory_samples.json", "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    await client.disconnect()

asyncio.run(dump_samples())
```

## 구현 대상

### parser/repostory.py 보완

실제 샘플 확인 후 패턴 보완. 예상 변형 케이스:
- 종목코드 없는 경우: `▶ 반도체 섹터 점검 - 삼성증권`
- 애널리스트 포함: `▶ 삼성전자(005930) 제목 - 미래에셋증권 홍길동`
- 목표가 없는 경우 (의견만): `(매수 유지)`
- 이전 목표가 포함: `목표가: 85,000원 → 90,000원 (매수)`

```python
# 이전 목표가 패턴 추가
PATTERN_PREV_TARGET = re.compile(
    r"목표가[:\s]*([0-9,]+)\s*원?\s*[→->]\s*([0-9,]+)\s*원"
)

# 애널리스트 패턴 추가 (증권사 뒤에 붙는 경우)
PATTERN_ANALYST = re.compile(r"[-–]\s*\S+증권\s+([가-힣]{2,4})$", re.MULTILINE)
```

### parser/normalizer.py 보완

누락된 증권사 별칭 추가 (실제 샘플에서 발견된 것 기준).

## 테스트 코드

### tests/fixtures/ 디렉토리

```
tests/
├── fixtures/
│   ├── repostory_samples.json     # 실제 수집 샘플 (STEP 03에서 생성)
│   └── repostory_expected.json    # 예상 파싱 결과 (수동 작성)
```

### tests/test_parser.py

```python
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

    def test_empty_text_returns_none(self):
        assert parser.parse("", CHANNEL) is None
        assert parser.parse("   ", CHANNEL) is None

    def test_title_normalized(self):
        text = "▶ 삼성전자(005930) 반도체 업황 개선 지속! - 미래에셋증권"
        result = parser.parse(text, CHANNEL)
        # 특수문자/공백 제거, 소문자
        assert result.title_normalized == "반도체업황개선지속"


class TestGenericParser:

    def test_fallback_extracts_basics(self):
        text = "삼성전자(005930) 리포트 - 키움증권\n목표가: 70,000원 (매수)"
        result = parse_message(text, "@unknown_channel")
        assert result is not None
        assert result.stock_code == "005930"


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
```

### 실행

```bash
pytest tests/test_parser.py -v

# 실제 샘플 파싱률 확인
pytest tests/test_parser.py::TestFixtureSamples -v -s
```

## 검증 체크리스트

- [ ] 표준 종목 리포트 파싱 PASS
- [ ] 산업 리포트(종목 없음) 파싱 PASS
- [ ] 목표가 변경 (이전→현재) 파싱 PASS
- [ ] 증권사 별칭 정규화 PASS
- [ ] 실제 샘플 파싱률 70% 이상
- [ ] 어떤 입력도 예외 발생 없음

## 완료 기준 → STEP 05 진입

체크리스트 통과 시.

## 이슈/메모

- 파싱 실패 케이스를 `tests/fixtures/parse_failures.txt`에 모아두면 나중에 패턴 보완에 활용
- 70%는 최소 기준. 실제로는 90%+ 목표
- `report_date`는 파서에서 date.today() 임시 반환 → STEP 03에서 message.date로 덮어쓰는 구조 유지

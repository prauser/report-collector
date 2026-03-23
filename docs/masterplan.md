# Report Collector → AI Agent 확장 마스터플랜

> 작성일: 2026-03-19
> 목적: 기존 report-collector 시스템을 AI Agent 기반 분석 시스템으로 확장하기 위한 전체 설계안
> 범위: 아키텍처, 데이터 스키마, 추출 파이프라인, 포맷 전략, DB 설계, 구현 로드맵

---

## 목차

1. [현재 시스템 개요](#1-현재-시스템-개요)
2. [목표: AI Agent 분석 시스템](#2-목표-ai-agent-분석-시스템)
3. [3-Layer 데이터 아키텍처](#3-3-layer-데이터-아키텍처)
4. [리포트 분류 체계](#4-리포트-분류-체계)
5. [Layer 2 스키마 설계](#5-layer-2-스키마-설계)
6. [크로스 리포트 연결 구조](#6-크로스-리포트-연결-구조)
7. [데이터 포맷 전략](#7-데이터-포맷-전략)
8. [PDF → Markdown 변환 레이어](#8-pdf--markdown-변환-레이어)
9. [DB 스키마 설계](#9-db-스키마-설계)
10. [설계 Q&A (검토 피드백)](#10-설계-qa-검토-피드백)
11. [구현 로드맵](#11-구현-로드맵)
12. [비용 추정](#12-비용-추정)
13. [주의사항 및 권장사항](#13-주의사항-및-권장사항)

---

## 1. 현재 시스템 개요

텔레그램 채널에서 증권 리포트를 수집하여 기초 메타데이터를 저장하고 PDF 원본을 아카이빙하는 시스템.

### 현재 저장 데이터

증권사, 애널리스트명, 종목명, 투자의견, 제목, 목표가, PDF 원본 아카이빙, 수집 채널 정보.

### 기술 스택

- Python 기반 수집 파이프라인
- LLM: Claude API (향후 OpenAI/Gemini 교체 가능)
- DB: Railway PostgreSQL
- 마이그레이션: alembic (설치되어 있으나 미사용 — 이번 기회에 정리 필요)

### 현재 DB 테이블 구조

| 테이블 | 역할 | 비고 |
|--------|------|------|
| `reports` | 리포트 메타데이터 | 35개 컬럼, 핵심 테이블 |
| `stock_codes` | 종목 마스터 | code, name, sector 등 |
| `price_history` | 목표가/의견 이력 | reports FK |
| `change_events` | 의견 변경 이벤트 | reports FK |
| `channels` | 텔레그램 채널 관리 | |
| `pending_messages` | 처리 대기 메시지 | |
| `llm_usage` | LLM 사용량 추적 | |
| `backfill_runs` | 백필 실행 이력 | |

### 현재 수집 흐름

```
텔레그램 채널 → Python 기초 파싱 (증권사, 종목명 등)
    → 구분이 안 가면 LLM으로 판별
    → 메타데이터 DB 저장 + PDF 아카이빙
```

### 현재 한계

현재 저장하는 메타데이터(증권사, 종목명, 목표가 등)는 **인덱싱 메타데이터**에 가까움. "삼성전자 리포트 목록 보여줘"는 가능하지만 "왜 목표가를 올렸는지", "매출구조가 어떻게 변하고 있는지"에는 답할 수 없음.

---

## 2. 목표: AI Agent 분석 시스템

사용자가 특정 기업에 대해 질문하면, 수집된 리포트를 기반으로 종합 분석하여 답변하는 AI Agent.

### 예상 질의 유형

- 단순 조회: "삼성전자 최근 투자의견 변화 알려줘"
- 크로스 리포트 종합분석: "반도체 업황 변화에 따른 삼성전자 실적 전망을 종합해줘"
- 투자 논리 분석: "최근 목표가 상향의 주된 주장 요인은?"
- 매출구조 추적: "에코프로비엠 매출구조 변화 추이를 분석해줘"
- 산업 탐색: "2차전지 업종에서 주목할 종목은?"
- 매크로 연결: "금리 인하가 어떤 섹터에 영향을 줄까?"

### 출력 형태

- 챗봇 형태의 대화형 답변
- 웹에서 보고서 형태로 보여주기
- 특정 기업별 데이터를 모아 볼 수 있는 대시보드

---

## 3. 3-Layer 데이터 아키텍처

### 핵심 질문: 초간단 요약과 PDF 원문 사이에 중간 레이어가 필요한가?

**필요하다.** 그리고 이유는 Agent의 실질적 성능과 비용 양쪽 모두에 직결됨.

### 왜 현재 메타데이터(Layer 1)만으로 부족한가

"삼성전자 리포트 목록"은 보여줄 수 있지만, "왜 목표가를 올렸는지"에는 답할 수 없음. 분석의 핵심인 논리, 근거, 수치가 없음.

### 왜 매번 PDF 원문을 보는 것도 비현실적인가

1. **컨텍스트 윈도우 한계**: 리포트 한 건이 5~20페이지, 10건만 동시에 넣어도 토큰이 터짐
2. **비용**: 매 질의마다 수만~수십만 토큰 소비
3. **노이즈**: 원문의 법적 고지, 보일러플레이트, 차트 설명 등이 절반 이상

### 3-Layer 구조

```
Layer 1: 메타데이터 인덱스 (현재 reports 테이블)
    → 검색, 필터링용
    → 증권사, 종목명, 목표가, 의견, 날짜

Layer 2: 구조화된 상세 분석 (신규 report_analysis 테이블)  ← 핵심
    → 투자 논리 체인, 재무 추정, 산업 맥락
    → 리포트 한 건당 2,000~3,000 토큰
    → Agent 분석의 핵심 소스

Layer 3: PDF 원문 아카이브 (현재 pdf_path)
    → 근거 확인용 fallback
    → "이 수치의 원본 근거를 확인해줘" 같은 경우에만 참조
```

### 설계 원칙

**수집 시점에 리포트 한 건의 사실(fact)을 구조화 추출하되, 여러 리포트에 걸친 종합 판단은 Agent 질의 시점에 수행.**

수집 시점에 Layer 2를 만드는 게 맞음. 나중에 일괄 재처리하는 것보다 건당 비용이 같고, 데이터가 쌓이는 즉시 Agent가 활용할 수 있음.

---

## 4. 리포트 분류 체계

수집되는 리포트를 3가지 타입으로 분류. 타입별로 다른 스키마로 추출.

| 타입 | 설명 | 예시 |
|------|------|------|
| `stock` | 특정 종목 분석 리포트 | "삼성전자 - AI 반도체 수혜 본격화" |
| `industry` | 산업/섹터 분석 리포트 | "2차전지 - 유럽 EV 보조금 재개의 수혜 구조" |
| `macro` | 거시경제/정책/시장 전체 | "FOMC 프리뷰 - 6월 인하 시작 전망" |

산업/매크로 리포트는 **특정 종목을 더 잘 분석하기 위한 도구**이거나 **산업군에서 주목할 종목을 찾기 위한 용도**로 활용됨.

### 2단계 LLM 호출로 처리

**1단계 (분류)** — 짧은 호출, 저비용. `report_type`만 판별.

```
이 리포트의 유형을 판별해주세요.
- stock: 특정 종목을 분석하는 리포트
- industry: 특정 산업/섹터를 분석하는 리포트
- macro: 거시경제/정책/시장 전체를 다루는 리포트

JSON으로만 응답: {"report_type": "stock|industry|macro"}
```

**2단계 (추출)** — 타입에 맞는 스키마를 프롬프트에 포함하여 구조화 추출.

```
다음 증권 리포트에서 아래 JSON 스키마에 맞게 정보를 추출하세요.
확인할 수 없는 필드는 null로 남겨주세요.
추정치는 리포트에 명시된 수치만 사용하세요.

[타입별 JSON 스키마 제공]
```

분류에는 토큰을 적게 쓰고, 추출에만 풀 텍스트를 보내서 비용을 아낌.

---

## 5. Layer 2 스키마 설계

### 5.1 핵심 설계: 인과관계 체인 (Investment Chain)

단편적 요약(예: "HBM3E 양산 본격화로 고부가 비중 확대")이 아니라, 애널리스트의 **논리 흐름 자체를 근거와 수치까지 포함하여 보존**하는 구조.

#### 왜 단편적 요약으로는 부족한가

리포트 원문에 "HBM3E는 기존 HBM3 대비 용량 1.5배, 대역폭 20% 향상. 엔비디아 B200 탑재 확정으로 25년 하반기부터 양산 물량 본격 출하"라고 있는 걸 "HBM3E 양산 본격화"로 압축하면 Agent가 **왜 그런지, 얼마나인지**를 알 수 없음.

#### step 타입 (고정 enum)

| step 타입 | 설명 | 사용 컨텍스트 |
|-----------|------|---------------|
| `trigger` | 논리의 출발점 (이벤트, 정책 등) | 공통 |
| `mechanism` | trigger가 작동하는 메커니즘 | 공통 |
| `demand_transmission` | 수요 전달 경로 | 산업 |
| `supply_dynamics` | 공급 측 동학 | 산업 |
| `pricing_impact` | 가격/마진 영향 | 산업 |
| `financial_impact` | 재무적 영향 (실적 변화) | 종목/산업 |
| `valuation_impact` | 밸류에이션 영향 | 종목 |
| `structural_risk` | 구조적 리스크 | 공통 |
| `uncertainty` | 불확실성 요인 | 공통 |
| `data_signal` | 데이터 시그널 (경제지표 등) | 매크로 |
| `policy_logic` | 정책 논리/방향성 | 매크로 |
| `market_transmission` | 시장 전달 경로 | 매크로 |
| `local_impact` | 국내 시장 영향 | 매크로 |

### 5.2 종목 리포트 스키마

```json
{
  "report_type": "stock",
  "target": {
    "company_name": "삼성전자",
    "ticker": "005930",
    "sector": "반도체",
    "industry_keywords": ["HBM", "AI반도체", "파운드리"]
  },
  "opinion": {
    "rating": "매수",
    "rating_change": "유지",
    "target_price": 95000,
    "target_price_change": "상향",
    "prev_target_price": 85000,
    "valuation_method": "PER 15배 적용 (25년 예상 EPS 기준)",
    "upside_pct": 28.4
  },
  "thesis": {
    "investment_chains": [
      {
        "conclusion": "HBM 사업이 삼성전자 수익성의 구조적 전환점",
        "chain": [
          {
            "step": "trigger",
            "statement": "엔비디아 B200/B300 시리즈가 HBM3E를 기본 탑재",
            "evidence": "엔비디아 25년 로드맵 기준 B200 하반기 양산, B300은 26년 상반기"
          },
          {
            "step": "mechanism",
            "statement": "삼성전자 HBM3E 양산 본격화로 공급 물량 확보",
            "evidence": "HBM3E 수율 80% 이상 안정화, 월 생산능력 기존 대비 2배 확대 완료"
          },
          {
            "step": "financial_impact",
            "statement": "DRAM 내 HBM 매출 비중이 급격히 확대되며 ASP 믹스 개선",
            "evidence": "HBM 비중 15%→28% 전망, HBM ASP는 범용 DDR5 대비 5~6배"
          },
          {
            "step": "valuation_impact",
            "statement": "이익 추정치 상향이 밸류에이션 리레이팅으로 연결",
            "evidence": "26년 영업이익 16조→18.5조 상향, 역사적 HBM 사이클에서 PBR 1.5→2.2배 리레이팅 사례"
          }
        ],
        "confidence": "high",
        "risk_to_thesis": "엔비디아 외 고객사 확보 지연 시 물량 리스크"
      },
      {
        "conclusion": "파운드리 턴어라운드는 아직 초기 단계",
        "chain": [
          {
            "step": "trigger",
            "statement": "2nm GAA 공정에서 퀄컴 차기 AP 수주 협의 진행 중"
          },
          {
            "step": "mechanism",
            "statement": "테스트칩 수율 70% 달성으로 기술 신뢰도 확보 중",
            "evidence": "24년 말 기준, TSMC 동일 시점 대비 6개월 지연이나 격차 축소 추세"
          },
          {
            "step": "uncertainty",
            "statement": "실제 양산 수율과 수주 확정까지는 불확실성 존재",
            "evidence": "양산 수율 목표 85% 대비 현재 70%, 수주 확정은 26년 상반기 예상"
          }
        ],
        "confidence": "medium",
        "risk_to_thesis": "TSMC 가격 인하 시 경쟁력 약화"
      }
    ],
    "bear_chains": [
      {
        "conclusion": "범용 DRAM 가격 하락이 HBM 수혜를 일부 상쇄",
        "chain": [
          {
            "step": "trigger",
            "statement": "PC/모바일 수요 부진이 예상보다 장기화"
          },
          {
            "step": "mechanism",
            "statement": "중국 CXMT의 DDR5 범용 공급 확대로 가격 경쟁 심화",
            "evidence": "CXMT DDR5 월 생산 웨이퍼 5만장→8만장 확대 계획"
          },
          {
            "step": "financial_impact",
            "statement": "DDR5 범용 ASP 분기 -8~10% 하락 전망",
            "evidence": "25H1 계약가 기준 전분기 대비 하락 전환 확인"
          }
        ],
        "confidence": "high",
        "risk_to_thesis": "AI 서버 외 수요 회복 시 반전 가능"
      }
    ],
    "rating_change_reason": {
      "direction": "목표가 상향",
      "primary_driver": "HBM 매출 비중 확대에 따른 이익 추정치 상향",
      "logic": "HBM 비중 확대 → 블렌디드 ASP 상승 → OPM +2~3%p → 26E OP 16조→18.5조 → PER 15배 적용 시 목표가 85K→95K"
    }
  },
  "financials": {
    "recent_actual": {
      "period": "2025Q4",
      "revenue": 790000,
      "operating_profit": 120000,
      "net_income": 95000
    },
    "estimates": [
      {
        "period": "2026E",
        "revenue": 880000,
        "operating_profit": 160000,
        "net_income": 128000
      }
    ],
    "revenue_breakdown": [
      {"segment": "반도체", "pct": 55, "yoy_change": "+12%"},
      {"segment": "디스플레이", "pct": 15, "yoy_change": "-3%"},
      {"segment": "모바일", "pct": 25, "yoy_change": "+5%"}
    ],
    "key_ratios": {
      "per_fwd": 12.5,
      "pbr": 1.8,
      "roe": 18.2,
      "debt_ratio": 35.4
    }
  },
  "context": {
    "related_companies": ["SK하이닉스", "TSMC"],
    "industry_drivers": ["AI 서버 투자 확대", "HBM 수급 타이트"],
    "regulation_issues": ["미중 반도체 수출규제"]
  }
}
```

### 5.3 산업 리포트 스키마

```json
{
  "report_type": "industry",
  "industry": {
    "sector": "2차전지",
    "sub_sectors": ["배터리셀", "양극재", "음극재", "전해질"],
    "value_chain_position": "완성차 OEM → 배터리셀 → 소재(양극재/음극재/전해질)",
    "industry_keywords": ["EV배터리", "LFP", "하이니켈", "전고체", "유럽보조금"]
  },
  "industry_chains": [
    {
      "conclusion": "유럽 EV 보조금 재개가 배터리 밸류체인 전반의 실적 턴어라운드 촉발",
      "chain": [
        {
          "step": "trigger",
          "statement": "EU가 26년 Q2부터 EV 구매보조금 재도입 확정",
          "evidence": "차량당 최대 5,000유로, 배터리 용량 40kWh 이상 조건, 26년 3월 EU 의회 통과"
        },
        {
          "step": "mechanism",
          "statement": "유럽 EV 판매량이 24~25년 정체기를 벗어나 회복 전환",
          "evidence": "유럽 EV 판매량 전망 25년 280만대 → 26년 380만대 (+36% YoY)"
        },
        {
          "step": "demand_transmission",
          "statement": "OEM의 배터리 발주가 셀 메이커를 거쳐 소재사까지 전달",
          "evidence": "VW/BMW/Stellantis 26년 배터리 조달 계획 +25~30% 상향, 통상 OEM 발주 → 셀 생산 → 소재 납품까지 3~6개월 시차"
        },
        {
          "step": "supply_dynamics",
          "statement": "하이니켈 양극재는 수요 회복 대비 공급 증설이 제한적이라 타이트한 수급 예상",
          "evidence": "24~25년 업황 부진으로 증설 연기, 하이니켈 글로벌 capa 증가율 25년 +8% vs 수요 증가율 26년 +20%"
        },
        {
          "step": "pricing_impact",
          "statement": "양극재 가격 반등이 소재사 수익성 회복으로 직결",
          "evidence": "하이니켈 양극재 톤당 $12,000 → $15,000 전망 (26H2), 소재사 스프레드 $2,000→$2,800 회복 시 OPM 5%→9%"
        }
      ],
      "confidence": "high",
      "risk_to_thesis": "중국 LFP 배터리의 유럽 점유율 확대 시 하이니켈 수혜 제한"
    },
    {
      "conclusion": "LFP 전환 가속은 한국 소재사에 구조적 위협",
      "chain": [
        {
          "step": "trigger",
          "statement": "테슬라/BYD의 LFP 채택 확대가 유럽 OEM으로 전이 조짐",
          "evidence": "르노 26년 신모델 50%를 LFP로 전환 발표"
        },
        {
          "step": "mechanism",
          "statement": "LFP는 중국 CATL/BYD가 원가 우위를 독점적으로 보유",
          "evidence": "LFP 셀 단가 $55/kWh vs 하이니켈 $85/kWh, 한국 업체 LFP 양산 경험 부재"
        },
        {
          "step": "structural_risk",
          "statement": "유럽 시장에서 LFP 비중이 올라갈수록 한국 배터리 밸류체인의 파이 축소",
          "evidence": "유럽 LFP 비중 전망 25년 15% → 28년 30%, 한국 업체 유럽 점유율 25년 65% → 28년 50% 하락 리스크"
        }
      ],
      "confidence": "medium",
      "risk_to_thesis": "전고체 배터리 상용화 시 하이니켈 진영 역전 가능"
    }
  ],
  "stock_implications": [
    {
      "company": "LG에너지솔루션",
      "ticker": "373220",
      "impact": "수혜",
      "linked_chain": "유럽 EV 보조금 재개",
      "logic": "유럽 OEM向 장기계약 비중 60% 이상 → 보조금 수혜가 가장 직접적으로 전달 → 26년 유럽 출하량 +35% YoY 전망",
      "sensitivity": "유럽 EV 판매 10% 추가 증가 시 매출 +8%, OP +15% 추정"
    },
    {
      "company": "에코프로비엠",
      "ticker": "247540",
      "impact": "수혜",
      "linked_chain": "유럽 EV 보조금 재개",
      "logic": "하이니켈 양극재 전문 → 수급 타이트닝 시 가격 협상력 확보 → 스프레드 회복이 이익 레버리지로 작용",
      "sensitivity": "양극재 가격 톤당 $1,000 상승 시 OPM +2%p"
    },
    {
      "company": "포스코퓨처엠",
      "ticker": "003670",
      "impact": "혼재",
      "linked_chain": "유럽 EV 보조금 재개 + LFP 전환 가속",
      "logic": "하이니켈 양극재 수혜는 동일하나, LFP 대응 전략 부재로 중장기 포트폴리오 리스크 → 단기 수혜 vs 중기 구조적 우려 병존",
      "sensitivity": "LFP 비중 5%p 추가 확대 시 중기 매출 성장률 -3%p 하향 리스크"
    }
  ],
  "context": {
    "regulation_issues": ["EU CBAM 27년 본격 시행", "IRA 보조금 한국 업체 적용 범위 불확실"],
    "risk_factors": ["리튬 가격 재상승 시 소재 원가 부담", "중국 과잉공급 장기화"],
    "related_reports_keywords": ["EV판매량", "양극재가격", "LFP점유율"]
  }
}
```

### 5.4 매크로 리포트 스키마

```json
{
  "report_type": "macro",
  "macro_topic": {
    "category": "통화정책",
    "region": "미국",
    "keywords": ["FOMC", "금리인하", "국채금리", "달러약세"]
  },
  "macro_chains": [
    {
      "conclusion": "6월 FOMC에서 25bp 인하 개시, 연내 총 75bp 인하 전망",
      "chain": [
        {
          "step": "data_signal",
          "statement": "인플레이션이 연준 목표에 근접하며 인하 조건 충족",
          "evidence": "Core PCE 2.4% (목표 2.0%), CPI 2.8%로 둔화 추세 지속, 3개월 연율 기준 2.1%까지 하락"
        },
        {
          "step": "data_signal",
          "statement": "고용시장이 완만한 둔화세로 연준의 균형 잡힌 접근 가능",
          "evidence": "비농업 고용 +15만(3개월 평균), 실업률 4.1%, 임금상승률 3.5%로 과열 아님"
        },
        {
          "step": "policy_logic",
          "statement": "연준이 선제적 인하보다 데이터 확인 후 점진적 인하를 선택할 것",
          "evidence": "파월 의장 3월 의회 증언에서 'patient but not complacent' 발언, 점도표 중간값 연내 3회 인하 시사"
        },
        {
          "step": "market_transmission",
          "statement": "금리 인하 기대가 장기금리 하락과 달러 약세로 전달",
          "evidence": "미 10년물 국채금리 4.2%→3.8% 전망(연말), 달러인덱스 104→99 전망, 선물시장에 6월 인하 78% 선반영"
        }
      ],
      "confidence": "high",
      "risk_to_thesis": "관세 부과 확대 시 인플레이션 재점화로 인하 지연"
    },
    {
      "conclusion": "달러 약세 전환이 신흥국 자금 유입을 촉발",
      "chain": [
        {
          "step": "trigger",
          "statement": "미 금리 인하 사이클 개시가 달러 약세 압력을 구조적으로 강화"
        },
        {
          "step": "mechanism",
          "statement": "금리 차이 축소로 캐리 트레이드 매력 감소, EM으로 자금 재배분",
          "evidence": "과거 3차례 인하 사이클 개시 후 6개월간 EM 주식형 펀드 평균 +$45B 순유입"
        },
        {
          "step": "local_impact",
          "statement": "원화 강세 전환 시 외국인 한국 주식 순매수 확대 예상",
          "evidence": "원달러 전망 1,380→1,320(연말), 외국인 순매수와 원화 강세 상관계수 0.72(최근 5년)"
        }
      ],
      "confidence": "medium",
      "risk_to_thesis": "지정학 리스크(대만 해협, 중동) 발생 시 안전자산 선호로 역전"
    }
  ],
  "sector_implications": [
    {
      "sector": "성장주/기술주",
      "impact": "긍정",
      "linked_chain": "6월 FOMC 인하 개시",
      "logic": "할인율 하락 → 장기 이익의 현재가치 상승 → DCF 밸류에이션 리레이팅, 과거 인하 사이클 개시 후 6개월간 KOSDAQ 평균 +18%",
      "representative_stocks": ["삼성전자", "SK하이닉스", "네이버", "카카오"]
    },
    {
      "sector": "금융",
      "impact": "혼재",
      "linked_chain": "6월 FOMC 인하 개시",
      "logic": "NIM 축소 압력(단기 부정)이나 자산건전성 개선 + 채권평가익(중기 긍정), 순효과는 은행별 포트폴리오에 따라 상이",
      "representative_stocks": ["KB금융", "신한지주", "하나금융"]
    },
    {
      "sector": "수출주/원화 민감",
      "impact": "부정",
      "linked_chain": "달러 약세 → 원화 강세",
      "logic": "원달러 60원 하락 시 수출기업 영업이익 평균 -3~5% 영향, 자동차/조선 등 달러 매출 비중 높은 업종 직격",
      "representative_stocks": ["현대차", "기아", "HD한국조선해양"]
    }
  ],
  "context": {
    "key_upcoming_events": [
      {"event": "6월 FOMC", "date": "2026-06-17", "importance": "high"},
      {"event": "5월 CPI 발표", "date": "2026-06-11", "importance": "high"},
      {"event": "ECB 금리결정", "date": "2026-06-05", "importance": "medium"}
    ],
    "risk_factors": ["트럼프 관세 확대", "중동 지정학 리스크", "일본 BOJ 정책 변경"],
    "related_reports_keywords": ["금리인하", "달러약세", "원화강세", "외국인순매수"]
  }
}
```

### 5.5 분량 가이드라인

| 항목 | 수치 |
|------|------|
| 리포트 한 건당 Layer 2 | 약 2,000~3,000 토큰 (한글 기준 약 1,500~2,000자) |
| 원문 대비 압축률 | 약 1/10~1/15 |
| Agent 컨텍스트에 동시 투입 가능 | 15~25건 |

---

## 6. 크로스 리포트 연결 구조

### 연결 키 3가지

| 연결 경로 | 매칭 방식 | 예시 |
|-----------|-----------|------|
| `ticker` | 직접 매칭 | 삼성전자(005930) 종목 리포트 |
| `industry_keywords` | 키워드 매칭 | "HBM" 키워드로 반도체 산업 리포트 매칭 |
| `representative_stocks` + `sector` | 섹터/종목 매핑 | 매크로 리포트의 "기술주" → 삼성전자 |

### 연결 흐름도

```
┌─────────────────────────────────────────────────────┐
│  매크로 리포트                                        │
│  ┌─────────────────┐  ┌──────────────────────┐       │
│  │ FOMC 인하 → 할인율│  │ 달러약세 → 원화강세    │       │
│  └────────┬────────┘  └──────────┬───────────┘       │
└───────────┼──────────────────────┼───────────────────┘
            │ sector_implications  │ sector_implications
            ▼                      ▼
┌─────────────────────────────────────────────────────┐
│  산업 리포트                                          │
│  ┌──────────────────────────┐  ┌─────────────────┐   │
│  │ EU보조금 → EV수요 회복     │  │ LFP 전환 가속    │   │
│  │ → 양극재 수급 타이트닝      │  │ → 한국 소재사 위협│   │
│  └────────────┬─────────────┘  └───────┬─────────┘   │
└───────────────┼────────────────────────┼─────────────┘
                │ stock_implications     │ stock_implications
                ▼                        ▼
┌─────────────────────────────────────────────────────┐
│  종목 리포트                                          │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐          │
│  │LG에너지솔루│  │에코프로비엠│  │포스코퓨처엠 │          │
│  │유럽출하+35%│  │스프레드회복│  │수혜+LFP리스크│         │
│  └──────────┘  └──────────┘  └───────────┘          │
└─────────────────────────────────────────────────────┘

연결 키: ticker / industry_keywords / representative_stocks
```

### Agent 질의 시 작동 흐름

```
질문: "에코프로비엠 전망 알려줘"

1단계: report_stock_mentions에서 에코프로비엠(247540) 관련 리포트 전부 검색
    → mention_type='primary' (직접 종목 리포트)
    → mention_type='implication' (산업 리포트에서 언급)

2단계: report_keywords + report_sector_mentions로 관련 매크로 리포트 매칭

3단계: 각 리포트의 report_analysis.analysis_data를 YAML로 변환

4단계: Agent 프롬프트에 투입하여 종합 분석 생성

5단계: 필요 시 report_markdown 또는 PDF 원문 참조
```

---

## 7. 데이터 포맷 전략

### 연구 결과 요약

LLM이 구조화된 데이터를 얼마나 잘 이해하는지는 포맷에 따라 유의미하게 달라짐. 벤치마크 연구 결과:

- **YAML**: 중첩 데이터에서 가장 높은 정확도 (JSON 대비 +12%p). 들여쓰기 기반 시각적 계층 구조가 LLM의 관계 파악에 도움.
- **JSON**: 범용적이고 API 지원 탄탄하나, 엄격한 JSON 출력 강제 시 추론 성능 10~15% 하락.
- **Markdown**: LLM 학습 데이터에 대량 포함되어 친숙도 높음. JSON 대비 평균 16% 토큰 절감.
- **XML**: 정확도 최하위 (YAML 대비 -17.7%p), 토큰 소비 최다 (Markdown 대비 +80%). 데이터 포맷으로 비추천.

> 참고: Anthropic이 Claude 프롬프트 구조화에 XML 태그를 권장하는 것은 "프롬프트 섹션 구분"의 맥락이며, "데이터 표현 포맷"과는 별개.

### 용도별 포맷 결정

| 용도 | 포맷 | 이유 |
|------|------|------|
| DB 저장 | **JSON** (PostgreSQL `jsonb`) | 쿼리, 인덱싱, 부분 추출 네이티브 지원 |
| Agent 프롬프트 투입 | **YAML** | 중첩 데이터 정확도 최고, 토큰 절약 |
| 사용자 출력 | **Markdown** | 가독성 최고, LLM이 자연스럽게 생성 |

### 변환 파이프라인

```
DB (jsonb) → Python yaml.dump() → Agent 프롬프트 (YAML)
                                  → Agent 응답 생성 (Markdown) → 사용자
```

### YAML vs Markdown: Agent 내부 처리에서 YAML을 선택한 이유

Markdown은 단일 리포트 표시에는 가독성이 좋지만, Agent가 **20~30건을 동시에 크로스 분석**할 때 두 가지 문제:

1. **경계가 모호**: 리포트 30건의 Markdown을 이어붙이면 `##` 헤딩이 수십 개. YAML은 리스트 아이템(`-`)으로 경계가 구조적으로 명확.
2. **필드 접근 불안정**: "3월 리포트의 trigger step evidence"를 YAML은 `chain[0].evidence`로 경로가 명확하지만, Markdown은 자연어로 찾아야 해서 오류율 상승.

---

## 8. PDF → Markdown 변환 레이어

### 변환이 필요한 이유

PDF에서 raw 텍스트를 뽑으면 헤더/푸터 반복, 페이지 번호, 각주가 뒤섞여서 노이즈가 심함. Markdown으로 변환하면:

1. **LLM 이해도 향상** — 노이즈 제거된 깨끗한 구조
2. **토큰 효율 개선** — raw PDF 텍스트 대비 불필요한 부분 제거
3. **재추출 용이** — Layer 2 스키마 변경 시 PDF 재변환 없이 Markdown에서 재추출

### 확장된 파이프라인

```
PDF 원본 (아카이브 보관, Layer 3)
  ↓
Markdown 변환 (Marker/MinerU) → report_markdown 테이블에 저장
  ↓
LLM 1단계: 분류 (종목/산업/매크로)
  ↓
LLM 2단계: Layer 2 추출 (타입별 체인 스키마)
  ↓
JSON으로 report_analysis + 연결 테이블들에 저장
```

### 도구 추천

| 도구 | 특징 | 적합 케이스 |
|------|------|------------|
| **Marker** | `--use_llm` 모드로 테이블 병합/수식 처리 개선, 다국어 강점, 빠른 속도 | 범용, 배치 처리 |
| **MinerU** | 테이블 인식 특히 강함, 상업용 도구 수준 파싱 품질 | 복잡한 재무 테이블 |
| **PyMuPDF4LLM** | 가벼움, pip 한 줄 설치, 헤더/푸터 제거 지원 | 빠른 도입, 간단한 리포트 |

### 유실 분석

| 요소 | 유실 여부 | Layer 2 추출에 미치는 영향 |
|------|-----------|--------------------------|
| 본문 텍스트 (투자 논리) | 거의 없음 | 체인 추출의 핵심 소스, 문제 없음 |
| 기본 테이블 (실적 추정) | 대부분 보존 | 재무 데이터 추출 가능 |
| 복잡 다단 테이블 | 일부 유실 가능 | 수치 데이터 별도 분석 시 큰 영향 없음 |
| 차트/그래프 | 유실됨 (이미지만 추출) | 핵심 메시지는 보통 본문에도 서술됨 |

---

## 9. DB 스키마 설계

### 설계 원칙

- 기존 `reports` 테이블 및 수집 로직은 전혀 건드리지 않음
- Layer 2 관련 테이블을 별도 추가하여 독립적 운영
- `report_id` FK로 기존 테이블과 연결

### 신규 테이블 (6개)

#### 9.1 `report_markdown`

PDF → Markdown 변환 결과 저장. 리포트당 1건.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL PK | |
| `report_id` | INTEGER FK (UNIQUE) | reports.id 참조 |
| `markdown_text` | TEXT | 변환된 Markdown 전문 |
| `converter` | VARCHAR(50) | 'marker', 'mineru', 'pymupdf4llm' |
| `token_count` | INTEGER | 대략적 토큰 수 (비용 추정용) |
| `created_at` | TIMESTAMPTZ | |

#### 9.2 `report_analysis`

Layer 2 핵심 테이블. 리포트당 1건.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL PK | |
| `report_id` | INTEGER FK (UNIQUE) | reports.id 참조 |
| `report_category` | VARCHAR(20) | 'stock', 'industry', 'macro' |
| `analysis_data` | JSONB | 체인 스키마 전체 (5장 참조) |
| `schema_version` | VARCHAR(20) | 스키마 버전 (재처리 판단용) |
| `extraction_quality` | VARCHAR(20) | 'high', 'medium', 'low' |
| `llm_model` | VARCHAR(100) | 추출에 사용한 모델 |
| `llm_cost_usd` | NUMERIC(10,6) | 추출 비용 |
| `created_at` | TIMESTAMPTZ | |

JSONB 인덱스: `target.ticker`, `industry.sector`, `stock_implications`, 전체 jsonb_path_ops GIN 인덱스.

#### 9.3 `report_stock_mentions`

리포트-종목 다대다 매핑. Agent의 종목별 리포트 검색 핵심.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL PK | |
| `report_id` | INTEGER FK | reports.id 참조 |
| `stock_code` | VARCHAR(20) | 종목코드 |
| `company_name` | VARCHAR(100) | 종목명 |
| `mention_type` | VARCHAR(20) | 'primary', 'implication', 'related' |
| `impact` | VARCHAR(20) | 'positive', 'negative', 'neutral', 'mixed' |
| `relevance_score` | NUMERIC(3,2) | 0.00~1.00 (optional) |

UNIQUE 제약: `(report_id, stock_code)`

#### 9.4 `report_sector_mentions`

리포트-섹터 매핑.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL PK | |
| `report_id` | INTEGER FK | reports.id 참조 |
| `sector` | VARCHAR(100) | 섹터명 |
| `mention_type` | VARCHAR(20) | 'primary', 'implication' |
| `impact` | VARCHAR(20) | 'positive', 'negative', 'neutral', 'mixed' |

#### 9.5 `report_keywords`

키워드 태그. 크로스 리포트 검색 지원.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL PK | |
| `report_id` | INTEGER FK | reports.id 참조 |
| `keyword` | VARCHAR(100) | 키워드 |
| `keyword_type` | VARCHAR(30) | 'industry', 'macro', 'product', 'policy', 'event' |

#### 9.6 `analysis_jobs`

변환/분류/추출 처리 로그.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL PK | |
| `report_id` | INTEGER FK | reports.id 참조 |
| `job_type` | VARCHAR(30) | 'markdown_convert', 'classify', 'extract_layer2' |
| `status` | VARCHAR(20) | 'pending', 'running', 'success', 'failed' |
| `error_message` | TEXT | 실패 시 에러 메시지 |
| `llm_model` | VARCHAR(100) | |
| `input_tokens` | INTEGER | |
| `output_tokens` | INTEGER | |
| `cost_usd` | NUMERIC(10,6) | |
| `started_at` | TIMESTAMPTZ | |
| `finished_at` | TIMESTAMPTZ | |

### 기존 테이블 변경 (최소)

`reports` 테이블에 3개 컬럼 추가 (기존 로직 영향 없음):

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `analysis_status` | VARCHAR(20) | 'pending', 'processing', 'done', 'failed' |
| `analysis_version` | VARCHAR(20) | 현재 적용된 스키마 버전 |
| `markdown_converted` | BOOLEAN | Markdown 변환 완료 여부 |

### 편의 뷰 (3개)

| 뷰 | 용도 | 핵심 쿼리 |
|----|------|-----------|
| `v_stock_latest_analysis` | 종목별 최신 리포트 + Layer 2 | `WHERE report_category = 'stock' ORDER BY report_date DESC` |
| `v_stock_all_reports` | 종목 관련 전체 리포트 (직접 + 산업/매크로 언급) | `report_stock_mentions JOIN report_analysis` |
| `v_sector_reports` | 섹터별 관련 리포트 | `report_sector_mentions JOIN report_analysis` |

### pgvector (향후 확장)

- Railway PostgreSQL에서 `CREATE EXTENSION IF NOT EXISTS vector;`로 활성화
- `report_analysis` 테이블에 `embedding vector(1536)` 컬럼 추가
- 키워드 매칭으로 못 찾는 유사 리포트 검색에 활용
- 임베딩 모델: OpenAI `text-embedding-3-small` 또는 Voyage AI (LLM 교체와 독립 운영 가능)
- 당장 필수는 아님. Layer 2가 잘 구조화되어 있으면 SQL 필터링만으로 초기 운영 가능.

---

## 10. 설계 Q&A (검토 피드백)

### Q1. 연결 테이블(`report_stock_mentions` 등)과 `analysis_data` JSONB 간의 의도적 중복인가?

**의도적 중복이 맞다.** 이유는 두 가지:

1. **검색 성능**: JSONB 내부를 GIN 인덱스로 뒤지는 것보다 정규화된 테이블에서 `WHERE stock_code = '005930'`이 훨씬 빠르고 예측 가능. 리포트 수천 건 이상에서 차이가 커짐.
2. **크로스 타입 검색**: 종목 리포트의 `target.ticker`와 산업 리포트의 `stock_implications[].ticker`는 JSONB 안에서 경로가 다름. 평탄화된 테이블이 있어야 하나의 쿼리로 합칠 수 있음.

**싱크 관리 방안**: Layer 2 추출 시 `report_analysis` INSERT와 연결 테이블 INSERT를 하나의 트랜잭션으로 묶음. 재처리 시에도 `report_id` 기준으로 연결 테이블 DELETE → 재INSERT. 추출 로직을 하나의 함수로 캡슐화하여 항상 세트로 처리되게 강제.

**대안 (초기 단순화)**: 연결 테이블 없이 JSONB 인덱스만으로 시작하고, 검색 성능이 문제가 되는 시점에 연결 테이블 추가. 단, 그 시점에 기존 건 전체 백필 필요.

### Q2. `analysis_jobs` vs `reports.analysis_status` — 상태 추적이 두 곳에 분산되는 건?

**역할 분리가 맞다.**

- `reports.analysis_status` = **현재 스냅샷**. Agent가 질의할 때 `WHERE analysis_status = 'done'`으로 분석 완료된 건만 빠르게 필터링.
- `analysis_jobs` = **히스토리 로그**. markdown_convert → classify → extract_layer2 세 단계의 성공/실패/비용/소요시간 기록. 스키마 v2 재처리 시 "v1은 성공, v2는 실패" 같은 이력 추적.

`reports.analysis_status`는 `analysis_jobs`에서 파생 가능하지만, 매번 jobs 테이블을 조회하는 것보다 비정규화된 상태값이 실용적. 업데이트 시점: `analysis_jobs`에 마지막 단계(`extract_layer2`)가 `success`로 기록될 때 `reports.analysis_status = 'done'`으로 함께 갱신.

### Q3. `report_markdown` 영구 저장이 필요한가?

**영구 저장을 권장한다.** 이유는 비용 구조:

| 시나리오 | Markdown 보관 시 | Markdown 미보관 시 |
|----------|------------------|---------------------|
| Layer 2 스키마 변경 (빈번) | Markdown에서 바로 재추출 (빠름, 저비용) | PDF부터 재변환 필요 (느림, 고비용) |
| 변환 도구 업그레이드 (드묾) | Markdown 삭제 후 재변환 | 동일 |
| 스토리지 비용 | 리포트 1,000건 기준 10~50MB | 없음 |

스키마 변경이 변환 도구 업그레이드보다 훨씬 빈번하게 일어나므로, Markdown을 보관하면 대부분의 재처리에서 PDF 단계를 건너뛸 수 있음.

**대안 (스토리지 절약)**: Markdown 미저장하되 변환 도구/버전을 `analysis_jobs`에 기록. 재처리 필요 시 PDF에서 다시 변환. 시간/비용 트레이드오프 감수 필요.

---

## 11. 구현 로드맵

### Phase 1: 인프라 준비

- [ ] alembic 설정 정리 및 마이그레이션 이력 관리 시작
- [ ] DB 마이그레이션 실행 (`schema_migration.sql`)
- [ ] PDF → Markdown 변환 도구 선정 및 설치 (Marker / MinerU / PyMuPDF4LLM)
- [ ] 변환 도구 테스트: 실제 증권 리포트 5~10건으로 변환 품질 확인

### Phase 2: 수집 파이프라인 확장

- [ ] 기존 수집 후 Markdown 변환 단계 추가 → `report_markdown` 저장
- [ ] LLM 1단계 분류 로직 구현 (stock / industry / macro)
- [ ] 타입별 Layer 2 추출 프롬프트 작성
- [ ] 추출 결과를 `report_analysis` + 연결 테이블에 트랜잭션으로 저장
- [ ] `analysis_jobs`에 처리 로그 기록
- [ ] `reports.analysis_status` 갱신 로직

### Phase 3: 기존 리포트 백필

- [ ] 기존 PDF 아카이브에서 Markdown 일괄 변환 (배치)
- [ ] 기존 리포트 Layer 2 일괄 추출 (배치)
- [ ] 추출 품질 검증: 랜덤 샘플 수동 검토
- [ ] 프롬프트 튜닝 및 재추출

### Phase 4: Agent 구현

- [ ] 질의 → SQL 필터링 (v_stock_all_reports 등 뷰 활용)
- [ ] 검색된 Layer 2 → YAML 변환 → 프롬프트 구성
- [ ] Agent 응답 생성 (종합 분석, Markdown 출력)
- [ ] PDF 원문 참조 fallback 로직
- [ ] 웹 인터페이스 또는 챗봇 UI

### Phase 5: 고도화

- [ ] pgvector 도입: 임베딩 기반 유사 리포트 검색
- [ ] 스키마 버전 관리: v1 → v2 자동 재처리 파이프라인
- [ ] 추출 품질 자동 평가 로직
- [ ] 종목별 시계열 대시보드

---

## 12. 비용 추정

### 리포트 한 건당 처리 비용 (Claude Sonnet 기준)

| 단계 | 입력 토큰 | 출력 토큰 | 예상 비용 |
|------|-----------|-----------|-----------|
| 분류 | ~500 | ~50 | ~$0.002 |
| Layer 2 추출 | ~5,000-15,000 | ~2,000-3,000 | ~$0.02-0.06 |
| **합계** | | | **~$0.02-0.06/건** |

### 월간 운영 비용 (하루 15건 기준)

| 항목 | 비용 |
|------|------|
| Layer 2 추출 (월 450건) | ~$18/월 |
| Agent 질의 응답 (하루 10회 가정) | ~$10-20/월 |
| Railway PostgreSQL | 기존 비용 |
| **합계** | **~$30-40/월** |

### 일회성 비용

| 항목 | 비용 |
|------|------|
| 기존 리포트 1,000건 백필 | ~$40 |
| Markdown 변환 (로컬 처리 시) | 거의 무료 |

---

## 13. 주의사항 및 권장사항

1. **스키마를 처음부터 너무 고정하지 말 것.** Agent를 실제로 써보면서 "이 정보가 부족하다"는 피드백을 반영하여 스키마를 확장하고, `schema_version`을 올려서 기존 건을 재처리하는 사이클을 돌릴 것.

2. **PDF 원본은 반드시 보관.** Markdown 변환이나 Layer 2 추출 로직이 바뀔 때 언제든 재처리 가능.

3. **LLM 교체 가능성을 고려.** 추출 프롬프트를 모델 의존적이지 않게 작성하고, 모델별 출력 차이를 `llm_model` 컬럼으로 추적.

4. **schema_version을 활용.** 스키마가 v2로 바뀌면 v1인 건만 골라서 재처리. 전량 재처리 대비 효율적.

5. **추출 품질 모니터링.** 초기에는 `extraction_quality` 필드를 수동 검토하여 프롬프트 품질을 개선.

6. **연결 테이블 싱크 관리.** `report_analysis` INSERT와 연결 테이블 INSERT를 반드시 하나의 트랜잭션으로 처리. 추출 로직을 캡슐화하여 부분 업데이트 방지.

7. **alembic 정리.** 이번 기회에 alembic 마이그레이션 이력 관리를 시작하여, 이후 스키마 변경을 체계적으로 추적.

8. **DB 비밀번호 변경.** 설계 과정에서 DATABASE_URL이 노출되었으므로, Railway에서 PostgreSQL 비밀번호를 재생성할 것.

---

## 부록: 마이그레이션 SQL

별첨 파일 `schema_migration.sql` 참조. psql로 실행:

```bash
psql "postgresql://[USER]:[PASSWORD]@[HOST]:[PORT]/railway" -f schema_migration.sql
```

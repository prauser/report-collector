-- ============================================================
-- Layer 2 Analysis Schema Migration
-- 기존 reports 테이블은 그대로 유지, 분석 테이블을 별도 추가
-- ============================================================

-- 1. 리포트 Markdown 변환 저장
-- PDF → Markdown 변환 결과를 별도 저장 (재처리 시 PDF 재변환 불필요)
CREATE TABLE IF NOT EXISTS report_markdown (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    markdown_text   TEXT NOT NULL,
    converter       VARCHAR(50),          -- 'marker', 'mineru', 'pymupdf4llm' 등
    token_count     INTEGER,              -- 대략적 토큰 수 (비용 추정용)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,

    CONSTRAINT uq_report_markdown UNIQUE (report_id)
);
-- NOTE: idx_report_markdown_report_id 불필요 (UNIQUE가 이미 인덱스 생성)


-- 2. Layer 2 분석 결과 (핵심 테이블)
-- 리포트 한 건당 하나의 구조화된 분석 결과
CREATE TABLE IF NOT EXISTS report_analysis (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,

    -- 리포트 분류
    report_category VARCHAR(20) NOT NULL,  -- 'stock', 'industry', 'macro'

    -- 전체 Layer 2 구조화 데이터 (체인 포함)
    -- JSON으로 저장, Agent 질의 시 YAML로 변환하여 프롬프트 투입
    analysis_data   JSONB NOT NULL,

    -- 추출 메타
    llm_model       VARCHAR(100),          -- 추출에 사용한 모델
    llm_cost_usd    NUMERIC(10,6),         -- 추출 비용
    schema_version  VARCHAR(20) NOT NULL DEFAULT 'v1',  -- 스키마 버전 (재처리 판단용)
    extraction_quality VARCHAR(20),         -- 'high', 'medium', 'low' (자체 평가)

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,

    CONSTRAINT uq_report_analysis UNIQUE (report_id)
);

-- NOTE: idx_report_analysis_report_id 불필요 (UNIQUE가 이미 인덱스 생성)
CREATE INDEX idx_report_analysis_category ON report_analysis(report_category);
CREATE INDEX idx_report_analysis_schema_ver ON report_analysis(schema_version);

-- JSONB catch-all 인덱스 (containment 쿼리용)
-- 개별 필드 GIN 인덱스 대신 jsonb_path_ops 하나로 충분
CREATE INDEX idx_analysis_jsonb ON report_analysis
    USING GIN (analysis_data jsonb_path_ops);


-- 3. 종목-리포트 연결 테이블
-- 하나의 리포트가 여러 종목에 대해 언급할 수 있음 (특히 산업/매크로 리포트)
-- Agent가 "삼성전자 관련 리포트 전부"를 빠르게 찾기 위한 매핑
CREATE TABLE IF NOT EXISTS report_stock_mentions (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    stock_code      VARCHAR(20) NOT NULL,  -- stock_codes.code 참조
    company_name    VARCHAR(100),
    mention_type    VARCHAR(20) NOT NULL,  -- 'primary' (종목리포트 대상), 'implication' (산업리포트에서 언급), 'related' (맥락상 관련)
    impact          VARCHAR(20),           -- 'positive', 'negative', 'neutral', 'mixed'
    relevance_score NUMERIC(3,2),          -- 0.00~1.00, 관련도 (optional)

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_report_stock UNIQUE (report_id, stock_code)
);

CREATE INDEX idx_rsm_stock_code ON report_stock_mentions(stock_code);
CREATE INDEX idx_rsm_report_id ON report_stock_mentions(report_id);
CREATE INDEX idx_rsm_mention_type ON report_stock_mentions(mention_type);


-- 4. 섹터-리포트 연결 테이블
-- 매크로 리포트의 sector_implications, 산업 리포트의 sector 매핑
CREATE TABLE IF NOT EXISTS report_sector_mentions (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    sector          VARCHAR(100) NOT NULL,
    mention_type    VARCHAR(20) NOT NULL,  -- 'primary' (산업리포트 대상 섹터), 'implication' (매크로에서 영향받는 섹터)
    impact          VARCHAR(20),           -- 'positive', 'negative', 'neutral', 'mixed'

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_report_sector UNIQUE (report_id, sector)
);

CREATE INDEX idx_rscm_sector ON report_sector_mentions(sector);
CREATE INDEX idx_rscm_report_id ON report_sector_mentions(report_id);


-- 5. 키워드 태그 테이블
-- industry_keywords, macro keywords 등을 정규화하여 크로스 검색 지원
CREATE TABLE IF NOT EXISTS report_keywords (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    keyword         VARCHAR(100) NOT NULL,
    keyword_type    VARCHAR(30),           -- 'industry', 'macro', 'product', 'policy', 'event'

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_report_keyword UNIQUE (report_id, keyword)
);

CREATE INDEX idx_rk_keyword ON report_keywords(keyword);
CREATE INDEX idx_rk_keyword_type ON report_keywords(keyword_type);
CREATE INDEX idx_rk_report_id ON report_keywords(report_id);


-- 6. 분석 처리 로그
-- Layer 2 추출의 성공/실패/재처리 이력 추적
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    job_type        VARCHAR(30) NOT NULL,  -- 'markdown_convert', 'classify', 'extract_layer2'
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- 'pending', 'running', 'success', 'failed'
    error_message   TEXT,
    llm_model       VARCHAR(100),
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        NUMERIC(10,6),
    target_schema_version VARCHAR(20),     -- 재처리 시 대상 스키마 버전
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_aj_report_id ON analysis_jobs(report_id);
CREATE INDEX idx_aj_status ON analysis_jobs(status);
CREATE INDEX idx_aj_job_type ON analysis_jobs(job_type);


-- ============================================================
-- 기존 테이블 보완
-- ============================================================

-- reports 테이블에 분석 상태 추가
ALTER TABLE reports
    ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(20) DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS analysis_version VARCHAR(20),
    ADD COLUMN IF NOT EXISTS markdown_converted BOOLEAN DEFAULT FALSE;

-- stock_codes 테이블에 섹터 인덱스
CREATE INDEX IF NOT EXISTS idx_stock_codes_sector ON stock_codes(sector);
-- NOTE: idx_stock_codes_code 불필요 (code가 이미 PK)


-- ============================================================
-- 편의 뷰 (Agent 질의용)
-- ============================================================

-- 종목별 최신 리포트 + Layer 2 요약 뷰
CREATE OR REPLACE VIEW v_stock_latest_analysis AS
SELECT
    r.id AS report_id,
    r.stock_code,
    r.stock_name,
    r.broker,
    r.analyst,
    r.report_date,
    r.target_price,
    r.prev_target_price,
    r.opinion,
    r.prev_opinion,
    r.title,
    ra.report_category,
    ra.analysis_data,
    ra.schema_version,
    ra.extraction_quality,
    ra.created_at AS analyzed_at
FROM reports r
JOIN report_analysis ra ON ra.report_id = r.id
WHERE ra.report_category = 'stock'
ORDER BY r.report_date DESC;


-- 종목별 관련 리포트 전체 (종목 직접 + 산업/매크로에서 언급) 뷰
CREATE OR REPLACE VIEW v_stock_all_reports AS
SELECT
    rsm.stock_code,
    rsm.company_name,
    rsm.mention_type,
    rsm.impact,
    r.id AS report_id,
    r.broker,
    r.analyst,
    r.report_date,
    r.title,
    ra.report_category,
    ra.analysis_data,
    ra.extraction_quality
FROM report_stock_mentions rsm
JOIN reports r ON r.id = rsm.report_id
LEFT JOIN report_analysis ra ON ra.report_id = r.id
ORDER BY rsm.stock_code, r.report_date DESC;


-- 섹터별 관련 리포트 뷰
CREATE OR REPLACE VIEW v_sector_reports AS
SELECT
    rscm.sector,
    rscm.mention_type,
    rscm.impact,
    r.id AS report_id,
    r.broker,
    r.report_date,
    r.title,
    ra.report_category,
    ra.analysis_data
FROM report_sector_mentions rscm
JOIN reports r ON r.id = rscm.report_id
LEFT JOIN report_analysis ra ON ra.report_id = r.id
ORDER BY rscm.sector, r.report_date DESC;


-- ============================================================
-- pgvector 준비 (향후 확장, 지금은 주석)
-- ============================================================

-- CREATE EXTENSION IF NOT EXISTS vector;
--
-- ALTER TABLE report_analysis
--     ADD COLUMN IF NOT EXISTS embedding vector(1536);
--
-- CREATE INDEX idx_analysis_embedding ON report_analysis
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

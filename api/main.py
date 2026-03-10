"""FastAPI 앱 - 리포트 검색/조회 API."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import reports, stats
from config.settings import settings

app = FastAPI(
    title="Report Collector API",
    version="1.0.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",  # Vercel preview URLs 자동 허용
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reports.router, prefix="/api")
app.include_router(stats.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}

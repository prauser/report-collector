#!/bin/bash
# FastAPI 서버 실행 (포트 8000)
cd "$(dirname "$0")/.."
.venv/Scripts/uvicorn api.main:app --reload --port 8000

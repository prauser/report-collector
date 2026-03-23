"""key_data 전체 재추출 스크립트.

pdf_path가 있는 모든 리포트의 키데이터(broker, analyst, report_type 등)를 재추출.
"""
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import warnings

import structlog

warnings.filterwarnings("ignore", category=DeprecationWarning)
structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
)

from sqlalchemy import select, update as sa_update

from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import Report as ReportModel
from parser.key_data_extractor import extract_key_data

log = structlog.get_logger(__name__)

_TIMEOUT = 30


async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReportModel).where(ReportModel.pdf_path.isnot(None))
            .order_by(ReportModel.id)
        )
        reports = list(result.scalars().all())

    print(f"=== Key Data 재추출 ===")
    print(f"대상: {len(reports)}건")
    print()

    n_ok = n_fail = n_skip = 0

    for i, report in enumerate(reports, 1):
        abs_path = settings.pdf_base_path / report.pdf_path
        if not abs_path.exists():
            n_skip += 1
            continue

        try:
            key_data = await asyncio.wait_for(
                extract_key_data(abs_path, report_id=report.id, channel=report.source_channel or ""),
                timeout=_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.warning("timeout", report_id=report.id)
            n_fail += 1
            continue
        except Exception as e:
            log.warning("error", report_id=report.id, error=str(e))
            n_fail += 1
            continue

        if not key_data:
            n_skip += 1
            continue

        _t = lambda v, n: v[:n] if isinstance(v, str) and len(v) > n else v
        updates = {
            k: v for k, v in {
                "broker": _t(key_data.broker, 50),
                "analyst": _t(key_data.analyst or "Unknown", 100),
                "stock_name": _t(key_data.stock_name, 100),
                "stock_code": key_data.stock_code,
                "opinion": _t(key_data.opinion, 20),
                "target_price": key_data.target_price,
                "report_type": _t(key_data.report_type, 50),
            }.items() if v is not None
        }

        if updates:
            async with AsyncSessionLocal() as session:
                try:
                    await session.execute(
                        sa_update(ReportModel).where(ReportModel.id == report.id).values(**updates)
                    )
                    await session.commit()
                    n_ok += 1
                except Exception as e:
                    await session.rollback()
                    log.warning("update_failed", report_id=report.id, error=str(e))
                    n_fail += 1
        else:
            n_skip += 1

        if i % 50 == 0:
            print(f"  progress: {i}/{len(reports)} (ok={n_ok}, fail={n_fail}, skip={n_skip})")

    print(f"\n=== Done ===")
    print(f"  OK: {n_ok}")
    print(f"  Fail: {n_fail}")
    print(f"  Skip: {n_skip}")


if __name__ == "__main__":
    asyncio.run(main())

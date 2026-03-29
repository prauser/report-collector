"""s2a_done/new 리포트의 PDF 직접 다운로드 — backfill과 동일한 fallback 체인.

backfill이 S2a 분류까지 했지만 PDF를 못 받은 건들을
DB에서 직접 쿼리 → 다단계 fallback으로 PDF 확보:
  1) Telegram 첨부파일 (source_message_id)
  2) raw_text에서 t.me 링크 파싱 → resolve → 첨부/URL
  3) pdf_url로 HTTP 다운로드
  4) 모두 실패 → 실패 사유 + Telegram 링크 기록

사용법:
  python run_download_pending.py --dry-run           # 대상 확인
  python run_download_pending.py --limit 100         # 100건 처리
  python run_download_pending.py --statuses s2a_done,new --limit 5000
"""
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import re
import warnings
from datetime import datetime, timezone

import structlog

warnings.filterwarnings("ignore", category=DeprecationWarning)
structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
)

from sqlalchemy import select, func, update as sa_update
from telethon.tl.types import MessageMediaDocument

from collector.telegram_client import get_client
from config.settings import settings
from db.session import AsyncSessionLocal
from db.models import Report as ReportModel
from storage.pdf_archiver import download_telegram_document, download_pdf, resolve_tme_links
from storage.report_repo import update_pdf_info, mark_pdf_failed, update_pipeline_status

log = structlog.get_logger(__name__)

_CONCURRENCY = 5
_TME_PATTERN = re.compile(r"https?://(?:t\.me|telegram\.me)/([a-zA-Z_]\w+)/(\d+)")


def _has_pdf_attachment(message) -> bool:
    """메시지에 PDF 첨부파일이 있는지 확인."""
    media = getattr(message, "media", None)
    if not isinstance(media, MessageMediaDocument):
        return False
    doc = media.document
    return "pdf" in getattr(doc, "mime_type", "")


def _extract_tme_links(raw_text: str | None) -> list[str]:
    """raw_text에서 t.me 메시지 링크 추출."""
    if not raw_text:
        return []
    return [m.group(0) for m in _TME_PATTERN.finditer(raw_text)]


def _telegram_link(channel: str | None, msg_id: int | None) -> str | None:
    """수작업 확인용 Telegram 링크 생성."""
    if not channel or not msg_id:
        return None
    ch = channel.lstrip("@")
    return f"https://t.me/{ch}/{msg_id}"


async def _update_success(report_id: int, rel_path: str, size_kb: int):
    """PDF 다운로드 성공 → DB 업데이트."""
    async with AsyncSessionLocal() as session:
        await update_pdf_info(session, report_id, rel_path, size_kb, None)
        await update_pipeline_status(session, report_id, "pdf_done")
        await session.commit()


async def _update_fail(report_id: int, reason: str):
    """PDF 다운로드 실패 → DB 업데이트."""
    async with AsyncSessionLocal() as session:
        await mark_pdf_failed(session, report_id, reason)
        await session.commit()


async def _process_report(client, report: ReportModel) -> tuple[str, str | None]:
    """단일 리포트의 PDF 다운로드. 전체 fallback 체인 시도.

    Returns: (result_code, fail_detail)
      result_code: 'telegram_ok' | 'tme_ok' | 'url_ok' | 'no_source' | 'fail'
      fail_detail: 실패 시 상세 사유
    """
    attempts = []  # 시도 기록

    # --- 1단계: Telegram 첨부파일 직접 다운로드 ---
    if report.source_message_id and report.source_channel:
        try:
            message = await client.get_messages(
                report.source_channel,
                ids=report.source_message_id,
            )
        except Exception as e:
            attempts.append(f"telegram_get_msg: {e}")
            message = None

        if message and _has_pdf_attachment(message):
            rel_path, size_kb = await download_telegram_document(client, message, report)
            if rel_path:
                await _update_success(report.id, rel_path, size_kb)
                return "telegram_ok", None
            attempts.append("telegram_download: failed")
        elif message:
            attempts.append("telegram: no_pdf_attachment")
        else:
            attempts.append("telegram: message_not_found")

    # --- 2단계: raw_text에서 t.me 링크 → resolve ---
    tme_links = _extract_tme_links(report.raw_text)
    if tme_links and not report.pdf_url:
        try:
            tme_url, tme_msg = await resolve_tme_links(client, tme_links)
            if tme_url:
                # URL 발견 → pdf_url에 저장하고 3단계에서 다운로드
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        sa_update(ReportModel).where(ReportModel.id == report.id)
                        .values(pdf_url=tme_url)
                    )
                    await session.commit()
                report.pdf_url = tme_url
            elif tme_msg:
                # t.me가 document를 가리킴 → 직접 다운로드
                rel_path, size_kb = await download_telegram_document(client, tme_msg, report)
                if rel_path:
                    await _update_success(report.id, rel_path, size_kb)
                    return "tme_ok", None
                attempts.append("tme_document: download_failed")
            else:
                attempts.append(f"tme_resolve: no_result ({len(tme_links)} links)")
        except Exception as e:
            attempts.append(f"tme_resolve: {e}")

    # --- 3단계: pdf_url로 HTTP 다운로드 ---
    if report.pdf_url:
        rel_path, size_kb, fail_reason = await download_pdf(report)
        if rel_path:
            await _update_success(report.id, rel_path, size_kb)
            return "url_ok", None
        attempts.append(f"url_download: {fail_reason}")

    # --- 모두 실패 ---
    if not report.source_message_id and not report.pdf_url and not tme_links:
        await _update_fail(report.id, "no_source")
        return "no_source", None

    fail_detail = " | ".join(attempts) if attempts else "unknown"
    await _update_fail(report.id, fail_detail[:500])

    return "fail", fail_detail


async def main(args: argparse.Namespace):
    statuses = args.statuses.split(",")
    limit = args.limit

    # 상태별 카운트
    async with AsyncSessionLocal() as s:
        for st in statuses:
            cnt = await s.scalar(
                select(func.count()).select_from(ReportModel).where(
                    ReportModel.pipeline_status == st,
                    ReportModel.pdf_path.is_(None),
                )
            )
            print(f"  {st}: {cnt}건 (PDF 미다운로드)")

    async with AsyncSessionLocal() as s:
        query = (
            select(ReportModel)
            .where(
                ReportModel.pipeline_status.in_(statuses),
                ReportModel.pdf_path.is_(None),
            )
            .order_by(ReportModel.id)
            .limit(limit)
        )
        if args.channel:
            query = query.where(ReportModel.source_channel == args.channel)
        reports = list((await s.execute(query)).scalars().all())

    print(f"\n=== PDF 다운로드 ({len(reports)}건, 동시 {_CONCURRENCY}) ===")
    print(f"대상: {statuses}, Limit: {limit}")
    if args.channel:
        print(f"Channel: {args.channel}")

    if args.dry_run:
        for r in reports[:30]:
            tg_link = _telegram_link(r.source_channel, r.source_message_id) or "-"
            tme = _extract_tme_links(r.raw_text)
            print(f"  [{r.id}] {r.source_channel} msg={r.source_message_id or '-'} "
                  f"url={('Y' if r.pdf_url else '-')} tme={len(tme)} | {(r.title or '')[:40]}")
        if len(reports) > 30:
            print(f"  ... 외 {len(reports) - 30}건")
        print(f"\n총 {len(reports)}건 (--dry-run)")
        return

    if not reports:
        print("처리할 건이 없습니다.")
        return

    client = get_client()
    await client.start()

    results = {}
    failed_reports = []  # 실패 건 상세 기록
    done = 0
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _worker(report):
        nonlocal done
        async with sem:
            code, detail = await _process_report(client, report)
            done += 1
            results[code] = results.get(code, 0) + 1

            if code == "fail":
                tg_link = _telegram_link(report.source_channel, report.source_message_id)
                failed_reports.append({
                    "id": report.id,
                    "channel": report.source_channel,
                    "msg_id": report.source_message_id,
                    "telegram_link": tg_link,
                    "detail": detail,
                })

            if done % 100 == 0:
                log.info("download_progress", progress=f"{done}/{len(reports)}", **results)

    tasks = [_worker(r) for r in reports]
    await asyncio.gather(*tasks)

    await client.disconnect()

    # 결과 요약
    print(f"\n=== 완료 ({done}/{len(reports)}) ===")
    for k, v in sorted(results.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k}: {v}")

    # 실패 건 상세 출력
    if failed_reports:
        print(f"\n=== 실패 상세 ({len(failed_reports)}건) ===")
        for f in failed_reports[:50]:
            print(f"  [{f['id']}] {f['telegram_link'] or '-'}")
            print(f"    사유: {f['detail']}")
        if len(failed_reports) > 50:
            print(f"  ... 외 {len(failed_reports) - 50}건")

        # CSV로도 저장
        csv_path = "download_failures.csv"
        with open(csv_path, "w", encoding="utf-8") as fp:
            fp.write("report_id,channel,msg_id,telegram_link,detail\n")
            for f in failed_reports:
                detail_escaped = (f['detail'] or '').replace('"', '""')
                fp.write(f"{f['id']},{f['channel']},{f['msg_id'] or ''},"
                         f"{f['telegram_link'] or ''},\"{detail_escaped}\"\n")
        print(f"\n실패 목록 저장: {csv_path}")


def cli():
    parser = argparse.ArgumentParser(description="s2a_done/new 리포트 PDF 직접 다운로드")
    parser.add_argument("--statuses", default="s2a_done",
                        help="대상 status (쉼표 구분, 기본: s2a_done)")
    parser.add_argument("--limit", type=int, default=1000,
                        help="처리 건수 (기본: 1000)")
    parser.add_argument("--channel", default=None,
                        help="특정 채널만")
    parser.add_argument("--dry-run", action="store_true",
                        help="대상만 확인")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    asyncio.run(main(args))

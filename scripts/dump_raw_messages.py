"""텔레그램 메시지 raw 구조 덤프 - 미디어/버튼/파일/링크 전부 출력.

사용법:
  PYTHONPATH=. .venv/Scripts/python scripts/dump_raw_messages.py
  PYTHONPATH=. .venv/Scripts/python scripts/dump_raw_messages.py --channels sunstudy1004 --limit 10
"""
import asyncio
import argparse
from telethon.tl.types import (
    Message, MessageMediaDocument, MessageMediaPhoto,
    MessageMediaWebPage, DocumentAttributeFilename,
)
from telethon.tl.custom import Button

from collector.telegram_client import get_client

SEP = "━" * 80


def _media_type(message: Message) -> str:
    media = message.media
    if media is None:
        return "없음"
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        # 파일명 추출
        fname = None
        for attr in (doc.attributes or []):
            if isinstance(attr, DocumentAttributeFilename):
                fname = attr.file_name
                break
        mime = getattr(doc, "mime_type", "?")
        size_kb = getattr(doc, "size", 0) // 1024
        return f"Document  mime={mime}  파일명={fname}  크기={size_kb}KB"
    if isinstance(media, MessageMediaPhoto):
        return "Photo(이미지)"
    if isinstance(media, MessageMediaWebPage):
        wp = media.webpage
        url = getattr(wp, "url", "")
        title = getattr(wp, "title", "")
        return f"WebPage  url={url!r}  title={title!r}"
    return type(media).__name__


def _buttons(message: Message) -> list[str]:
    """인라인 버튼에서 URL 추출."""
    rows = getattr(message, "reply_markup", None)
    if rows is None:
        return []
    result = []
    for row in getattr(rows, "rows", []):
        for btn in getattr(row, "buttons", []):
            url = getattr(btn, "url", None)
            text = getattr(btn, "text", "")
            if url:
                result.append(f"[버튼] {text!r} → {url}")
    return result


def _entities_urls(message: Message) -> list[str]:
    """메시지 entities(마크다운 링크 등)에서 URL 추출."""
    text = message.text or message.message or ""
    entities = message.entities or []
    urls = []
    for ent in entities:
        ent_type = type(ent).__name__
        if ent_type == "MessageEntityUrl":
            start = ent.offset
            end = ent.offset + ent.length
            urls.append(f"[URL entity] {text[start:end]}")
        elif ent_type == "MessageEntityTextUrl":
            urls.append(f"[TextURL] url={ent.url!r}")
    return urls


async def dump_channel(client, channel: str, limit: int) -> None:
    if not channel.startswith("@") and not channel.startswith("+"):
        channel = f"@{channel}"

    print(f"\n{'=' * 80}")
    print(f"채널: {channel}  (최근 {limit}개)")
    print("=" * 80)

    async for message in client.iter_messages(channel, limit=limit, reverse=False):
        if not isinstance(message, Message):
            continue

        msg_id = message.id
        date_str = message.date.strftime("%Y-%m-%d %H:%M")
        text = message.text or message.message or ""
        media_info = _media_type(message)
        buttons = _buttons(message)
        ent_urls = _entities_urls(message)

        print(f"\n{SEP}")
        print(f"ID={msg_id}  날짜={date_str}")
        print(f"  미디어   : {media_info}")

        if text:
            preview = text.replace("\n", " │ ")[:300]
            print(f"  텍스트   : {preview!r}")
            print(f"  텍스트길이: {len(text)}자")
        else:
            print(f"  텍스트   : (없음)")

        if ent_urls:
            for u in ent_urls:
                print(f"  {u}")

        if buttons:
            for b in buttons:
                print(f"  {b}")

        # PDF 흔적 체크
        has_pdf = False
        all_text = text.lower()
        if ".pdf" in all_text:
            has_pdf = True
        if isinstance(message.media, MessageMediaDocument):
            mime = getattr(message.media.document, "mime_type", "")
            if "pdf" in mime:
                has_pdf = True
        for b in buttons:
            if ".pdf" in b.lower() or "pdf" in b.lower():
                has_pdf = True

        if has_pdf:
            print(f"  ✓ PDF 감지됨")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--channels", nargs="+",
        default=["sunstudy1004", "report_figure_by_offset"],
    )
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    client = get_client()
    await client.start()
    try:
        for ch in args.channels:
            await dump_channel(client, ch, args.limit)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

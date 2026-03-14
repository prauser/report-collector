"""채널 메시지 진단 스크립트 - LLM 호출 없이 메시지 파싱 결과 확인.

사용법:
  .venv/Scripts/python scripts/inspect_messages.py
  .venv/Scripts/python scripts/inspect_messages.py --channels sunstudy1004 report_figure_by_offset --limit 30
"""
import asyncio
import argparse
from telethon.tl.types import Message

from collector.telegram_client import get_client
from parser.registry import parse_message


SEP = "─" * 80

TRUNCATION_HINTS = [
    "(Continuing", "continued", "계속", "이하 생략",
    "...", "…",  # trailing ellipsis가 메시지 끝에 있으면 의심
]


def _truncation_flag(text: str) -> str:
    """메시지 마지막 줄에 잘린 흔적이 있는지 체크."""
    tail = text.rstrip()[-50:]
    if tail.endswith(("...", "…")):
        return " ⚠ [끝 말줄임]"
    if len(text) > 3500:
        return f" ⚠ [매우 긴 메시지: {len(text)}자]"
    return ""


def _quality_label(text: str, parsed) -> str:
    parts = []
    if parsed is None:
        return "[SKIP - parse_message→None]"
    if len(text) < 30:
        parts.append("⚠단문")
    if not parsed.broker:
        parts.append("broker=None")
    if not parsed.title or len(parsed.title) < 5:
        parts.append("title짧음")
    if not parsed.pdf_url:
        parts.append("url없음")
    label = ", ".join(parts) if parts else "OK"
    return f"[{label}]"


async def inspect_channel(client, channel: str, limit: int) -> None:
    # @ prefix 정규화
    if not channel.startswith("@") and not channel.startswith("+"):
        channel = f"@{channel}"

    print(f"\n{'=' * 80}")
    print(f"채널: {channel}  (최근 {limit}개 메시지, 역순)")
    print("=" * 80)

    n_total = n_skipped_no_text = n_skipped_continuing = n_passed = 0
    issues = []

    async for message in client.iter_messages(channel, limit=limit, reverse=False):
        n_total += 1
        msg_id = message.id

        if not isinstance(message, Message) or not message.text:
            n_skipped_no_text += 1
            print(f"  #{msg_id:>8}  [미디어/비텍스트 - 스킵]")
            continue

        text = message.text
        date_str = message.date.strftime("%Y-%m-%d %H:%M")

        # (Continuing...) 체크
        is_continuing = text.strip().startswith("(Continuing")

        parsed = parse_message(text, channel, message_id=msg_id)
        quality = _quality_label(text, parsed)
        trunc = _truncation_flag(text)
        cont_flag = " ⚠ [이어짐 메시지]" if is_continuing else ""

        if parsed is None or is_continuing:
            n_skipped_continuing += 1
            status = "SKIP"
        else:
            n_passed += 1
            status = "→LLM"

        # 한 줄 요약
        preview = text.replace("\n", " ")[:100]
        print(f"\n  #{msg_id:>8}  [{date_str}]  {status}{cont_flag}{trunc}")
        print(f"           {quality}")
        print(f"           preview: {preview!r}")

        if parsed and not is_continuing:
            print(f"           broker={parsed.broker!r}  stock={parsed.stock_name!r}({parsed.stock_code})  url={bool(parsed.pdf_url)}")
            raw_len = len(parsed.raw_text)
            print(f"           raw_text 길이: {raw_len}자")

            # 이상 징후 수집
            if raw_len < 20:
                issues.append(f"  #{msg_id}: raw_text 너무 짧음 ({raw_len}자) → {preview!r}")
            if not parsed.broker and not parsed.stock_name and not parsed.pdf_url:
                issues.append(f"  #{msg_id}: broker/stock/url 모두 없음 → {preview!r}")

    print(f"\n{SEP}")
    print(f"집계: 전체={n_total}  텍스트없음={n_skipped_no_text}  스킵={n_skipped_continuing}  LLM전달={n_passed}")
    if issues:
        print(f"\n주요 이상 메시지 ({len(issues)}건):")
        for iss in issues:
            print(iss)


async def main() -> None:
    parser = argparse.ArgumentParser(description="채널 메시지 진단")
    parser.add_argument(
        "--channels",
        nargs="+",
        default=["sunstudy1004", "report_figure_by_offset"],
        help="채널명 (@ 생략 가능)",
    )
    parser.add_argument("--limit", type=int, default=30, help="채널당 메시지 수")
    args = parser.parse_args()

    client = get_client()
    await client.start()
    try:
        for ch in args.channels:
            await inspect_channel(client, ch, args.limit)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

"""Telegram 인증 Step 1 — 폰번호로 SMS 코드 요청."""
import asyncio
import sys
import json
from pathlib import Path
from telethon import TelegramClient
from config.settings import settings

STATE_FILE = Path(__file__).parent / ".auth_state.json"


async def step1(phone: str):
    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    result = await client.send_code_request(phone)

    STATE_FILE.write_text(json.dumps({
        "phone": phone,
        "phone_code_hash": result.phone_code_hash,
    }))

    print(f"SMS 코드 발송 완료 → {phone}")
    print("코드 받으면: python -m scripts.auth_step2 <코드>")
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.auth_step1 +821012345678")
        sys.exit(1)
    asyncio.run(step1(sys.argv[1]))

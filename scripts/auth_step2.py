"""Telegram 인증 Step 2 — SMS 코드로 로그인 + 세션 저장."""
import asyncio
import sys
import json
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from config.settings import settings

STATE_FILE = Path(__file__).parent / ".auth_state.json"


async def step2(code: str):
    if not STATE_FILE.exists():
        print("먼저 step1을 실행하세요: python -m scripts.auth_step1 <폰번호>")
        sys.exit(1)

    state = json.loads(STATE_FILE.read_text())
    phone = state["phone"]
    phone_code_hash = state["phone_code_hash"]

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()

    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        print("2FA 비밀번호가 필요합니다. python -m scripts.auth_step2 <코드> <2FA비밀번호>")
        if len(sys.argv) >= 3:
            await client.sign_in(password=sys.argv[2])
        else:
            await client.disconnect()
            sys.exit(1)

    me = await client.get_me()
    print(f"인증 완료! 로그인된 계정: {me.first_name} (@{me.username})")
    print(f"세션 파일 저장됨: {settings.telegram_session_name}.session")

    STATE_FILE.unlink(missing_ok=True)
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.auth_step2 <SMS코드>")
        sys.exit(1)
    asyncio.run(step2(sys.argv[1]))

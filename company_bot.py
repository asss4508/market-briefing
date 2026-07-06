#!/usr/bin/env python3
"""
기업분석 텔레그램 봇 - Telethon 기반 즉시 감지
"X 기업분석" 메시지 수신 즉시 분석 실행
"""

import asyncio
import os
import re
import subprocess
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID    = int(os.environ["TELEGRAM_API_ID"])
API_HASH  = os.environ["TELEGRAM_API_HASH"]
SESSION   = os.environ["TELEGRAM_SESSION"]
CHAT_ID   = int(os.environ["TELEGRAM_CHAT_ID"])

# 실행 중인 분석 태스크 추적
running_tasks = set()


async def run_analysis(company):
    """company_analysis.py를 별도 스레드에서 실행 (이벤트 루프 차단 방지)"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: subprocess.run(["python", "company_analysis.py", company], check=False)
    )


async def main():
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"봇 시작: {me.first_name} | 채널: {CHAT_ID}")

    @client.on(events.NewMessage())
    async def handler(event):
        # 대상 채널/그룹 필터
        # Telegram 채널은 -100을 붙인 음수 ID로 오는 경우도 있음
        cid = event.chat_id
        if cid != CHAT_ID and abs(cid) != abs(CHAT_ID):
            return

        text = (event.message.text or "").strip()

        # "X 기업분석" 패턴 (앞에 기업명 1~30자)
        m = re.match(r'^(.{1,30}?)\s*기업분석\s*$', text)
        if not m:
            return

        company = m.group(1).strip()
        print(f"[감지] {company} 기업분석 요청")

        # 즉시 접수 메시지 전송
        await client.send_message(
            CHAT_ID,
            f"⏳ <b>{company}</b> 기업분석 생성 중... (약 1분 소요)",
            parse_mode="html"
        )

        # 비동기 분석 실행
        task = asyncio.create_task(run_analysis(company))
        running_tasks.add(task)
        task.add_done_callback(running_tasks.discard)

    print("메시지 대기 중... (Ctrl+C로 종료)")

    # 5시간 30분 후 자동 종료 (GitHub Actions 6시간 제한 여유)
    await asyncio.sleep(5.5 * 3600)

    # 실행 중인 분석 완료 대기
    if running_tasks:
        print(f"분석 태스크 {len(running_tasks)}개 완료 대기 중...")
        await asyncio.gather(*running_tasks, return_exceptions=True)

    await client.disconnect()
    print("봇 종료")


if __name__ == "__main__":
    asyncio.run(main())

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


def log(msg):
    """flush=True로 GitHub Actions 실시간 로그 출력"""
    print(msg, flush=True)


def chat_matches(received_id, target_id):
    """Telegram 채널 ID는 -100XXXXXXXXXX 형식.
    환경변수 저장 형식(양수/음수/-100 접두사 유무)에 무관하게 비교.
    예) received=-1001234567890, target=1234567890 → True
    """
    if received_id == target_id:
        return True
    r = str(abs(received_id))
    t = str(abs(target_id))
    # 두 ID 중 하나가 다른 하나의 끝 부분과 일치하면 동일 채널
    return r == t or r.endswith(t) or t.endswith(r)


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
    log(f"봇 시작: {me.first_name} | 설정 CHAT_ID={CHAT_ID}")

    @client.on(events.NewMessage())
    async def handler(event):
        cid = event.chat_id
        text = (event.message.text or "").strip()

        # 모든 수신 메시지 로깅 (디버깅용)
        if text:
            log(f"[수신] chat_id={cid} | '{text[:60]}'")

        # "X 기업분석" 패턴 (앞에 기업명 1~30자) — CHAT_ID 필터 없이 모든 채팅 허용
        m = re.match(r'^(.{1,30}?)\s*기업분석\s*$', text)
        if not m:
            return

        company = m.group(1).strip()
        log(f"[감지] chat_id={cid} | '{company}' 기업분석 요청")

        # 즉시 접수 메시지 전송 (event.respond: 동일 채팅에 자동 전송)
        await event.respond(
            f"⏳ <b>{company}</b> 기업분석 생성 중... (약 1분 소요)",
            parse_mode="html"
        )

        # 비동기 분석 실행
        task = asyncio.create_task(run_analysis(company))
        running_tasks.add(task)
        task.add_done_callback(running_tasks.discard)

    log("메시지 대기 중... (Ctrl+C로 종료)")

    # 5시간 55분 후 자동 종료 (GitHub Actions 6시간 제한 여유, 다음 스케줄과 이어지도록)
    await asyncio.sleep(5 * 3600 + 55 * 60)

    # 실행 중인 분석 완료 대기
    if running_tasks:
        log(f"분석 태스크 {len(running_tasks)}개 완료 대기 중...")
        await asyncio.gather(*running_tasks, return_exceptions=True)

    await client.disconnect()
    log("봇 종료")


if __name__ == "__main__":
    asyncio.run(main())

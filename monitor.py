import asyncio
import html
import os
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
import anthropic
import requests

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ["TELEGRAM_SESSION"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CHANNELS = [
    "bornlupin", "KOREASTOCK11", "hogniel", "psychotherapy101",
    "mootda", "comvestment", "growthresearch", "gaoshoukorea",
    "moneybottle", "getfeed", "YeouidoStory2", "BRILLER_Research",
    "triple_stock", "cahier_de_market", "kkkontemp", "Jstockclass",
    "insangnism", "ehdwl"
]

KST = timezone(timedelta(hours=9))


async def collect_messages():
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.start()

    now = datetime.now(KST)
    one_hour_ago = now - timedelta(hours=1)
    all_messages = []

    for channel in CHANNELS:
        try:
            entity = await client.get_entity(channel)
            messages = await client.get_messages(entity, limit=30)
            for msg in messages:
                if not msg.text:
                    continue
                msg_time = msg.date.replace(tzinfo=timezone.utc).astimezone(KST)
                if msg_time < one_hour_ago:
                    continue
                all_messages.append({
                    "channel": channel,
                    "text": msg.text[:500],
                    "views": msg.views or 0,
                    "date": msg_time,
                    "link": f"https://t.me/{channel}/{msg.id}"
                })
        except Exception as e:
            print(f"[{channel}] 오류: {e}")

    await client.disconnect()
    return all_messages


def rank_with_claude(messages):
    messages_text = "\n\n".join([
        f"[{i+1}] 채널: {m['channel']} | 조회수: {m['views']:,}\n내용: {m['text']}"
        for i, m in enumerate(messages[:40])
    ])

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"""아래는 한국 주식/경제 텔레그램 채널의 최근 1시간 메시지입니다.
국내 주식시장에 실질적 영향을 줄 수 있는 내용과 조회수가 높은 것을 기준으로
가장 중요한 3개의 번호만 콤마로 구분해 답하세요. (예: 3,7,12)

{messages_text}"""
        }]
    )

    raw = response.content[0].text.strip()
    indices = []
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            idx = int(x) - 1
            if 0 <= idx < len(messages):
                indices.append(idx)
    return [messages[i] for i in indices[:3]]


def build_message(top3):
    now = datetime.now(KST)
    medals = ["🥇", "🥈", "🥉"]

    lines = [f"📊 시장 핵심 브리핑 | {now.strftime('%m/%d %H:%M')}", "─" * 22]

    for i, msg in enumerate(top3):
        summary = html.escape(msg["text"][:200].replace("\n", " "))
        lines.append(
            f"{medals[i]} <b>[{html.escape(msg['channel'])}]</b>\n"
            f"{summary}\n"
            f"👁 {msg['views']:,}  |  <a href=\"{msg['link']}\">원문 보기</a>"
        )
        if i < len(top3) - 1:
            lines.append("")

    lines.append("─" * 22)
    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    res = requests.post(url, json=payload, timeout=10)
    res.raise_for_status()
    print("전송 완료")


async def main():
    print("채널 메시지 수집 중...")
    messages = await collect_messages()
    print(f"수집된 메시지: {len(messages)}개")

    if not messages:
        print("수집된 메시지 없음 — 종료")
        return

    messages.sort(key=lambda x: x["views"], reverse=True)

    if len(messages) >= 3:
        print("AI 중요도 판단 중...")
        top3 = rank_with_claude(messages)
        if len(top3) < 3:
            top3 = messages[:3]
    else:
        top3 = messages

    msg = build_message(top3)
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    asyncio.run(main())

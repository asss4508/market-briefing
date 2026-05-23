import asyncio
import html
import os
import re
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
MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]


def is_market_hours():
    now = datetime.now(KST)
    return 9 <= now.hour <= 19


def clean_text(text):
    text = re.sub(r'[☎️📞]?\s*(?:\+82[-.]?)?\d{2,4}[-.]?\d{3,4}[-.]?\d{4}', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


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
                    "channel_title": getattr(entity, "title", channel),
                    "text": msg.text[:600],
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
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": f"""아래는 한국 주식/경제 텔레그램 채널의 최근 1시간 메시지입니다.
국내 주식시장에 실질적 영향을 줄 수 있는 내용과 조회수가 높은 것 기준으로 중요한 5개를 선택하세요.
각 항목에 대해 제목, 핵심 키워드(2~3개), 4~5줄 요약 내용을 작성하세요.
전화번호, URL은 제외하고 핵심 내용만 간결하게 작성하세요.
내용 각 줄은 반드시 "- "으로 시작하고, 마지막 줄은 짧은 결론 단어(예: 수주 확대, 부각, 주목)로만 마감하세요.

반드시 아래 형식으로만 답하세요:
전체키워드: 조선, 암모니아, KOSPI200

[1]
원본번호: 3
제목: HD한국조선해양 암모니아 운반선 수주
키워드: 조선, 수주, 암모니아
내용:
- HD한국조선해양, 유럽 선주와 초대형 암모니아 운반선 6척 계약
- 수주 금액 약 1조782억원 규모
- 친환경 연료 수요 증가로 VLAC 시장 본격 확대
- 수주 확대

[2]
원본번호: 7
...

{messages_text}"""
        }]
    )

    raw = response.content[0].text.strip()

    # 전체 키워드 파싱
    global_keywords = ""
    kw_match = re.search(r'전체키워드\s*:\s*(.+)', raw)
    if kw_match:
        global_keywords = kw_match.group(1).strip()

    # 각 항목 파싱
    items = []
    blocks = re.split(r'\[(\d+)\]', raw)
    for j in range(1, len(blocks), 2):
        block = blocks[j + 1] if j + 1 < len(blocks) else ""

        orig_match = re.search(r'원본번호\s*:\s*(\d+)', block)
        title_match = re.search(r'제목\s*:\s*(.+)', block)
        item_kw_match = re.search(r'키워드\s*:\s*(.+)', block)
        content_match = re.search(r'내용\s*:\s*([\s\S]+?)(?=\n\[|\Z)', block)

        if not orig_match:
            continue

        orig_idx = int(orig_match.group(1)) - 1
        if not (0 <= orig_idx < len(messages)):
            continue

        items.append({
            "msg": messages[orig_idx],
            "title": title_match.group(1).strip() if title_match else "",
            "keywords": item_kw_match.group(1).strip() if item_kw_match else "",
            "summary": content_match.group(1).strip() if content_match else "",
        })

    return items[:5], global_keywords


def escape_bold(text):
    parts = re.split(r'\*\*', text)
    result = []
    for i, part in enumerate(parts):
        escaped = html.escape(part)
        result.append(f'<b>{escaped}</b>' if i % 2 == 1 else escaped)
    return ''.join(result)


def build_message(items, global_keywords):
    now = datetime.now(KST)

    lines = [f"📊 <b>시장 핵심 브리핑</b>  {now.strftime('%m/%d %H:%M')}"]

    if global_keywords:
        lines.append(f"핵심 키워드 : {html.escape(global_keywords)}")

    lines.append("─" * 22)

    for i, item in enumerate(items):
        msg = item["msg"]
        ch_name = html.escape(msg.get("channel_title", msg["channel"]))
        title = html.escape(item["title"]) if item["title"] else html.escape(msg["text"][:50])
        keywords = html.escape(item["keywords"])
        summary = escape_bold(clean_text(item["summary"]))

        lines.append(
            f"\n<b>{MEDALS[i]} [{ch_name}]</b>\n"
            f"\n※ 제목 : <b>{title}</b>\n"
            f"🔍 키워드 : {keywords}\n"
            f"\n{summary}\n"
            f"\n▷ 조회 {msg['views']:,}  ·  <a href=\"{msg['link']}\">원문 보기</a>"
        )
        if i < len(items) - 1:
            lines.append("")

    lines.append("\n" + "─" * 22)
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
    if not is_market_hours():
        print(f"장외 시간 ({datetime.now(KST).strftime('%H:%M')} KST) — 종료")
        return

    print("채널 메시지 수집 중...")
    messages = await collect_messages()
    print(f"수집된 메시지: {len(messages)}개")

    if not messages:
        print("수집된 메시지 없음 — 종료")
        return

    messages.sort(key=lambda x: x["views"], reverse=True)

    print("AI 중요도 판단 중...")
    items, global_keywords = rank_with_claude(messages)

    if not items:
        print("AI 선별 실패, 조회수 상위 5개 사용")
        items = [{"msg": m, "title": "", "keywords": "", "summary": m["text"][:200]} for m in messages[:5]]
        global_keywords = ""

    msg = build_message(items, global_keywords)
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    asyncio.run(main())

"""
텔레그램 온디맨드 브리핑 리스너
브리핑해줘 메시지 감지 시 market_report.py 실행
"""
import os
import sys
import subprocess
import requests
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TRIGGER_KEYWORDS = ["브리핑해줘", "브리핑 해줘", "briefing"]
LOOKBACK_SECONDS = 360


def get_updates():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, params={"limit": 100}, timeout=10)
        return res.json().get("result", [])
    except Exception as e:
        print(f"[getUpdates] 오류: {e}")
        return []


def check_command():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LOOKBACK_SECONDS)
    updates = get_updates()

    for update in updates:
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        msg_time = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc)
        if msg_time < cutoff:
            continue
        text = (msg.get("text") or "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != CHAT_ID:
            continue
        if any(kw in text for kw in TRIGGER_KEYWORDS):
            print(f"명령 감지: {msg_time.strftime(\"%H:%M:%S\")} UTC")
            return True
    return False


if __name__ == "__main__":
    check_only = "--check-only" in sys.argv
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    print(f"텔레그램 명령 확인 중... ({now_str})")

    if check_command():
        print("TRIGGERED")
        if not check_only:
            print("리포트 생성 시작...")
            subprocess.run(["python", "market_report.py"], check=True)
    else:
        print("NO_COMMAND - 종료")

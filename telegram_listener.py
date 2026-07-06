"""
텔레그램 명령 감지 스크립트

--check-only      : 마켓 브리핑 트리거 여부만 확인
--check-analysis  : 기업분석 명령 여부만 확인 (COMPANY:종목명 출력)
인수 없음         : 마켓 브리핑 트리거 시 market_report.py 실행
"""
import os
import re
import sys
import subprocess
import requests
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BRIEFING_KEYWORDS = ["브리핑", "시장요약", "briefing"]
LOOKBACK_SECONDS = 360


def get_updates():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, params={"limit": 100}, timeout=10)
        return res.json().get("result", [])
    except Exception as e:
        print(f"[getUpdates] 오류: {e}")
        return []


def recent_messages():
    """최근 LOOKBACK_SECONDS 내 자신 채널 메시지 반환"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LOOKBACK_SECONDS)
    for update in get_updates():
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        if str(msg.get("chat", {}).get("id", "")) != CHAT_ID:
            continue
        msg_time = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc)
        if msg_time < cutoff:
            continue
        yield msg.get("text", "").strip(), msg_time


def check_briefing():
    """마켓 브리핑 트리거 키워드 감지"""
    for text, t in recent_messages():
        if any(kw in text for kw in BRIEFING_KEYWORDS):
            print(f"브리핑 요청 감지: {t.strftime('%H:%M:%S')} UTC")
            return True
    return False


def check_analysis():
    """'X 기업분석' 패턴 감지 → 종목명 반환"""
    for text, t in recent_messages():
        m = re.match(r'^(.+?)\s*기업분석\s*$', text)
        if m:
            company = m.group(1).strip()
            print(f"기업분석 요청 감지: {company} ({t.strftime('%H:%M:%S')} UTC)")
            return company
    return None


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if mode == "--check-analysis":
        print(f"기업분석 명령 확인 중... ({now_str})")
        company = check_analysis()
        if company:
            print(f"COMPANY:{company}")
        else:
            print("NO_ANALYSIS")

    elif mode == "--check-only":
        print(f"브리핑 명령 확인 중... ({now_str})")
        if check_briefing():
            print("TRIGGERED")
        else:
            print("NO_COMMAND")

    else:
        print(f"텔레그램 명령 확인 중... ({now_str})")
        if check_briefing():
            print("TRIGGERED")
            print("리포트 생성 시작...")
            subprocess.run(["python", "market_report.py"], check=True)
        else:
            print("NO_COMMAND - 종료")

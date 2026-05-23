import html as html_lib
import os
import re
import requests
import anthropic
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://finance.naver.com/",
}
KST = timezone(timedelta(hours=9))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


# ─── 데이터 수집 ───────────────────────────────────────────────

def get_korean_indices():
    result = {}
    for code in ["KOSPI", "KOSDAQ"]:
        try:
            url = f"https://finance.naver.com/sise/sise_index.nhn?code={code}"
            res = requests.get(url, headers=HEADERS, timeout=10)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
            val = soup.select_one("#now_value")
            chg = soup.select_one("#change_value")
            pct = soup.select_one("#change_rate")
            if val:
                result[code] = {
                    "value": val.text.strip(),
                    "change": chg.text.strip() if chg else "",
                    "rate": pct.text.strip() if pct else "",
                }
        except Exception as e:
            print(f"[{code}] 오류: {e}")
    return result


def get_market_indicators():
    data = {}
    try:
        url = "https://finance.naver.com/marketindex/"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")

        # 환율
        for item in soup.select(".exchange_table li, #exchange_table li, .market-index-item"):
            name_tag = item.select_one(".txt_name, .item-name, h3, .blind")
            val_tag = item.select_one(".num, .num2, .item-value")
            if name_tag and val_tag:
                name = name_tag.text.strip()
                val = val_tag.text.strip()
                if name and val:
                    data[name] = val

        # 대안: 특정 selector로 주요 지표 추출
        for row in soup.select("table tr, .rate_table tr"):
            cells = row.select("td, th")
            if len(cells) >= 2:
                key = cells[0].text.strip()
                val = cells[1].text.strip()
                if key and val and len(key) < 20:
                    data[key] = val
    except Exception as e:
        print(f"[지표] 오류: {e}")
    return data


def get_world_indices():
    data = {}
    try:
        url = "https://finance.naver.com/world/"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        for item in soup.select(".world_index li, .global_index li"):
            name = item.select_one(".txt_name, .name")
            val = item.select_one(".num, .value")
            chg = item.select_one(".change, .rate")
            if name and val:
                data[name.text.strip()] = {
                    "value": val.text.strip(),
                    "change": chg.text.strip() if chg else "",
                }
    except Exception as e:
        print(f"[글로벌] 오류: {e}")
    return data


def get_naver_market_summary():
    """네이버 금융 메인에서 주요 지표 텍스트 추출"""
    raw_text = ""
    try:
        url = "https://finance.naver.com/"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        # 주요 영역 텍스트 추출
        for section in soup.select(".group_index, .market_summary, .today_info, #container"):
            text = section.get_text(" ", strip=True)
            if len(text) > 50:
                raw_text += text[:1000] + "\n"
                break
    except Exception as e:
        print(f"[메인] 오류: {e}")
    return raw_text[:2000]


def get_financial_news():
    headlines = []
    sources = [
        ("https://finance.naver.com/news/news_list.nhn?mode=LSS2D&section_id=101&section_id2=258", "euc-kr"),
        ("https://www.yna.co.kr/economy/all/1", "utf-8"),
        ("https://news.naver.com/main/main.naver?mode=LSD&mid=shm&sid1=101", "euc-kr"),
    ]
    for url, enc in sources:
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            res.encoding = enc
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.select("a.title, .news-tit a, .tit a, h3 a, .hed a"):
                text = a.text.strip()
                if text and len(text) > 10 and text not in headlines:
                    headlines.append(text)
                if len(headlines) >= 30:
                    break
        except Exception as e:
            print(f"[뉴스] {url[:40]} 오류: {e}")
        if len(headlines) >= 30:
            break
    return headlines[:30]


def get_economic_calendar():
    """investing.com 경제 캘린더"""
    events = []
    try:
        url = "https://kr.investing.com/economic-calendar/"
        headers = {**HEADERS, "X-Requested-With": "XMLHttpRequest"}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        for row in soup.select("tr.js-event-item")[:20]:
            date = row.select_one(".date, td:nth-child(1)")
            event = row.select_one(".event, td:nth-child(4)")
            imp = row.select_one(".sentiment, .bull")
            if event:
                importance = len(imp.select("i")) if imp else 0
                if importance >= 2:
                    events.append({
                        "date": date.text.strip() if date else "",
                        "event": event.text.strip(),
                        "importance": importance,
                    })
    except Exception as e:
        print(f"[캘린더] 오류: {e}")
    return events[:15]


# ─── Claude 리포트 생성 ─────────────────────────────────────────

def generate_report(indices, indicators, world, news, calendar, raw_summary):
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]

    # 데이터 정리
    indices_text = "\n".join([f"{k}: {v['value']} ({v['change']}, {v['rate']})" for k, v in indices.items()]) or "수집 실패"
    indicators_text = "\n".join([f"{k}: {v}" for k, v in list(indicators.items())[:20]]) or ""
    world_text = "\n".join([f"{k}: {v['value']} {v['change']}" for k, v in list(world.items())[:10]]) or ""
    news_text = "\n".join(f"- {h}" for h in news[:25]) or "뉴스 수집 실패"
    calendar_text = "\n".join([f"{e['date']} {e['event']}" for e in calendar]) if calendar else "직접 조회 필요"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{
            "role": "user",
            "content": f"""오늘은 {date_str}({weekday})입니다. 아래 수집된 시장 데이터와 뉴스를 바탕으로 매일 오후 4시 마켓 클로징 리포트를 작성해주세요.

=== 수집 데이터 ===
[한국 지수]
{indices_text}

[시장 지표/환율/원자재]
{indicators_text}

[글로벌 지수]
{world_text}

[오늘의 주요 뉴스]
{news_text}

[경제 일정]
{calendar_text}

=== 작성 지침 ===
아래 형식을 반드시 따르되, 데이터가 없는 항목은 추정치나 일반적 맥락으로 보완하세요.

**작성 원칙:**
- 너무 길지도 짧지도 않게 (총 1500~2500자 내외)
- 매일 그날 가장 중요한 요소 위주로 선별
- 한국 시장 메인, 글로벌은 핵심만
- 주식과 연관된 이슈는 반드시 관련주 제시
- 신용거래/반대매매는 최근 동향이 있으면 반드시 포함
- 전쟁/전염병/주요 사회 이슈도 주식 연관 시 포함
- 새로운 임팩트 기술/산업 이슈가 있으면 포함
- 볼드(**), 이모티콘 적극 활용해서 가독성 높이기

**형식 (Telegram HTML 사용):**
<b>📊 마켓 클로징 리포트 | {date_str}({weekday})</b>

<b>🇰🇷 한국 시장 마감</b>
(KOSPI/KOSDAQ/환율 + 오늘 시장 특징 2~3줄)

<b>🌍 글로벌 주요 지표</b>
(원자재/금리/주요국 지수 + 핵심 이슈 2~3줄)

<b>🔥 오늘의 핵심 이슈 & 관련주</b>
(당일 가장 임팩트 있는 이슈 2~3개 / 각 이슈마다 관련 한국 주식 제시 / 번호 앞에 🔴🟠🟡 같은 컬러 이모티콘 없이 ① ② ③ 숫자만 사용)

<b>⚡ 주목할 신기술·산업 동향</b>
(있는 경우에만 / AI·로봇·에너지·우주·바이오 등)

<b>📅 주요 일정</b>
(1주일 내 + 중요한 것은 다음달까지 / 미국FOMC·CPI·한국금통위·실적발표 등)

위 형식에 맞게 완성된 리포트를 작성해주세요. HTML 태그만 사용하고, 마크다운(** 등)은 사용하지 마세요."""
        }]
    )
    return response.content[0].text.strip()


# ─── 전송 ──────────────────────────────────────────────────────

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = []
    current = ""
    for line in message.split("\n"):
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)

    for chunk in chunks:
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    print(f"전송 완료 ({len(chunks)}개 메시지)")


# ─── 메인 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    now = datetime.now(KST)
    print(f"마켓 리포트 생성 시작 ({now.strftime('%Y-%m-%d %H:%M')} KST)")

    print("한국 지수 수집 중...")
    indices = get_korean_indices()

    print("시장 지표 수집 중...")
    indicators = get_market_indicators()
    raw_summary = get_naver_market_summary()

    print("글로벌 지수 수집 중...")
    world = get_world_indices()

    print("뉴스 수집 중...")
    news = get_financial_news()

    print("경제 일정 수집 중...")
    calendar = get_economic_calendar()

    print(f"수집 완료 - 지표:{len(indicators)} 뉴스:{len(news)} 일정:{len(calendar)}")

    print("Claude 리포트 생성 중...")
    report = generate_report(indices, indicators, world, news, calendar, raw_summary)

    print("=" * 50)
    print(report)
    print("=" * 50)

    send_telegram(report)

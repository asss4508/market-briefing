import html as html_lib
import os
import re
import requests
import anthropic
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
KST = timezone(timedelta(hours=9))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


def get_rising_stocks(limit=20):
    stocks = []
    seen = set()
    for sosok in ["0", "1"]:
        url = f"https://finance.naver.com/sise/sise_rise_day.nhn?sosok={sosok}"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
            for row in soup.select("tr"):
                cells = row.select("td")
                if len(cells) < 8:
                    continue
                a_tag = cells[1].select_one("a") if len(cells) > 1 else None
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                code_match = re.search(r"code=(\w+)", href)
                if not code_match:
                    continue
                code = code_match.group(1)
                name = a_tag.text.strip()
                if not name or name in seen:
                    continue
                pct_text = cells[4].text.strip().replace("+", "").replace("%", "").replace(",", "")
                try:
                    pct = float(pct_text)
                except ValueError:
                    continue
                if pct <= 0:
                    continue
                seen.add(name)
                stocks.append({"name": name, "code": code, "change_pct": pct})
        except Exception as e:
            print(f"[KOSPI/KOSDAQ 수집 오류] {e}")

    stocks.sort(key=lambda x: x["change_pct"], reverse=True)
    return stocks[:limit]


def get_market_cap(code):
    url = f"https://finance.naver.com/item/main.nhn?code={code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        for th in soup.select("th"):
            if "시가총액" in th.get_text():
                td = th.find_next_sibling("td")
                if td:
                    text = td.get_text(" ", strip=True)
                    m = re.search(r"[\d,]+(?:\.\d+)?(?:조|억)?", text)
                    if m:
                        raw = text.replace("\xa0", "").strip()
                        raw = re.sub(r"\s+", "", raw)
                        m2 = re.search(r"([\d,.]+)(조|억)?", raw)
                        if m2:
                            return m2.group(1) + (m2.group(2) or "억")
    except Exception as e:
        print(f"[{code}] 시총 오류: {e}")
    return ""


def get_stock_news(code, limit=5):
    headlines = []
    for news_type in ["news_news", "news_investment"]:
        url = f"https://finance.naver.com/item/{news_type}.nhn?code={code}&page=1"
        try:
            res = requests.get(url, headers=HEADERS, timeout=8)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.select(".title a, td.title a"):
                text = a.text.strip()
                if text and len(text) > 5 and text not in headlines:
                    headlines.append(text)
                if len(headlines) >= limit:
                    break
        except Exception:
            pass
        if len(headlines) >= limit:
            break
    return headlines


def analyze_with_claude(stocks_data):
    stocks_info = "\n\n".join([
        f"종목: {s['name']} (+{s['change_pct']:.1f}%)\n뉴스:\n" +
        ("\n".join(f"- {h}" for h in s["headlines"]) if s["headlines"] else "- 뉴스 없음")
        for s in stocks_data
    ])

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": f"""아래 주식 종목들의 당일 상승 이유를 뉴스 기반으로 종목별 2줄씩 설명해주세요.
각 줄은 반드시 "- "로 시작하고, 핵심만 간결하게 작성하세요.
마지막 줄은 짧은 결론 단어(예: 기대감, 주목, 부각)로 마감하세요.
뉴스가 없으면 상승률과 테마를 기반으로 추정하세요.

반드시 아래 형식으로만 답하세요 ([]와 종목명 정확히 일치):
[삼진제약]
- 면역·염증 신약 임상 1상 신청에 따른 신약 개발 기대감
- 기대감

[한선엔지니어링]
- ...

{stocks_info}"""
        }]
    )
    return response.content[0].text.strip()


def parse_reasons(reasons_text, stocks_data):
    reasons = {}
    current = None
    for line in reasons_text.split("\n"):
        m = re.match(r'\[(.+?)\]', line.strip())
        if m:
            current = m.group(1).strip()
            reasons[current] = []
        elif line.strip().startswith("- ") and current is not None:
            reasons[current].append(line.strip())
    return reasons


def format_cap(cap_str):
    if not cap_str:
        return ""
    return cap_str


def build_message(stocks_data, reasons):
    now = datetime.now(KST)
    header = (
        f"📈 {now.strftime('%Y년 %m월 %d일')} 주요 상승 종목 현황\n\n"
        f"✅ 주요 상승 종목 현황({now.strftime('%Y-%m-%d %H:%M')} 기준)\n"
    )

    parts = []
    for s in stocks_data:
        name = html_lib.escape(s["name"])
        cap = format_cap(s.get("market_cap", ""))
        cap_str = f" ({cap})" if cap else ""
        pct = f"+{s['change_pct']:.1f}%"
        stock_reasons = reasons.get(s["name"], [])

        block = f"<b>{name}{cap_str} {pct}</b>\n"
        for r in stock_reasons:
            block += html_lib.escape(r) + "\n"
        parts.append(block.strip())

    body = "\n\n".join(parts)
    return header + body


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
    print("전송 완료")


if __name__ == "__main__":
    now = datetime.now(KST)
    print(f"주식 브리핑 시작 ({now.strftime('%H:%M')} KST)")

    print("상승 종목 수집 중...")
    stocks = get_rising_stocks(limit=20)
    if not stocks:
        print("상승 종목 없음 (휴장일 또는 오류) — 종료")
        exit(0)
    print(f"수집: {len(stocks)}개")

    print("시가총액 및 뉴스 수집 중...")
    for s in stocks:
        s["market_cap"] = get_market_cap(s["code"])
        s["headlines"] = get_stock_news(s["code"])
        print(f"  {s['name']} {s['market_cap']} +{s['change_pct']:.1f}%")

    print("AI 분석 중...")
    reasons_text = analyze_with_claude(stocks)
    reasons = parse_reasons(reasons_text, stocks)

    msg = build_message(stocks, reasons)
    print(msg)
    send_telegram(msg)

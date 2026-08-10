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
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
KST = timezone(timedelta(hours=9))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


# ─── 공통 헬퍼 ────────────────────────────────────────────────

def _yahoo_price(symbol):
    """Yahoo Finance에서 현재가와 등락률(%) 반환. 실패 시 (None, None)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    res = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
    res.raise_for_status()
    result = res.json().get("chart", {}).get("result")
    if not result:
        return None, None
    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = ((price - prev) / prev * 100) if (price and prev and prev != 0) else None
    return price, change_pct


def _fmt(price, change_pct, decimals=2):
    """가격 + 등락률을 문자열로 포맷."""
    base = f"{price:,.{decimals}f}"
    if change_pct is None:
        return base
    sign = "▲" if change_pct >= 0 else "▼"
    return f"{base} ({sign}{abs(change_pct):.2f}%)"


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
                print(f"[지수] {code}: {result[code]['value']} ({result[code]['rate']})")
        except Exception as e:
            print(f"[지수/{code}] 오류: {e}")
    return result


def get_exchange_rates():
    """Yahoo Finance API로 주요 환율 수집."""
    rates = {}
    targets = {
        "USDKRW=X":  ("원/달러",           1),
        "JPYKRW=X":  ("원/엔(100엔 기준)", 100),
        "EURKRW=X":  ("원/유로",           1),
        "CNYKRW=X":  ("원/위안",           1),
        "DX-Y.NYB":  ("달러인덱스(DXY)",   1),
    }
    for symbol, (label, multiplier) in targets.items():
        try:
            price, change_pct = _yahoo_price(symbol)
            if price is not None:
                display = price * multiplier
                rates[label] = _fmt(display, change_pct)
                print(f"[환율] {label}: {rates[label]}")
        except Exception as e:
            print(f"[환율/{symbol}] 오류: {e}")
    return rates


def get_commodities():
    """Yahoo Finance API로 원자재 및 미국 국채금리 수집."""
    data = {}
    targets = {
        "CL=F":  "WTI 유가(달러)",
        "BZ=F":  "브렌트유(달러)",
        "GC=F":  "금(달러/온스)",
        "^TNX":  "미국채 10년물(%)",
        "^TYX":  "미국채 30년물(%)",
    }
    for symbol, label in targets.items():
        try:
            price, change_pct = _yahoo_price(symbol)
            if price is not None:
                data[label] = _fmt(price, change_pct)
                print(f"[원자재] {label}: {data[label]}")
        except Exception as e:
            print(f"[원자재/{symbol}] 오류: {e}")
    return data


def get_world_indices():
    """Yahoo Finance API로 주요 글로벌 지수 수집."""
    data = {}
    targets = {
        "^GSPC":     "S&P 500",
        "^IXIC":     "나스닥",
        "^DJI":      "다우존스",
        "^N225":     "닛케이225",
        "000001.SS": "상해종합",
        "^FTSE":     "FTSE100",
    }
    for symbol, label in targets.items():
        try:
            price, change_pct = _yahoo_price(symbol)
            if price is not None:
                data[label] = _fmt(price, change_pct)
                print(f"[글로벌] {label}: {data[label]}")
        except Exception as e:
            print(f"[글로벌/{symbol}] 오류: {e}")
    return data


def get_financial_news():
    """네이버 금융/연합뉴스에서 헤드라인 수집.

    예전 셀렉터(a.title, .news-tit a 등)와 news_list.nhn 구주소가
    사이트 개편으로 더 이상 안 맞아 계속 0건이 나오고 있었음. 실제
    현재 마크업 기준으로 다시 확인한 셀렉터로 교체.
    """
    headlines = []
    seen = set()

    try:
        url = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.select("a"):
            if "article_id" not in a.get("href", ""):
                continue
            text = re.sub(r"^\d+", "", a.get_text(strip=True)).strip()
            if len(text) > 10 and text not in seen:
                seen.add(text)
                headlines.append(text)
    except Exception as e:
        print(f"[뉴스/네이버금융] 오류: {e}")

    try:
        url = "https://www.yna.co.kr/economy/all/1"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.select("a.tit-news"):
            text = a.get_text(strip=True)
            if len(text) > 10 and text not in seen:
                seen.add(text)
                headlines.append(text)
    except Exception as e:
        print(f"[뉴스/연합뉴스] 오류: {e}")

    return headlines[:30]


def get_economic_calendar():
    events = []
    try:
        url = "https://kr.investing.com/economic-calendar/"
        res = requests.get(url, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        for row in soup.select("tr.js-event-item")[:20]:
            date_el = row.select_one(".date, td:nth-child(1)")
            event_el = row.select_one(".event, td:nth-child(4)")
            imp_el = row.select_one(".sentiment, .bull")
            if event_el:
                importance = len(imp_el.select("i")) if imp_el else 0
                if importance >= 2:
                    events.append({
                        "date": date_el.text.strip() if date_el else "",
                        "event": event_el.text.strip(),
                    })
    except Exception as e:
        print(f"[캘린더] 오류: {e}")
    return events[:15]


# ─── Claude 리포트 생성 ─────────────────────────────────────────

def generate_report(indices, rates, commodities, world, news, calendar):
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]

    indices_text = "\n".join(
        f"{k}: {v['value']} ({v['change']}, {v['rate']})" for k, v in indices.items()
    ) or "수집 실패"
    rates_text = "\n".join(f"{k}: {v}" for k, v in rates.items()) or "수집 실패"
    commodities_text = "\n".join(f"{k}: {v}" for k, v in commodities.items()) or "수집 실패"
    world_text = "\n".join(f"{k}: {v}" for k, v in world.items()) or "수집 실패"
    news_text = "\n".join(f"- {h}" for h in news[:25]) or "뉴스 수집 실패"
    calendar_text = "\n".join(f"{e['date']} {e['event']}" for e in calendar) if calendar else "직접 조회 필요"

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

[환율]
{rates_text}

[원자재 및 채권금리]
{commodities_text}

[글로벌 지수]
{world_text}

[오늘의 주요 뉴스]
{news_text}

[경제 일정]
{calendar_text}

=== 작성 지침 ===

📏 분량 및 선별 원칙
- 총 1500~2500자 내외로 작성
- 매일 그날 가장 중요한 요소 위주로 선별
- 데이터가 없는 항목은 추정치나 일반적 맥락으로 보완

🌏 시장 범위
- 한국 시장 메인, 글로벌은 핵심만 요약

✅ 반드시 포함할 내용
- 주식과 연관된 이슈는 관련주 반드시 제시
- 신용거래/반대매매 최근 동향이 있으면 반드시 포함
- 전쟁/전염병/주요 사회 이슈는 주식 연관 시 포함
- 새로운 임팩트 기술/산업 이슈가 있으면 포함

🚫 제외 항목
- COFIX 등 국내 대출금리 지표는 제외

✍️ 형식 및 표현 규칙
- 볼드(HTML), 이모티콘 적극 활용해서 가독성 높이기
- 항목과 항목 사이 빈 줄(여백) 넣지 말 것
- 문장 중간에 대시(—, -, –) 사용하지 말 것, 쉼표나 자연스러운 문장으로 연결할 것
- 리포트 전체에서 모든 문장을 명사형으로 끝낼 것. 마지막 문장만이 아니라 단락 안의 모든 문장이 명사(또는 명사형 어미 ~상황, ~지속, ~압박, ~행진, ~양상, ~집중, ~우려, ~기대 등)로 끝나야 함. "~입니다" "~있습니다" "~합니다" "~됩니다" 같은 종결어미는 절대 사용 금지.

형식 (Telegram HTML 사용):
<b>📊 마켓 클로징 리포트 | {date_str}({weekday})</b>

<b>🇰🇷 한국 시장 마감</b>
KOSPI/KOSDAQ 수치와 오늘 시장 특징을 아래 형식으로 한 줄씩 5개 항목으로 작성할 것.
반드시 번호 형식 "1)" "2)" "3)" "4)" "5)" 을 사용하고, 각 줄은 명사(예: ~흐름, ~지속, ~우세, ~압박, ~마감)로 끝낼 것.
줄 바꿈만 하고 항목 사이 빈 줄 없음.
예시:
1) KOSPI 2,xxx.xx, KOSDAQ xxx.xx로 보합 마감
2) 외국인 순매도로 수급 부담 지속
3) 반도체 대형주 중심 하방 압력 우세
4) 원/달러 1,5xx원대 고환율 부담 유지
5) 신용잔고 증가세 속 반대매매 리스크 경계

<b>🌍 글로벌 주요 지표</b>
아래 항목을 각각 별도 단락으로 작성하고, 단락 사이에 반드시 빈 줄 한 줄을 넣을 것.
각 단락의 마지막 문장은 반드시 명사(예: ~상황, ~지속, ~압박, ~행진, ~양상)로 끝낼 것.

 - 💱 환율: 원/달러 수치 + DXY + 원/엔 포함, 달러 흐름 해석 한두 문장
 - 📈 미국 국채금리: 10년물, 30년물 수치 + 시장 의미 한두 문장
 - 🛢 국제유가: WTI, 브렌트 수치 + 배경 한두 문장
 - 🥇 금 시세: 금 수치 + 배경 한두 문장
 - 🗽 뉴욕 3대 지수: S&P500, 나스닥, 다우 수치 + 흐름 해석 한두 문장

<b>🔥 오늘의 핵심 이슈 & 관련주</b>
당일 가장 임팩트 있는 이슈 2~3개를 ① ② ③ 형식으로 작성.
각 이슈마다 관련 한국 주식을 반드시 제시할 것.
각 항목의 마지막 문장은 명사로 끝낼 것.

<b>⚡ 주목할 신기술·산업 동향</b>
(있는 경우에만 / AI·로봇·에너지·우주·바이오 등)

<b>📅 주요 일정</b>
(1주일 내 필수 포함: 미국 CPI·PPI·PCE·고용지표·FOMC / 한국금통위·실적발표 / 중요한 것은 다음달까지 / 날짜와 함께 시장 영향도 한 줄 코멘트)

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

    print("환율 수집 중...")
    rates = get_exchange_rates()

    print("원자재/채권금리 수집 중...")
    commodities = get_commodities()

    print("글로벌 지수 수집 중...")
    world = get_world_indices()

    print("뉴스 수집 중...")
    news = get_financial_news()

    print("경제 일정 수집 중...")
    calendar = get_economic_calendar()

    print(f"수집 완료 - 환율:{len(rates)} 원자재:{len(commodities)} 글로벌:{len(world)} 뉴스:{len(news)} 일정:{len(calendar)}")

    print("Claude 리포트 생성 중...")
    report = generate_report(indices, rates, commodities, world, news, calendar)

    print("=" * 50)
    print(report)
    print("=" * 50)

    send_telegram(report)

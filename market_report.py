import os
import re
import math
import time
import argparse
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
QUOTE_TIMES = {}
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
    timestamp = meta.get("regularMarketTime")
    if timestamp:
        QUOTE_TIMES[symbol] = datetime.fromtimestamp(timestamp, KST).strftime("%Y-%m-%d %H:%M KST")
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


def _quote_time(symbol):
    return f" [시세 기준 {QUOTE_TIMES.get(symbol, '시각 미확인')}]"


# ─── 데이터 수집 ───────────────────────────────────────────────

def get_korean_indices(now=None):
    """Read dated daily closes, never pre-open reset quotes or intraday prices."""
    now = now or datetime.now(KST)
    now = now.astimezone(KST)
    cutoff = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    result = {}
    for code in ("KOSPI", "KOSDAQ"):
        for attempt in range(3):
            try:
                url = f"https://m.stock.naver.com/api/index/{code}/price"
                res = requests.get(url, headers=HEADERS, timeout=10)
                res.raise_for_status()
                rows = res.json()
                valid = []
                for row in rows:
                    traded = datetime.fromisoformat(row["localTradedAt"]).date()
                    if traded <= cutoff and (cutoff - traded).days <= 7:
                        valid.append((traded, row))
                if not valid:
                    raise ValueError("No recent completed trading session")
                traded, row = max(valid, key=lambda item: item[0])
                values = [float(str(row[key]).replace(",", "")) for key in
                          ("closePrice", "compareToPreviousClosePrice", "fluctuationsRatio")]
                if not all(math.isfinite(value) for value in values) or values[0] <= 0:
                    raise ValueError("Invalid index values")
                result[code] = {
                    "value": f"{values[0]:,.2f}", "change": f"{values[1]:+.2f}",
                    "rate": f"{values[2]:+.2f}%", "date": traded.isoformat(), "source": url,
                }
                print(f"[index] {code}: {result[code]}")
                break
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                print(f"[index/{code}] attempt {attempt + 1}: {type(exc).__name__}")
                if attempt < 2:
                    time.sleep(2)
    return result


def validate_korean_indices(indices):
    if set(indices) != {"KOSPI", "KOSDAQ"}:
        raise RuntimeError("Both Korean indices are required; report delivery stopped")
    dates = {item["date"] for item in indices.values()}
    if len(dates) != 1:
        raise RuntimeError("Korean index trading dates disagree; report delivery stopped")
    return datetime.fromisoformat(dates.pop()).date()


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
                rates[label] = _fmt(display, change_pct) + _quote_time(symbol)
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
                data[label] = _fmt(price, change_pct) + _quote_time(symbol)
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
                data[label] = _fmt(price, change_pct) + _quote_time(symbol)
                print(f"[글로벌] {label}: {data[label]}")
        except Exception as e:
            print(f"[글로벌/{symbol}] 오류: {e}")
    return data


def get_financial_news(mode="korea"):
    """네이버 금융/연합뉴스에서 헤드라인 수집.

    예전 셀렉터(a.title, .news-tit a 등)와 news_list.nhn 구주소가
    사이트 개편으로 더 이상 안 맞아 계속 0건이 나오고 있었음. 실제
    현재 마크업 기준으로 다시 확인한 셀렉터로 교체.
    """
    headlines = []
    seen = set()

    try:
        section = "262" if mode == "global" else "258"
        url = f"https://news.naver.com/breakingnews/section/101/{section}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.select("a.sa_text_title"):
            text = a.get_text(strip=True)
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
    """이번 주 주요 지표(ForexFactory 공개 피드) + 다음 FOMC 회의(연준 공식 캘린더).

    investing.com은 Cloudflare 봇 차단(403)으로 늘 0건이었음. 두 소스 모두
    별도 인증 없이 접근 가능하고 실제로 응답을 확인한 것들로 교체.
    """
    events = []

    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        for e in res.json():
            if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
                continue
            try:
                dt = datetime.fromisoformat(e["date"])
            except (KeyError, ValueError):
                continue
            detail = f"[{e['impact']}] {e['title']}"
            if e.get("forecast") or e.get("previous"):
                detail += f" (예상 {e.get('forecast') or '-'}, 이전 {e.get('previous') or '-'})"
            events.append({"date": dt.strftime("%m/%d(%a)"), "event": detail})
    except Exception as e:
        print(f"[캘린더/ForexFactory] 오류: {e}")

    try:
        url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        today = datetime.now(KST).date()
        upcoming = []
        for panel in soup.select(".panel-default"):
            heading = panel.select_one(".panel-heading h4")
            m = re.search(r"(\d{4})\s+FOMC\s+Meetings", heading.get_text()) if heading else None
            if not m:
                continue
            year = int(m.group(1))
            for row in panel.select(".fomc-meeting"):
                month_el = row.select_one(".fomc-meeting__month")
                date_el = row.select_one(".fomc-meeting__date")
                if not month_el or not date_el:
                    continue
                day_nums = re.findall(r"\d+", date_el.get_text(strip=True))
                if not day_nums:
                    continue
                try:
                    meeting_date = datetime.strptime(
                        f"{year} {month_el.get_text(strip=True)} {day_nums[-1]}", "%Y %B %d"
                    ).date()
                except ValueError:
                    continue
                if meeting_date >= today:
                    upcoming.append(meeting_date)
        if upcoming:
            next_fomc = min(upcoming)
            events.append({
                "date": next_fomc.strftime("%Y-%m-%d"),
                "event": "美 FOMC 정례회의 (연준 공식 캘린더 기준)",
            })
    except Exception as e:
        print(f"[캘린더/FOMC] 오류: {e}")

    return events[:20]


# ─── Claude 리포트 생성 ─────────────────────────────────────────

def generate_report(indices, rates, commodities, world, news, calendar, mode="korea"):
    if mode not in ("korea", "global"):
        raise ValueError("Unknown report mode")
    now = datetime.now(KST)
    if mode == "korea":
        report_date = validate_korean_indices(indices)
    else:
        if not {"S&P 500", "나스닥", "다우존스"}.issubset(world):
            raise RuntimeError("US index data missing; global report stopped")
        report_date = now.date()
    date_str = report_date.strftime("%Y년 %m월 %d일")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][report_date.weekday()]
    title = "🌍 글로벌 모닝 리포트" if mode == "global" else "📊 한국 증시 마감 리포트"
    scope = ("글로벌 증시 중심. 미국 3대 지수와 해외 주요 시장을 맨 먼저 상세히 다루고, "
             "금리·달러·원자재·경제 일정을 연결. 한국 시장은 마지막에 확인된 영향만 짧게 요약. "
             "제목은 한국시간 발행일 기준. 한국 지수가 없으면 한국 시장 수치는 생략."
             if mode == "global" else
             "한국 증시 중심. KOSPI·KOSDAQ 종가와 국내 기업·산업 이슈를 우선하고, 글로벌은 핵심만 요약. "
             "제목은 한국 지수의 실제 거래일 기준. 휴장일에는 직전 거래일 기준임을 명시.")
    market_section = ("<b>🌍 글로벌 증시 핵심 요약</b>\n"
                      "미국 3대 지수와 해외 시장 특징을 확인된 데이터만으로 최대 5개 번호 항목으로 작성. "
                      "거래일 또는 조회 시각을 명시하고 휴장·미확정 종가를 오늘 마감으로 표현하지 말 것."
                      if mode == "global" else
                      "<b>🇰🇷 한국 시장 마감</b>\n"
                      "KOSPI/KOSDAQ 수치와 제공 자료로 확인되는 시장 특징만 최대 5개 번호 항목으로 작성. "
                      "근거가 부족하면 항목 수를 줄일 것.")

    indices_text = "\n".join(
        f"{k}: {v['value']} ({v['change']}, {v['rate']}), 거래일 {v['date']}" for k, v in indices.items()
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
            "content": f"""리포트 기준일은 {date_str}({weekday})입니다. 생성 시각은 {now.isoformat()}입니다.
{scope}
아래 수집된 시장 데이터와 뉴스만 바탕으로 리포트를 작성해주세요.

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
- 데이터가 없는 항목은 '확인 불가'로 표시하거나 생략. 추정치로 보완 금지.
- 수집 데이터에 없는 지수 방향, 투자자 수급, 업종 등락, 장중 고점, 상승·하락 원인 생성 금지.
- 뉴스는 제목만 수집한 자료이므로 제목에 없는 계약 조건, 수치, 배경을 덧붙이지 말 것.
- 금리 예상과 이전 수치는 전망 자료이며 확정 결정이나 인상 기정사실로 표현 금지.
- 관련주와 수혜·피해 관계는 제공 자료에서 확인된 경우만 포함. 근거 없는 종목 나열 금지.
- 글로벌 수치는 조회 시점 값이며 해당 거래일의 확정 종가로 단정 금지.
- 각 주요 지표의 제공된 시세 기준 시각을 표시. 미국 서머타임 여부와 무관하게 장 마감이 확인되지 않은 값은 잠정치로 표현.

🌏 시장 범위
- {scope}

✅ 반드시 포함할 내용
- 주식과 연관된 이슈는 제공 자료에서 확인되는 관련주만 제시
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
<b>{title} | {date_str}({weekday})</b>

{market_section}
번호 형식 "1)"부터 사용하고, 각 줄은 명사(예: ~흐름, ~지속, ~우세, ~압박, ~마감)로 끝낼 것.
줄 바꿈만 하고 항목 사이 빈 줄 없음.

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
각 이슈마다 제공 자료에서 확인되는 관련 한국 주식만 제시할 것.
각 항목의 마지막 문장은 명사로 끝낼 것.

<b>⚡ 주목할 신기술·산업 동향</b>
(있는 경우에만 / AI·로봇·에너지·우주·바이오 등)

<b>📅 주요 일정</b>
(수집된 경제 일정에 있는 미래 일정만 포함. 미수집 일정·발언자·예상치를 만들어 넣지 말 것. 출처의 날짜 기준을 유지할 것.)

위 형식에 맞게 완성된 리포트를 작성해주세요. HTML 태그만 사용하고, 마크다운(** 등)은 사용하지 마세요."""
        }]
    )
    return response.content[0].text.strip()


# ─── 데이터 소스 헬스체크 ─────────────────────────────────────────

def check_data_health(indices, rates, commodities, world, news, calendar):
    """개별 소스가 조용히 0건씩 실패해도 Claude가 그럴듯하게 리포트를 채워버려
    티가 안 나던 문제(뉴스/캘린더 몇 주간 0건) 재발 방지용. 기대치보다 심하게
    부족한 항목이 있으면 문자열 목록으로 반환하고, 없으면 빈 리스트."""
    expected = {
        "한국 지수": (len(indices), 2),
        "환율": (len(rates), 5),
        "원자재/금리": (len(commodities), 5),
        "글로벌 지수": (len(world), 6),
        "뉴스": (len(news), 5),
        "경제 캘린더": (len(calendar), 1),
    }
    return [
        f"{name} {count}건 (최소 {minimum}건 기대)"
        for name, (count, minimum) in expected.items()
        if count < minimum
    ]


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

def main(mode="korea", output=None):
    now = datetime.now(KST)
    print(f"마켓 리포트 생성 시작 ({now.strftime('%Y-%m-%d %H:%M')} KST)")

    print("한국 지수 수집 중...")
    indices = get_korean_indices()
    if mode == "korea":
        validate_korean_indices(indices)

    print("환율 수집 중...")
    rates = get_exchange_rates()

    print("원자재/채권금리 수집 중...")
    commodities = get_commodities()

    print("글로벌 지수 수집 중...")
    world = get_world_indices()

    print("뉴스 수집 중...")
    news = get_financial_news(mode)

    print("경제 일정 수집 중...")
    calendar = get_economic_calendar()

    print(f"수집 완료 - 환율:{len(rates)} 원자재:{len(commodities)} 글로벌:{len(world)} 뉴스:{len(news)} 일정:{len(calendar)}")

    health_warnings = check_data_health(indices, rates, commodities, world, news, calendar)
    if health_warnings:
        print("데이터 수집 경고: " + " / ".join(health_warnings))

    print("Claude 리포트 생성 중...")
    report = generate_report(indices, rates, commodities, world, news, calendar, mode=mode)

    if health_warnings:
        warn_block = "⚠️ <b>데이터 수집 경고</b>\n" + "\n".join(f"· {w}" for w in health_warnings) + "\n\n"
        report = warn_block + report

    print("=" * 50)
    print(report)
    print("=" * 50)

    if output:
        from pathlib import Path
        Path(output).write_text(report, encoding="utf-8")
    else:
        send_telegram(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("korea", "global"), default="korea")
    parser.add_argument("--output", help="Prepare a report without sending")
    args = parser.parse_args()
    main(args.mode, args.output)

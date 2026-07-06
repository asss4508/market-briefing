#!/usr/bin/env python3
"""
기업분석 리포트 생성기
Usage: python company_analysis.py "삼양식품"
"""

import os
import sys
import re
import json
import subprocess
import requests
import anthropic
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://finance.naver.com/",
}

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "https://asss4508.github.io/market-briefing")


# ─── 데이터 수집 ───────────────────────────────────────────────

def find_stock_code(company_name):
    """Naver Finance 자동완성 API로 종목코드 검색"""
    try:
        url = (
            f"https://ac.finance.naver.com/ac"
            f"?q={company_name}&q_enc=UTF-8&st=111&r_format=json"
            f"&r_enc=UTF-8&r_unicode=0&t_koreng=1&lt=1&u1=111"
        )
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json()
        items = data.get("items", [[]])[0]
        if items:
            code = items[0][1]
            name = items[0][0]
            print(f"[종목검색] {company_name} → {name} ({code})")
            return code, name
    except Exception as e:
        print(f"[종목검색] 오류: {e}")
    return None, company_name


def get_stock_data(code):
    """Naver Finance 종목 주요 지표 수집"""
    data = {}
    try:
        url = f"https://finance.naver.com/item/main.nhn?code={code}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")

        price_el = soup.select_one(".no_today .blind")
        if price_el:
            data["현재가"] = price_el.text.strip()

        for row in soup.select(".tb_type1 tr, .first tbody tr"):
            cells = row.select("th, td")
            if len(cells) >= 2:
                key = cells[0].text.strip()
                val = cells[1].text.strip()
                if key and val and 1 < len(key) < 15 and any(c.isdigit() for c in val):
                    data[key] = val

        print(f"[주가데이터] {len(data)}개 항목 수집")
    except Exception as e:
        print(f"[주가데이터] 오류: {e}")
    return data


def get_company_news(code):
    """Naver Finance 종목 관련 뉴스 수집"""
    news = []
    try:
        url = (
            f"https://finance.naver.com/item/news_news.nhn"
            f"?code={code}&page=1&sm=title_entity_id.basic&clusterId="
        )
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.select(".title a, .news_tit a"):
            text = a.text.strip()
            if text and len(text) > 5 and text not in news:
                news.append(text)
            if len(news) >= 15:
                break
        print(f"[뉴스] {len(news)}개 수집")
    except Exception as e:
        print(f"[뉴스] 오류: {e}")
    return news


# ─── Claude 분석 생성 ─────────────────────────────────────────

def generate_analysis(company_name, stock_code, stock_data, news):
    """Claude API로 기업분석 JSON 생성"""
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일")

    stock_text = "\n".join(f"{k}: {v}" for k, v in stock_data.items()) or "수집 실패"
    news_text = "\n".join(f"- {n}" for n in news) or "수집 실패"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": f"""오늘({date_str}) {company_name}(종목코드: {stock_code}) 기업분석 리포트를 작성해주세요.

=== 수집 데이터 ===
[주가 데이터]
{stock_text}

[최근 뉴스]
{news_text}

=== 작성 규칙 ===
- 모든 항목을 명사형으로 끝낼 것 (~상황, ~지속, ~우세, ~집중, ~전망, ~우려, ~기대, ~압박)
- "~입니다" "~합니다" "~됩니다" 종결어미 절대 사용 금지
- 한 줄씩 핵심만 간결하게
- 이모티콘 최소화, 영문/숫자로 구분
- 투자 의견은 수집 데이터 + 보유 지식 기반으로 명확하게 제시
- 데이터 부족 시 일반 지식으로 보완

=== 반환 형식 ===
반드시 아래 JSON 형식으로만 반환 (다른 텍스트 없이):

{{
  "summary": "한 줄 요약: 종목명, 현재가, 핵심 포인트",
  "recommendation": {{
    "opinion": "매수 or 중립 or 매도",
    "buy_pct": 0~100 정수,
    "hold_pct": 0~100 정수,
    "sell_pct": 0~100 정수,
    "target_price": "목표주가 범위 (예: 120,000~140,000원)",
    "basis": ["근거1", "근거2", "근거3"]
  }},
  "company_overview": {{
    "business": ["사업 핵심 내용 1줄", "2줄", "3줄"],
    "position": ["시장 지위 1줄", "2줄"],
    "recent_highlights": ["최근 이슈 1줄", "2줄", "3줄"]
  }},
  "financial_analysis": {{
    "key_metrics": ["지표명: 수치 및 해석 1줄", "2줄", "3줄", "4줄", "5줄"],
    "revenue_trend": ["매출 트렌드 1줄", "2줄", "3줄"],
    "profitability": ["수익성 포인트 1줄", "2줄", "3줄"]
  }},
  "investment_points": ["투자포인트 1", "2", "3", "4"],
  "swot": {{
    "strength": ["강점1", "강점2", "강점3"],
    "weakness": ["약점1", "약점2"],
    "opportunity": ["기회1", "기회2", "기회3"],
    "threat": ["위협1", "위협2"]
  }},
  "bull_case": ["긍정 시나리오 1", "2", "3", "4"],
  "risk_factors": ["리스크 1", "2", "3", "4"],
  "conclusion": ["결론 1줄", "2줄", "3줄"]
}}"""
        }]
    )

    text = response.content[0].text.strip()
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group())
    return json.loads(text)


# ─── HTML 생성 ────────────────────────────────────────────────

def build_html(company_name, stock_code, analysis, stock_data):
    now = datetime.now(KST)
    date_str = now.strftime("%Y.%m.%d")

    rec = analysis.get("recommendation", {})
    opinion = rec.get("opinion", "중립")
    buy_pct = rec.get("buy_pct", 0)
    hold_pct = rec.get("hold_pct", 0)
    sell_pct = rec.get("sell_pct", 0)
    target = rec.get("target_price", "-")
    price = stock_data.get("현재가", "-")

    op_color = {"매수": "#1a73e8", "중립": "#f29900", "매도": "#d93025"}.get(opinion, "#f29900")
    op_bg = {"매수": "#e8f0fe", "중립": "#fef7e0", "매도": "#fce8e6"}.get(opinion, "#fef7e0")

    def li_clean(items):
        return "".join(f'<li>{i}</li>' for i in (items or []))

    def li_num(items):
        return "".join(
            f'<li><span class="num">{str(idx + 1).zfill(2)}</span>{i}</li>'
            for idx, i in enumerate(items or [])
        )

    def swot_rows(items):
        return "".join(f'<div class="si">∙ {i}</div>' for i in (items or []))

    def box_rows(items):
        return "".join(f'<div class="bi">∙ {i}</div>' for i in (items or []))

    ov = analysis.get("company_overview", {})
    fa = analysis.get("financial_analysis", {})
    sw = analysis.get("swot", {})

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{company_name} 기업분석 · {date_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif;background:#f0f2f5;color:#1a1a2e;font-size:14px;line-height:1.75}}
.wrap{{max-width:880px;margin:0 auto;padding:20px 14px 40px}}

.hd{{background:#0d1b3e;color:#fff;border-radius:14px;padding:28px 32px;margin-bottom:14px}}
.hd-top{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
.co-name{{font-size:28px;font-weight:800;letter-spacing:-.5px}}
.co-meta{{font-size:12px;color:#8892a4;margin-top:5px}}
.badge{{display:inline-block;background:{op_color};color:#fff;padding:5px 16px;border-radius:20px;font-weight:700;font-size:14px}}
.tp{{font-size:12px;color:#8892a4;margin-top:5px}}
.hd-dt{{font-size:11px;color:#8892a4;text-align:right}}
.hd-sum{{margin-top:16px;font-size:13px;color:#b0bac9;border-top:1px solid #1e2d4a;padding-top:14px}}

.op-card{{background:#fff;border-radius:12px;padding:20px 24px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.op-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
.op-title{{font-size:15px;font-weight:700}}
.bar{{display:flex;height:9px;border-radius:5px;overflow:hidden;margin-bottom:8px}}
.b-buy{{background:#1a73e8;flex:{buy_pct}}}
.b-hold{{background:#f29900;flex:{hold_pct}}}
.b-sell{{background:#d93025;flex:{sell_pct}}}
.leg{{display:flex;gap:18px;font-size:12px;color:#555}}
.dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:middle}}
.d-buy{{background:#1a73e8}}.d-hold{{background:#f29900}}.d-sell{{background:#d93025}}
.pv{{font-weight:700}}

.card{{background:#fff;border-radius:12px;padding:20px 24px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.ct{{font-size:14px;font-weight:700;border-left:3px solid #1a73e8;padding-left:10px;margin-bottom:14px;color:#0d1b3e}}
.sub{{font-size:11px;font-weight:700;color:#888;letter-spacing:.6px;text-transform:uppercase;margin:12px 0 6px}}
.sub:first-of-type{{margin-top:0}}

ul.cl{{list-style:none;padding:0}}
ul.cl li{{padding:5px 0;border-bottom:1px solid #f2f2f2;font-size:13px;color:#333}}
ul.cl li:last-child{{border-bottom:none}}
ul.cl li::before{{content:"·";color:#1a73e8;font-weight:700;margin-right:8px}}

ul.nl{{list-style:none;padding:0}}
ul.nl li{{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #f2f2f2;font-size:13px;color:#333;align-items:flex-start}}
ul.nl li:last-child{{border-bottom:none}}
.num{{color:#1a73e8;font-weight:700;font-size:12px;min-width:22px;padding-top:1px}}

.swot{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.sb{{padding:14px;border-radius:8px}}
.sb.s{{background:#e8f0fe}}.sb.w{{background:#fce8e6}}.sb.o{{background:#e6f4ea}}.sb.t{{background:#fef7e0}}
.sl{{font-size:11px;font-weight:800;letter-spacing:.8px;margin-bottom:8px}}
.sb.s .sl{{color:#1a73e8}}.sb.w .sl{{color:#d93025}}.sb.o .sl{{color:#1e8e3e}}.sb.t .sl{{color:#b06000}}
.si{{font-size:12px;color:#333;padding:3px 0;border-bottom:1px solid rgba(0,0,0,.05);line-height:1.55}}
.si:last-child{{border-bottom:none}}

.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}}
.bull{{background:#e6f4ea;border-radius:12px;padding:18px 20px}}
.bear{{background:#fce8e6;border-radius:12px;padding:18px 20px}}
.bt{{font-size:13px;font-weight:700;margin-bottom:10px}}
.bull .bt{{color:#1e8e3e}}.bear .bt{{color:#d93025}}
.bi{{font-size:12px;color:#333;padding:3px 0;border-bottom:1px solid rgba(0,0,0,.05)}}
.bi:last-child{{border-bottom:none}}

.conc{{background:{op_bg};border:1px solid {op_color}22;border-radius:12px;padding:18px 22px;margin-top:14px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}}
.cv label{{font-size:11px;color:#666;display:block;margin-bottom:3px}}
.cv b{{font-size:16px}}

.chart-wrap{{background:#fff;border-radius:12px;padding:16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.cl-label{{font-size:11px;font-weight:700;color:#888;letter-spacing:.6px;text-transform:uppercase;margin-bottom:10px}}

.footer{{text-align:center;font-size:11px;color:#aaa;padding:20px 0 4px}}

@media(max-width:600px){{.swot,.two{{grid-template-columns:1fr}}.hd-top{{flex-direction:column}}}}
</style>
</head>
<body>
<div class="wrap">

<div class="hd">
  <div class="hd-top">
    <div>
      <div class="co-name">{company_name}</div>
      <div class="co-meta">KRX · {stock_code} · 현재가 {price}원</div>
    </div>
    <div style="text-align:right">
      <div class="badge">{opinion}</div>
      <div class="tp">목표주가 {target}</div>
      <div class="hd-dt">{date_str} 기준</div>
    </div>
  </div>
  <div class="hd-sum">{analysis.get("summary", "")}</div>
</div>

<div class="chart-wrap">
  <div class="cl-label">Price Chart</div>
  <div class="tradingview-widget-container" style="height:420px">
    <div id="tv_chart" style="height:400px"></div>
    <script src="https://s3.tradingview.com/tv.js"></script>
    <script>
    new TradingView.widget({{
      "autosize":true,"symbol":"KRX:{stock_code}","interval":"D",
      "timezone":"Asia/Seoul","theme":"light","style":"1","locale":"kr",
      "toolbar_bg":"#f8f9fb","enable_publishing":false,"allow_symbol_change":false,
      "container_id":"tv_chart","hide_side_toolbar":false,
      "studies":["MASimple@tv-basicstudies","Volume@tv-basicstudies"]
    }});
    </script>
  </div>
</div>

<div class="op-card">
  <div class="op-row">
    <div class="op-title">투자의견 종합</div>
    <div class="badge">{opinion}</div>
  </div>
  <div class="bar"><div class="b-buy"></div><div class="b-hold"></div><div class="b-sell"></div></div>
  <div class="leg">
    <span><span class="dot d-buy"></span>매수 <b class="pv">{buy_pct}%</b></span>
    <span><span class="dot d-hold"></span>중립 <b class="pv">{hold_pct}%</b></span>
    <span><span class="dot d-sell"></span>매도 <b class="pv">{sell_pct}%</b></span>
  </div>
  <ul class="cl" style="margin-top:12px">{li_clean(rec.get("basis", []))}</ul>
</div>

<div class="card">
  <div class="ct">핵심 투자포인트</div>
  <ul class="nl">{li_num(analysis.get("investment_points", []))}</ul>
</div>

<div class="card">
  <div class="ct">기업 개요</div>
  <div class="sub">A. 사업 내용</div>
  <ul class="cl">{li_clean(ov.get("business", []))}</ul>
  <div class="sub">B. 시장 지위</div>
  <ul class="cl">{li_clean(ov.get("position", []))}</ul>
  <div class="sub">C. 최근 주요 이슈</div>
  <ul class="cl">{li_clean(ov.get("recent_highlights", []))}</ul>
</div>

<div class="card">
  <div class="ct">재무 분석</div>
  <div class="sub">A. 핵심 지표</div>
  <ul class="cl">{li_clean(fa.get("key_metrics", []))}</ul>
  <div class="sub">B. 매출 트렌드</div>
  <ul class="cl">{li_clean(fa.get("revenue_trend", []))}</ul>
  <div class="sub">C. 수익성</div>
  <ul class="cl">{li_clean(fa.get("profitability", []))}</ul>
</div>

<div class="card">
  <div class="ct">SWOT 분석</div>
  <div class="swot">
    <div class="sb s"><div class="sl">S · Strength</div>{swot_rows(sw.get("strength", []))}</div>
    <div class="sb w"><div class="sl">W · Weakness</div>{swot_rows(sw.get("weakness", []))}</div>
    <div class="sb o"><div class="sl">O · Opportunity</div>{swot_rows(sw.get("opportunity", []))}</div>
    <div class="sb t"><div class="sl">T · Threat</div>{swot_rows(sw.get("threat", []))}</div>
  </div>
</div>

<div class="two">
  <div class="bull">
    <div class="bt">긍정적 시나리오 (Bull Case)</div>
    {box_rows(analysis.get("bull_case", []))}
  </div>
  <div class="bear">
    <div class="bt">리스크 요인 (Risk)</div>
    {box_rows(analysis.get("risk_factors", []))}
  </div>
</div>

<div class="card">
  <div class="ct">최종 결론</div>
  <ul class="nl">{li_num(analysis.get("conclusion", []))}</ul>
  <div class="conc">
    <div class="cv"><label>투자의견</label><b style="color:{op_color}">{opinion}</b></div>
    <div class="cv"><label>목표주가</label><b>{target}</b></div>
    <div class="cv"><label>Buy / Hold / Sell</label><b>{buy_pct}% · {hold_pct}% · {sell_pct}%</b></div>
  </div>
</div>

<div class="footer">
  본 자료는 AI 생성 투자 참고용이며, 투자 결과의 책임은 투자자 본인에게 있음<br>
  Generated by AI · {date_str}
</div>

</div>
</body>
</html>"""


# ─── Git 푸시 ─────────────────────────────────────────────────

def git_push(company_name, filepath):
    try:
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "add", str(filepath)], check=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"report: {company_name} 기업분석 {datetime.now(KST).strftime('%Y.%m.%d')}"],
            capture_output=True, text=True
        )
        if "nothing to commit" in result.stdout:
            print("[Git] 변경사항 없음")
        else:
            subprocess.run(["git", "push"], check=True)
            print("[Git] 푸시 완료")
    except subprocess.CalledProcessError as e:
        print(f"[Git] 오류: {e}")


# ─── 텔레그램 전송 ────────────────────────────────────────────

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[텔레그램 미설정]\n{message}")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=10)


# ─── 메인 ────────────────────────────────────────────────────

def main(company_name):
    print(f"\n{'='*50}")
    print(f"기업분석 시작: {company_name}")
    print(f"{'='*50}")

    # 1. 종목코드
    code, official_name = find_stock_code(company_name)
    if not code:
        send_telegram(f"❌ '{company_name}' 종목 검색 실패")
        return

    # 2. 주가 데이터
    print("주가 데이터 수집 중...")
    stock_data = get_stock_data(code)

    # 3. 뉴스
    print("뉴스 수집 중...")
    news = get_company_news(code)

    # 4. AI 분석
    print("AI 분석 생성 중...")
    analysis = generate_analysis(official_name, code, stock_data, news)

    # 5. HTML 생성
    print("HTML 리포트 생성 중...")
    html = build_html(official_name, code, analysis, stock_data)

    # 6. 파일 저장
    safe_name = re.sub(r'[^\w가-힣]', '_', official_name)
    filepath = Path(f"docs/reports/{safe_name}.html")
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(html, encoding="utf-8")
    print(f"[저장] {filepath}")

    # 7. Git push
    git_push(official_name, filepath)

    # 8. 텔레그램 링크 전송
    rec = analysis.get("recommendation", {})
    url = f"{GITHUB_PAGES_URL}/reports/{safe_name}.html"
    send_telegram(
        f"<b>📋 {official_name} 기업분석 완료</b>\n\n"
        f"투자의견: <b>{rec.get('opinion', '-')}</b>  "
        f"(매수 {rec.get('buy_pct', 0)}% / 중립 {rec.get('hold_pct', 0)}% / 매도 {rec.get('sell_pct', 0)}%)\n"
        f"목표주가: {rec.get('target_price', '-')}\n\n"
        f"🔗 {url}"
    )
    print(f"\n완료: {url}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python company_analysis.py '삼양식품'")
        sys.exit(1)
    main(sys.argv[1])

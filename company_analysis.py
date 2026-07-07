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
}
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "https://asss4508.github.io/market-briefing")


# ─── 데이터 수집 ───────────────────────────────────────────────

def find_stock_code(query):
    """종목코드(6자리) 또는 회사명으로 KRX 코드 및 회사명 반환"""
    query = query.strip()

    # 6자리 숫자 → 코드 직접 사용, Yahoo Finance에서 회사명 조회
    if re.match(r'^\d{6}$', query):
        code = query
        for suffix in [".KS", ".KQ"]:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}?interval=1d&range=1d"
                res = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
                meta = res.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
                if meta.get("regularMarketPrice"):
                    name = meta.get("shortName") or meta.get("longName") or code
                    print(f"[종목검색] 코드 직접 입력 → {name} ({code})")
                    return code, name
            except Exception as e:
                print(f"[종목검색/{code}{suffix}] 오류: {e}")
        print(f"[종목검색] {code} 조회 실패, 코드만으로 진행")
        return code, code

    # 회사명 → Yahoo Finance 검색
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}&lang=ko&region=KR&quotesCount=5&newsCount=0"
        res = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
        quotes = res.json().get("quotes", [])
        for q in quotes:
            sym = q.get("symbol", "")
            if sym.endswith(".KS") or sym.endswith(".KQ"):
                code = sym.replace(".KS", "").replace(".KQ", "")
                name = q.get("shortname") or q.get("longname") or query
                print(f"[종목검색] {query} → {name} ({code})")
                return code, name
    except Exception as e:
        print(f"[종목검색/Yahoo] 오류: {e}")
    print(f"[종목검색] 코드 미발견, Claude 지식 기반으로 진행")
    return None, query


def get_stock_data(code):
    """Yahoo Finance API로 주가 및 재무 지표 수집"""
    data = {}
    if not code:
        return data
    for suffix in [".KS", ".KQ"]:
        try:
            symbol = f"{code}{suffix}"
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
            res = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
            meta = res.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if not price:
                continue
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            change_pct = ((price - prev) / prev * 100) if prev else None
            data["현재가"] = f"{price:,.0f}원"
            if change_pct is not None:
                sign = "▲" if change_pct >= 0 else "▼"
                data["등락률"] = f"{sign}{abs(change_pct):.2f}%"
            if meta.get("fiftyTwoWeekHigh"):
                data["52주 고가"] = f"{meta['fiftyTwoWeekHigh']:,.0f}원"
            if meta.get("fiftyTwoWeekLow"):
                data["52주 저가"] = f"{meta['fiftyTwoWeekLow']:,.0f}원"
            if meta.get("regularMarketVolume"):
                data["거래량"] = f"{meta['regularMarketVolume']:,}"
            print(f"[주가데이터] {symbol}: {data.get('현재가', '-')} {data.get('등락률', '')}")
            break
        except Exception as e:
            print(f"[주가데이터/{suffix}] 오류: {e}")
    return data


def get_company_news(company_name, code):
    """Yahoo Finance 뉴스 수집 (Naver 차단 대비)"""
    news = []
    if code:
        for suffix in [".KS", ".KQ"]:
            try:
                url = f"https://query1.finance.yahoo.com/v1/finance/search?q={code}{suffix}&lang=ko&region=KR&quotesCount=0&newsCount=10"
                res = requests.get(url, headers=YAHOO_HEADERS, timeout=10)
                for item in res.json().get("news", []):
                    title = item.get("title", "").strip()
                    if title and title not in news:
                        news.append(title)
                if news:
                    break
            except Exception as e:
                print(f"[뉴스/Yahoo] 오류: {e}")
    print(f"[뉴스] {len(news)}개 수집")
    return news


def get_financial_history(code):
    """Yahoo Finance timeseries API로 5개년 연간 재무 데이터 수집"""
    if not code:
        return {}
    types = "annualTotalRevenue,annualOperatingIncome,annualNetIncome"
    for suffix in [".KS", ".KQ"]:
        symbol = f"{code}{suffix}"
        try:
            url = (
                "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1"
                f"/finance/timeseries/{symbol}"
                f"?type={types}&period1=1388534400&period2=9999999999"
            )
            r = requests.get(url, headers=YAHOO_HEADERS, timeout=15)
            items = r.json().get("timeseries", {}).get("result", [])
            if not items:
                continue
            series = {}
            for item in items:
                key = item.get("meta", {}).get("type", "")
                vals = item.get(key, [])
                if vals:
                    series[key] = {
                        v["asOfDate"][:4]: round(v["reportedValue"]["raw"] / 1e8)
                        for v in vals if "reportedValue" in v
                    }
            rev = series.get("annualTotalRevenue", {})
            if not rev:
                continue
            years = sorted(rev.keys())[-5:]
            result = {
                "years": years,
                "revenue":          [rev.get(y) for y in years],
                "operating_income": [series.get("annualOperatingIncome", {}).get(y) for y in years],
                "net_income":       [series.get("annualNetIncome", {}).get(y) for y in years],
            }
            print(f"[재무이력] {symbol}: {len(years)}년치", flush=True)
            return result
        except Exception as e:
            print(f"[재무이력/{suffix}] 오류: {e}", flush=True)
    return {}


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
(krx_code가 없으면 네 학습 데이터 기반으로 정확한 KRX 6자리 종목코드를 채울 것)

{{
  "krx_code": "KRX 6자리 종목코드 (예: 003230)",
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
  "revenue_segments": [
    {{"name": "사업부명", "pct": 0~100 정수, "note": "1줄 설명"}},
    ...
  ],
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

def build_html(company_name, stock_code, analysis, stock_data, financial_history=None):
    now = datetime.now(KST)
    date_str = now.strftime("%Y.%m.%d")
    fh = financial_history or {}

    # 재무 차트 JSON 직렬화
    fin_years_json  = json.dumps(fh.get("years", []))
    fin_rev_json    = json.dumps(fh.get("revenue", []))
    fin_op_json     = json.dumps(fh.get("operating_income", []))
    fin_net_json    = json.dumps(fh.get("net_income", []))

    # 사업부 매출 비중
    segments        = analysis.get("revenue_segments", [])
    seg_labels_json = json.dumps([s.get("name", "") for s in segments])
    seg_pcts_json   = json.dumps([s.get("pct", 0) for s in segments])
    seg_notes_json  = json.dumps([s.get("note", "") for s in segments])
    seg_colors      = ["#1a73e8","#34a853","#f29900","#ea4335","#9c27b0","#00bcd4"]

    # 5개년 재무 테이블 HTML
    def make_fin_table():
        years = fh.get("years", [])
        if not years:
            return '<p style="color:#aaa;font-size:12px;padding:6px 0">재무 데이터를 가져오지 못했습니다.</p>'
        hdrs = "".join(f"<th>{y}</th>" for y in years)
        rows = []
        for label, key in [("매출액 (억원)", "revenue"), ("영업이익 (억원)", "operating_income"), ("순이익 (억원)", "net_income")]:
            vals = fh.get(key, [])
            cells = ""
            for i, v in enumerate(vals):
                if v is None:
                    cells += "<td>-</td>"
                else:
                    txt = f"{v:,}"
                    if i > 0 and vals[i-1] is not None and vals[i-1] != 0:
                        chg = (v - vals[i-1]) / abs(vals[i-1]) * 100
                        color = "#1a73e8" if chg >= 0 else "#d93025"
                        arrow = "▲" if chg >= 0 else "▼"
                        txt += f'<br><span style="font-size:10px;color:{color}">{arrow}{abs(chg):.1f}%</span>'
                    cells += f"<td>{txt}</td>"
            rows.append(f'<tr><td><b>{label}</b></td>{cells}</tr>')
        return f'<table class="fin-tbl"><thead><tr><th>항목</th>{hdrs}</tr></thead><tbody>{"".join(rows)}</tbody></table>'

    fin_table_html = make_fin_table()

    # 사업부 범례 HTML (Python 3.11 f-string 내 백슬래시 금지 → 루프로 분리)
    seg_legend_html = ""
    for i, s in enumerate(segments):
        color = seg_colors[i % len(seg_colors)]
        note_text = s.get("note", "")
        note_span = ('<span style="color:#888">— ' + note_text + '</span>') if note_text else ""
        seg_legend_html += (
            f'<div>'
            f'<span class="seg-dot" style="background:{color}"></span>'
            f'<b>{s.get("name","")}</b> {s.get("pct",0)}%{note_span}'
            f'</div>'
        )

    rec = analysis.get("recommendation", {})
    opinion = rec.get("opinion", "중립")
    buy_pct = rec.get("buy_pct", 0)
    hold_pct = rec.get("hold_pct", 0)
    sell_pct = rec.get("sell_pct", 0)
    target = rec.get("target_price", "-")
    price = stock_data.get("현재가", "-")

    op_color = {"매수": "#1a73e8", "중립": "#f29900", "매도": "#d93025"}.get(opinion, "#f29900")
    op_bg = {"매수": "#e8f0fe", "중립": "#fef7e0", "매도": "#fce8e6"}.get(opinion, "#fef7e0")

    # 차트 섹션 (f-string 내 백슬래시 금지 → 미리 조립)
    naver_url = f"https://finance.naver.com/item/chart.naver?code={stock_code}" if stock_code else "https://finance.naver.com"
    tv_url = f"https://www.tradingview.com/chart/?symbol=KRX:{stock_code}" if stock_code else "https://www.tradingview.com"
    code_label = f"KRX:{stock_code}" if stock_code else "종목코드 없음"

    chart_header_html = (
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        f'<span class="cl-label">Price Chart · {code_label}</span>'
        '<div style="display:flex;gap:12px;font-size:12px">'
        f'<a href="{naver_url}" target="_blank" style="color:#1a73e8;font-weight:600">네이버 차트</a>'
        f'<a href="{tv_url}" target="_blank" style="color:#888">TradingView</a>'
        '</div></div>'
    )
    if stock_code:
        chart_body_html = (
            '<div class="tradingview-widget-container" style="height:460px;width:100%">'
            '<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>'
            '<script type="text/javascript"'
            ' src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>'
            '\n    {\n'
            '      "autosize": true,\n'
            f'      "symbol": "KRX:{stock_code}",\n'
            '      "interval": "W",\n'
            '      "timezone": "Asia/Seoul",\n'
            '      "theme": "light",\n'
            '      "style": "1",\n'
            '      "locale": "kr",\n'
            '      "allow_symbol_change": true\n'
            '    }\n'
            '</script></div>'
            '<div id="tv-fallback" style="display:none;text-align:center;padding:20px 0;color:#888;font-size:13px">'
            'TradingView에서 지원하지 않는 종목입니다. '
            f'<a href="{naver_url}" target="_blank" style="color:#1a73e8;font-weight:600">네이버 금융에서 차트 보기 →</a>'
            '</div>'
            '<script>setTimeout(function(){'
            'var w=document.querySelector(".tradingview-widget-container__widget");'
            'var f=w&&w.querySelector("iframe");'
            'if(!f||!f.src){document.getElementById("tv-fallback").style.display="block";}'
            '},5000);</script>'
        )
    else:
        chart_body_html = (
            '<div style="text-align:center;padding:30px;color:#aaa;font-size:13px">'
            '종목코드를 확인할 수 없습니다. '
            '<a href="https://finance.naver.com" target="_blank" style="color:#1a73e8">네이버 금융에서 확인</a>'
            '</div>'
        )

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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
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

.fin-tbl{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:4px;overflow-x:auto;display:block}}
.fin-tbl th,.fin-tbl td{{padding:7px 10px;text-align:right;border-bottom:1px solid #f2f2f2;white-space:nowrap}}
.fin-tbl th{{background:#f8f9fb;font-weight:700;color:#555;text-align:center}}
.fin-tbl td:first-child,.fin-tbl th:first-child{{text-align:left;min-width:120px}}
.fin-tbl tr:last-child td{{border-bottom:none}}
.fin-chart-wrap{{height:220px;margin-top:16px;position:relative}}
.seg-wrap{{display:flex;gap:20px;align-items:center;height:220px}}
.seg-chart-box{{flex:0 0 200px;height:200px}}
.seg-legend{{flex:1;font-size:12px;line-height:2}}
.seg-dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}}
@media(max-width:600px){{.swot,.two{{grid-template-columns:1fr}}.hd-top{{flex-direction:column}}.seg-wrap{{flex-direction:column;height:auto}}.seg-chart-box{{flex:none;width:100%}}}}
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
  {chart_header_html}
  {chart_body_html}
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
  <div class="ct">5개년 재무 현황 (연간, 억원)</div>
  {fin_table_html}
  <div class="fin-chart-wrap"><canvas id="finChart"></canvas></div>
</div>

<div class="card">
  <div class="ct">사업부별 매출 비중</div>
  <div class="seg-wrap">
    <div class="seg-chart-box"><canvas id="segChart"></canvas></div>
    <div class="seg-legend">{seg_legend_html}</div>
  </div>
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

<script>
const finYears   = {fin_years_json};
const finRevenue = {fin_rev_json};
const finOpIncome= {fin_op_json};
const finNetIncome={fin_net_json};
const segLabels  = {seg_labels_json};
const segPcts    = {seg_pcts_json};
const segNotes   = {seg_notes_json};

document.addEventListener('DOMContentLoaded', function() {{
  // ── 5개년 재무 차트 ──────────────────────────────
  const finEl = document.getElementById('finChart');
  if (finEl && finYears.length > 0) {{
    new Chart(finEl, {{
      data: {{
        labels: finYears,
        datasets: [
          {{ type:'bar', label:'매출액', data:finRevenue,   backgroundColor:'#1a73e830', borderColor:'#1a73e8', borderWidth:2, yAxisID:'y' }},
          {{ type:'bar', label:'영업이익', data:finOpIncome, backgroundColor:'#f2990030', borderColor:'#f29900', borderWidth:2, yAxisID:'y' }},
          {{ type:'line', label:'순이익', data:finNetIncome, borderColor:'#34a853', backgroundColor:'#34a85318', borderWidth:2, pointRadius:4, fill:true, tension:0.3, yAxisID:'y' }}
        ]
      }},
      options:{{
        responsive:true, maintainAspectRatio:false,
        plugins:{{
          legend:{{ position:'top', labels:{{ font:{{ size:11 }} }} }},
          tooltip:{{ callbacks:{{ label:c => `${{c.dataset.label}}: ${{c.parsed.y != null ? c.parsed.y.toLocaleString() : '-'}}억원` }} }}
        }},
        scales:{{ y:{{ ticks:{{ callback:v => v>=10000 ? `${{(v/10000).toFixed(0)}}조` : `${{v.toLocaleString()}}억` }}, grid:{{ color:'#f0f0f0' }} }} }}
      }}
    }});
  }}

  // ── 사업부 매출 도넛 차트 ───────────────────────
  const segEl = document.getElementById('segChart');
  if (segEl && segLabels.length > 0) {{
    new Chart(segEl, {{
      type:'doughnut',
      data:{{
        labels:segLabels,
        datasets:[{{ data:segPcts, backgroundColor:['#1a73e8','#34a853','#f29900','#ea4335','#9c27b0','#00bcd4'], borderWidth:2, borderColor:'#fff' }}]
      }},
      options:{{
        responsive:true, maintainAspectRatio:false,
        plugins:{{
          legend:{{ display:false }},
          tooltip:{{ callbacks:{{ label:c => `${{c.label}}: ${{c.parsed}}%${{segNotes[c.dataIndex] ? ' ('+segNotes[c.dataIndex]+')' : ''}}` }} }}
        }}
      }}
    }});
  }}
}});
</script>
</body>
</html>"""


# ─── Git 푸시 ─────────────────────────────────────────────────

def git_push(company_name, *filepaths):
    try:
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        for fp in filepaths:
            subprocess.run(["git", "add", str(fp)], check=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"report: {company_name} 기업분석 {datetime.now(KST).strftime('%Y.%m.%d')}"],
            capture_output=True, text=True
        )
        if "nothing to commit" in result.stdout:
            print("[Git] 변경사항 없음", flush=True)
        else:
            subprocess.run(["git", "pull", "--rebase", "origin", "master"], check=True)
            subprocess.run(["git", "push"], check=True)
            print("[Git] 푸시 완료", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"[Git] 오류: {e}", flush=True)


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


def update_report_list(official_name, safe_name, date_str, analysis, report_type="기업분석"):
    """docs/report-list.json 업데이트 (대시보드용 인덱스)"""
    list_path = Path("docs/report-list.json")
    try:
        existing = json.loads(list_path.read_text(encoding="utf-8")) if list_path.exists() else {"reports": []}
    except Exception:
        existing = {"reports": []}
    rec = analysis.get("recommendation", {})
    entry = {
        "type":     report_type,
        "title":    official_name,
        "file":     f"reports/{safe_name}.html",
        "date":     date_str,
        "opinion":  rec.get("opinion", "-"),
        "summary":  analysis.get("summary", ""),
        "buy_pct":  rec.get("buy_pct", 0),
        "hold_pct": rec.get("hold_pct", 0),
        "sell_pct": rec.get("sell_pct", 0),
    }
    existing["reports"] = [r for r in existing["reports"] if r.get("title") != official_name]
    existing["reports"].insert(0, entry)
    existing["updated"] = date_str
    list_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[리포트목록] {list_path} 업데이트", flush=True)
    return list_path


# ─── 메인 ────────────────────────────────────────────────────

def main(company_name):
    print(f"\n{'='*50}")
    print(f"기업분석 시작: {company_name}")
    print(f"{'='*50}")

    # 1. 종목코드 (실패해도 Claude 지식 기반으로 계속 진행)
    code, official_name = find_stock_code(company_name)

    # 2. 주가 데이터
    print("주가 데이터 수집 중...", flush=True)
    stock_data = get_stock_data(code)

    # 3. 뉴스
    print("뉴스 수집 중...", flush=True)
    news = get_company_news(official_name, code)

    # 4. 5개년 재무 데이터
    print("재무 이력 수집 중...", flush=True)
    financial_history = get_financial_history(code)

    # 5. AI 분석
    print("AI 분석 생성 중...", flush=True)
    analysis = generate_analysis(official_name, code, stock_data, news)

    # 6. HTML 생성 (종목코드 없으면 Claude가 분석에서 반환한 코드 사용)
    final_code = code or analysis.get("krx_code", "")
    print("HTML 리포트 생성 중...", flush=True)
    html = build_html(official_name, final_code, analysis, stock_data, financial_history)

    # 7. 파일 저장
    safe_name = re.sub(r'[^\w가-힣]', '_', official_name)
    filepath = Path(f"docs/reports/{safe_name}.html")
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(html, encoding="utf-8")
    date_str = datetime.now(KST).strftime("%Y.%m.%d")
    print(f"[저장] {filepath}", flush=True)

    # 8. 리포트 목록 업데이트
    list_path = update_report_list(official_name, safe_name, date_str, analysis)

    # 9. Git push (리포트 + 목록 함께)
    git_push(official_name, filepath, list_path)

    # 10. 텔레그램 링크 전송
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

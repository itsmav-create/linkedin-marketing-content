# daily_investment.py
# Multi-asset Daily Investment Digest (separate from your yfinance market_digest.py)
# Uses Tiingo + AlphaVantage + Finnhub + OpenAI + SendGrid

import os
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import formatdate

import requests
from openai import OpenAI
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ========= ENV ==========

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("MARKET_DIGEST_FROM")     # use your existing sender
TO_EMAIL = os.getenv("MARKET_DIGEST_TO")         # existing recipient

TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# guard so it only runs at 8am Sydney when scheduled (set to "false" for testing)
ENFORCE_SYDNEY_8AM = os.getenv("ENFORCE_SYDNEY_8AM", "false").lower() in ("1", "true", "yes")

client = OpenAI(api_key=OPENAI_API_KEY)

# ========= CONFIG ==========

MARKET_UNIVERSE = {
    "Indices": ["SPY", "QQQ"],          # US equity proxies
    "Sectors": ["XLK", "XLF", "XLE"],   # Tech, Financials, Energy
    "AIInfra": ["SOXX", "SMH", "AIQ", "BOTZ"],  # AI / Semis / Robotics & AI
    "FX": ["AUDUSD"],                   # FX pair for AUD
    "Crypto": ["BTC", "ETH"],           # Crypto majors
}

TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"
ALPHAVANTAGE_BASE = "https://www.alphavantage.co/query"
FINNHUB_BASE = "https://finnhub.io/api/v1"

HTTP_TIMEOUT = 10


# ========= HTTP HELPER ==========

def safe_get(url, params=None, headers=None):
    params = params or {}
    headers = headers or {"User-Agent": "Marmik-Daily-Investment-Agent/1.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"⚠️ HTTP error {url}: {e}")
        return None


# ========= TIINGO: PRICES ==========

def get_tiingo_price(symbol):
    """
    Returns dict: {symbol, close, prev_close, pct_change, date}
    Uses Tiingo daily endpoint (free tier is OK for a small universe).
    """
    url = f"{TIINGO_BASE}/{symbol}/prices"
    params = {
        "token": TIINGO_API_KEY,
        "resampleFreq": "daily",
        "limit": 2,
    }
    data = safe_get(url, params=params)
    if not data or len(data) == 0:
        return None

    latest = data[-1]
    prev = data[-2] if len(data) > 1 else None

    close = latest.get("close")
    prev_close = prev.get("close") if prev else None
    pct_change = None
    if close is not None and prev_close:
        try:
            pct_change = (close - prev_close) / prev_close * 100.0
        except Exception:
            pct_change = None

    return {
        "symbol": symbol,
        "close": close,
        "prev_close": prev_close,
        "pct_change": pct_change,
        "date": latest.get("date"),
    }


# ========= ALPHAVANTAGE: TECHNICALS / FX ==========

def get_alpha_rsi(symbol):
    """
    RSI(14) for equity/ETF symbol.
    """
    params = {
        "function": "RSI",
        "symbol": symbol,
        "interval": "daily",
        "time_period": 14,
        "series_type": "close",
        "apikey": ALPHAVANTAGE_API_KEY,
    }
    data = safe_get(ALPHAVANTAGE_BASE, params=params)
    if not data or "Technical Analysis: RSI" not in data:
        return None

    rsi_series = data["Technical Analysis: RSI"]
    if not rsi_series:
        return None

    latest_date = sorted(rsi_series.keys())[-1]
    try:
        rsi_value = float(rsi_series[latest_date]["RSI"])
    except Exception:
        return None

    return {"symbol": symbol, "rsi": rsi_value, "date": latest_date}


def get_fx_rate(pair="AUDUSD"):
    """
    Realtime FX for AUDUSD (or other XXXYYY).
    """
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": pair[:3],
        "to_currency": pair[3:],
        "apikey": ALPHAVANTAGE_API_KEY,
    }
    data = safe_get(ALPHAVANTAGE_BASE, params=params)
    rate = None
    if data and "Realtime Currency Exchange Rate" in data:
        raw = data["Realtime Currency Exchange Rate"]
        try:
            rate = float(raw["5. Exchange Rate"])
        except Exception:
            rate = None
    return {"pair": pair, "rate": rate}


# ========= FINNHUB: NEWS & CRYPTO ==========

def get_finnhub_news(symbol, days=3, max_items=3):
    """
    Company/ETF news last few days.
    """
    today = datetime.utcnow().date()
    from_date = today - timedelta(days=days)

    params = {
        "token": FINNHUB_API_KEY,
        "symbol": symbol,
        "from": from_date.isoformat(),
        "to": today.isoformat(),
    }
    url = f"{FINNHUB_BASE}/company-news"
    data = safe_get(url, params=params)
    if not data:
        return []

    news_items = []
    for item in data[:max_items]:
        ts = item.get("datetime")
        pub_date = datetime.utcfromtimestamp(ts).isoformat() if ts else None
        news_items.append({
            "headline": item.get("headline"),
            "source": item.get("source"),
            "summary": item.get("summary"),
            "url": item.get("url"),
            "published": pub_date,
        })
    return news_items


def get_crypto_quote(coin):
    """
    Crypto quote via Finnhub (BINANCE:COINUSDT).
    """
    params = {
        "symbol": f"BINANCE:{coin}USDT",
        "token": FINNHUB_API_KEY,
    }
    url = f"{FINNHUB_BASE}/quote"
    data = safe_get(url, params=params)
    if not data:
        return None

    price = data.get("c")
    prev_close = data.get("pc")
    pct_change = None
    if price is not None and prev_close:
        try:
            pct_change = (price - prev_close) / prev_close * 100.0
        except Exception:
            pct_change = None

    return {
        "symbol": coin,
        "price": price,
        "prev_close": prev_close,
        "pct_change": pct_change,
    }


# ========= BUILD SNAPSHOT ==========

def build_market_snapshot():
    """
    Pull prices + RSI + FX + Crypto + News; return a JSON-serialisable dict.
    """
    snapshot = {
        "indices": [],
        "sectors": [],
        "ai_infra": [],
        "fx": [],
        "crypto": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Indices, sectors, AI infra ETFs
    for group_name, key in [
        ("indices", "Indices"),
        ("sectors", "Sectors"),
        ("ai_infra", "AIInfra"),
    ]:
        for symbol in MARKET_UNIVERSE.get(key, []):
            price = get_tiingo_price(symbol)
            rsi = get_alpha_rsi(symbol)
            news = get_finnhub_news(symbol)

            snapshot[group_name].append({
                "symbol": symbol,
                "price": price,
                "rsi": rsi,
                "news": news,
            })
            time.sleep(1)  # be gentle with free tiers

    # FX
    fx_items = []
    for pair in MARKET_UNIVERSE.get("FX", []):
        fx_items.append(get_fx_rate(pair))
        time.sleep(1)
    snapshot["fx"] = fx_items

    # Crypto
    crypto_items = []
    for coin in MARKET_UNIVERSE.get("Crypto", []):
        crypto_items.append(get_crypto_quote(coin))
        time.sleep(1)
    snapshot["crypto"] = crypto_items

    return snapshot


# ========= OPENAI SUMMARY (HYBRID CIO STYLE) ==========

def build_openai_summary(snapshot):
    system_prompt = (
        "You are the Chief Investment Strategist for Marmik Vyas. "
        "Write like a CIO who thinks in signals, scenarios, and sector themes. "
        "Blend three perspectives:\n"
        "1) SIGNALS → momentum, accumulation, RSI tone, rotation, fatigue.\n"
        "2) IF–THEN SCENARIOS → what assets typically do if macro triggers move.\n"
        "3) THEMES → structural narratives (AI infra, rates, energy, consumer resilience).\n\n"
        "STRICT RULES:\n"
        "- Do NOT give explicit buy/sell/hold recommendations.\n"
        "- Do NOT suggest portfolio weights or personalised financial advice.\n"
        "- Speak in CIO-style language: accumulation, fatigue, risk-on, risk-off, reversal tone, rotation.\n"
        "- Your job is to make the data INTERPRETABLE and ACTIONABLE without crossing compliance boundaries."
    )

    user_prompt = (
        "Using the following JSON snapshot, write an HTML-ready daily investment briefing with this exact structure:\n\n"

        "1) MARKET MOOD (Signals Overview)\n"
        "- 2–3 bullets summarising risk-on/off tone, momentum, broad rotations.\n"
        "- Mention where strength/fatigue is building.\n\n"

        "2) SIGNALS ACROSS INDICES, SECTORS & AI INFRA\n"
        "- 4–6 bullets highlighting leadership, laggards, accumulation vs exhaustion.\n"
        "- Explicitly call out AI infra ETFs (SOXX, SMH, AIQ, BOTZ) where relevant.\n"
        "- Include RSI tone where relevant (overbought/oversold only as descriptive, not advisory).\n\n"

        "3) IF–THEN SETUPS (Scenarios)\n"
        "- 3–5 bullets starting with 'If… then watch…' linking macro triggers to asset behaviour.\n"
        "- Examples: 'If yields continue to soften, then tech, REITs, and AI infra ETFs typically strengthen.'\n"
        "- No explicit recommendations, just behavioural logic.\n\n"

        "4) THEMES → ASSET MAPPINGS\n"
        "- 3–5 bullets mapping major themes to assets "
        "(AI infra → SOXX/SMH/AIQ/BOTZ; USD moves → commodities; rates → growth vs cyclicals).\n"
        "- Use directional language only.\n\n"

        "5) CRYPTO AS A RISK GAUGE\n"
        "- 1–3 bullets on BTC/ETH tone and what it signals for broader risk appetite.\n\n"

        "6) TODAY’S WATCHLIST HIGHLIGHTS\n"
        "- 3–6 bullets that give CIO-style guidance WITHOUT advice.\n"
        "- Use phrasing like: 'areas showing accumulation tone', 'names entering fatigue zones', "
        "'sectors or AI infra ETFs with reversal setups'.\n\n"

        "STRICT STYLE:\n"
        "- Must be under 500 words.\n"
        "- Must be edgy, crisp, professional, mobile-friendly.\n"
        "- NEVER give buy/sell recommendations or personalised advice.\n\n"
        f"SNAPSHOT_DATA:\n{json.dumps(snapshot)}"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.25,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    return resp.choices[0].message.content


# ========= EMAIL BUILD / SEND ==========

def build_email_html(summary_html_block):
    sent_date = formatdate(localtime=True)
    return f"""
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">
        <h2>Daily Investment Digest</h2>
        <p style="color:#555;margin-top:-6px;">Generated {sent_date}</p>
        {summary_html_block}
      </body>
    </html>
    """


def send_email(subject, html_body):
    if not SENDGRID_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY missing")
    if not FROM_EMAIL:
        raise RuntimeError("MARKET_DIGEST_FROM missing")
    if not TO_EMAIL:
        raise RuntimeError("MARKET_DIGEST_TO missing")

    msg = Mail(
        from_email=FROM_EMAIL,
        to_emails=[e.strip() for e in TO_EMAIL.split(",") if e.strip()],
        subject=subject,
        html_content=html_body,
    )
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    resp = sg.send(msg)
    print(f"SENDGRID_STATUS {resp.status_code} to={TO_EMAIL}")


# ========= MAIN ==========

def main():
    if ENFORCE_SYDNEY_8AM:
        now_syd = datetime.now(ZoneInfo("Australia/Sydney"))
        if now_syd.hour != 8:
            print(f"⏭️ Skipping run (Sydney time: {now_syd.isoformat()}) – not 08:00.")
            return

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    if not TIINGO_API_KEY:
        raise RuntimeError("TIINGO_API_KEY missing")
    if not ALPHAVANTAGE_API_KEY:
        raise RuntimeError("ALPHAVANTAGE_API_KEY missing")
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY missing")

    snapshot = build_market_snapshot()
    summary_html_block = build_openai_summary(snapshot)
    email_html = build_email_html(summary_html_block)
    send_email("Daily Investment Digest", email_html)


if __name__ == "__main__":
    main()

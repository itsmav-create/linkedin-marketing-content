import os
from datetime import datetime, timedelta, timezone

import yfinance as yf
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from openai import OpenAI

# ---------------------------------------------------------
# CONFIG – DAILY WATCHLIST (BROADER UNIVERSE)
# ---------------------------------------------------------

WATCHLIST = [
    # ASX ETFs
    "VAS.AX",
    "VGS.AX",
    "VGE.AX",
    "NDQ.AX",
    "IVV.AX",
    "STW.AX",

    # AU large caps
    "TLS.AX",
    "CBA.AX",
    "BHP.AX",
    "RIO.AX",
    "WES.AX",
    "CSL.AX",
    "XRO.AX",

    # US/global ETFs
    "QQQ",
    "SPY",
    "XLK",
    "XLF",
    "XLE",
    "XLU",
    "SMH",
    "VNQ",

    # US big tech
    "NVDA",
    "MSFT",
    "AAPL",
    "AMZN",
    "GOOGL",
    "META",
]

MIN_DAILY_MOVE_PCT = 2.0
MIN_WEEKLY_MOVE_PCT = 5.0

MODEL_NAME = "gpt-4.1-mini"


# ---------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------

def fetch_market_data(tickers):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=10)

    results = []

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(start=start, end=end)

            if hist.empty or len(hist) < 2:
                continue

            latest = hist.iloc[-1]
            prev = hist.iloc[-2]

            price = float(latest["Close"])
            prev_price = float(prev["Close"])
            day_change_pct = ((price - prev_price) / prev_price) * 100

            if len(hist) >= 6:
                week_price = float(hist.iloc[-6]["Close"])
            else:
                week_price = float(hist.iloc[0]["Close"])
            week_change_pct = ((price - week_price) / week_price) * 100

            info = t.info if hasattr(t, "info") else {}
            name = info.get("shortName") or info.get("longName") or ticker

            results.append({
                "ticker": ticker,
                "name": name,
                "price": round(price, 2),
                "day_change_pct": round(day_change_pct, 2),
                "week_change_pct": round(week_change_pct, 2),
            })
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")

    return results


def filter_significant_moves(market_data):
    return [
        item for item in market_data
        if abs(item["day_change_pct"]) >= MIN_DAILY_MOVE_PCT
        or abs(item["week_change_pct"]) >= MIN_WEEKLY_MOVE_PCT
    ]


# ---------------------------------------------------------
# AI PROMPT BUILDER
# ---------------------------------------------------------

def build_ai_prompt(market_data, significant):
    today = datetime.now().strftime("%d %b %Y")

    snapshot_lines = []
    snapshot_lines.append(f"Date: {today}\n")
    snapshot_lines.append("WATCHLIST SNAPSHOT:")

    for item in market_data:
        snapshot_lines.append(
            f"- {item['ticker']} ({item['name']}): Price {item['price']}, "
            f"Day {item['day_change_pct']}%, Week {item['week_change_pct']}%"
        )

    snapshot_lines.append("\nSIGNIFICANT MOVES:")
    if significant:
        for item in significant:
            snapshot_lines.append(
                f"- {item['ticker']} ({item['name']}): "
                f"Day {item['day_change_pct']}%, Week {item['week_change_pct']}%"
            )
    else:
        snapshot_lines.append("- None")

    snapshot_text = "\n".join(snapshot_lines)

    system_msg = (
        "You are an investment research assistant for a senior marketing and commercial leader. "
        "He thinks like a CIO: in themes, risk, and capital allocation. "
        "This is a DAILY briefing—focus on directional tone, near-term rotations, "
        "accumulation vs de-risking, leadership vs fatigue. "
        "Never give buy/sell advice. Use language like: accumulation trend, fatigue, "
        "rotation out of, momentum building, de-risking zone, structural uptrend."
    )

    user_msg = f"""
Below is the snapshot of the full watchlist.

{snapshot_text}

Write a DAILY CIO-style market pulse using this structure

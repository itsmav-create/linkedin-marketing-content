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
    "VAS.AX",   # Aus shares
    "VGS.AX",   # Global shares
    "VGE.AX",   # Emerging markets
    "NDQ.AX",   # Nasdaq 100 (AU)
    "IVV.AX",   # S&P 500 (AU)
    "STW.AX",   # ASX 200

    # AU large caps
    "TLS.AX",   # Telstra
    "CBA.AX",   # CBA
    "BHP.AX",   # BHP
    "RIO.AX",   # Rio Tinto
    "WES.AX",   # Wesfarmers
    "CSL.AX",   # CSL
    "XRO.AX",   # Xero

    # US / global ETFs & sectors
    "QQQ",      # Nasdaq 100
    "SPY",      # S&P 500
    "XLK",      # Tech
    "XLF",      # Financials
    "XLE",      # Energy
    "XLU",      # Utilities
    "SMH",      # Semiconductors
    "VNQ",      # REITs

    # US big tech
    "NVDA",
    "MSFT",
    "AAPL",
    "AMZN",
    "GOOGL",
    "META",
]

# Only highlight moves above these thresholds
MIN_DAILY_MOVE_PCT = 2.0
MIN_WEEKLY_MOVE_PCT = 5.0

MODEL_NAME = "gpt-4.1-mini"

# ---------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------


def fetch_market_data(tickers):
    """
    Fetch latest price & performance using yfinance.
    Returns list of dicts with:
    ticker, name, price, day_change_pct, week_change_pct
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=10)  # enough for 1-week lookback

    data = []

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(start=start, end=end)

            if hist.empty or len(hist) < 2:
                continue

            latest_row = hist.iloc[-1]
            prev_row = hist.iloc[-2]

            price = float(latest_row["Close"])
            prev_price = float(prev_row["Close"])

            day_change_pct = ((price - prev_price) / prev_price) * 100

            # Weekly move (approx 5 trading days back)
            if len(hist) >= 6:
                week_price = float(hist.iloc[-6]["Close"])
            else:
                week_price = float(hist.iloc[0]["Close"])

            week_change_pct = ((price - week_price) / week_price) * 100

            info = t.info if hasattr(t, "info") else {}
            name = info.get("shortName") or info.get("longName") or ticker

            data.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "price": round(price, 2),
                    "day_change_pct": round(day_change_pct, 2),
                    "week_change_pct": round(week_change_pct, 2),
                }
            )

        except Exception as e:
            print(f"Error fetching {ticker}: {e}")

    return data


def filter_significant_moves(market_data):
    """
    Filter for instruments that have meaningful moves.
    """
    significant = []
    for item in market_data:
        if (
            abs(item["day_change_pct"]) >= MIN_DAILY_MOVE_PCT
            or abs(item["week_change_pct"]) >= MIN_WEEKLY_MOVE_PCT
        ):
            significant.append(item)
    return significant


def build_ai_prompt(market_data, significant_moves):
    """
    Build a prompt for OpenAI with a more directional, CIO-style tone:

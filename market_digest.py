import os
from datetime import datetime, timedelta, timezone

import yfinance as yf
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from openai import OpenAI

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

# Add/remove tickers as you like
WATCHLIST = [
    # ASX ETFs
    "VAS.AX",   # Vanguard Australian Shares
    "VGS.AX",   # Vanguard Intl Shares
    "VGE.AX",   # Emerging Markets
    "NDQ.AX",   # Nasdaq 100
    "IVV.AX",   # S&P 500

    # Individual stocks you might care about
    "TLS.AX",   # Telstra
    "CBA.AX",   # Commonwealth Bank
    "NVDA",     # Nvidia
    "MSFT",     # Microsoft
]

# Only highlight moves above these thresholds
MIN_DAILY_MOVE_PCT = 2.0
MIN_WEEKLY_MOVE_PCT = 5.0

MODEL_NAME = "gpt-4.1-mini"  # good balance of cost and quality

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------


def fetch_market_data(tickers):
    """
    Fetch latest price & performance using yfinance.
    Returns list of dicts with:
    ticker, name, price, day_change_pct, week_change_pct
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=10)

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

            # Weekly move (5 trading days back)
            if len(hist) >= 6:
                week_price = float(hist.iloc[-6]["Close"])
            else:
                week_price = float(hist.iloc[0]["Close"])

            week_change_pct = ((price - week_price) / week_price) * 100

            # Try to get company/ETF name
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
    Build a clean natural-language prompt for OpenAI.
    """
    today_str = datetime.now().strftime("%d %b %Y")

    lines = []
    lines.append(f"Date: {today_str}")
    lines.append("")
    lines.append("WATCHLIST SNAPSHOT:")
    for item in market_data:
        lines.append(
            f"- {item['ticker']} ({item['name']}): "
            f"Price {item['price']}, "
            f"Day {item['day_change_pct']}%, "
            f"Week {item['week_change_pct']}%"
        )

    lines.append("")
    lines.append("SIGNIFICANT MOVES:")
    if significant_moves:
        for item in significant_moves:
            lines.append(
                f"- {item['ticker']} ({item['name']}): "
                f"Day {item['day_change_pct']}%, "
                f"Week {item['week_change_pct']}%"
            )
    else:
        lines.append("- None above thresholds.")

    snapshot = "\n".join(lines)

    # System prompt (sets persona)
    system_msg = (
        "You are an investment research assistant for a senior marketing & commercial leader. "
        "He is experienced, thinks in terms of strategic allocation, not trading. "
        "He lives in Australia and cares about quality global tech, telco, ETFs, and property. "
        "Provide a concise board-style briefing: what moved, why it matters, and what deserves a deeper look. "
        "Do NOT give financial advice or trade instructions. "
        "Tone = sharp, professional, simple, and mobile-friendly."
    )

    # User prompt (contains the data + instructions)
    user_msg = f"""
Below is the latest watchlist snapshot and significant moves:

{snapshot}

Please produce a structured email-style briefing with:

1. A headline + 2–3 sentence 'big picture' summary.
2. A section: 'Key Moves on Your Watchlist' – bullet points, short, clear.
3. A section: 'What Might Be Worth a Deeper Look' – 3–5 ideas (NOT advice), just themes/angles.
4. A section: 'Macro & Sentiment Signals' – infer themes (defensives, rotation, AI strength, risk-on/off).
5. A section: 'Strategic Questions to Consider' – self-reflection, no recommendations.

Keep it clean, no jargon, no hype, readable on mobile.
"""

    return system_msg, user_msg


def generate_email_body(system_msg, user_msg):
    """
    Call OpenAI to generate the written summary email.
    """
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content


def send_email_via_sendgrid(subject, body):
    """
    Send the email using SendGrid.
    """
    sg_api_key = os.environ["SENDGRID_API_KEY"]
    to_email = os.environ["MARKET_DIGEST_TO"]
    from_email = os.environ["MARKET_DIGEST_FROM"]

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        html_content=body.replace("\n", "<br>"),
    )

    try:
        sg = SendGridAPIClient(sg_api_key)
        resp = sg.send(message)
        print(f"Email sent. Status: {resp.status_code}")
    except Exception as e:
        print(f"SendGrid error: {e}")


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    print("Fetching market data…")
    market_data = fetch_market_data(WATCHLIST)

    if not market_data:
        print("No market data returned. Exiting.")
        return

    print("Filtering significant moves…")
    significant = filter_significant_moves(market_data)

    print("Building AI prompt…")
    system_msg, user_msg = build_ai_prompt(market_data, significant)

    print("Generating email body…")
    body = generate_email_body(system_msg, user_msg)

    today_str = datetime.now().strftime("%d %b %Y")
    subject = f"Weekly Market Scan – Watchlist Briefing ({today_str})"

    print("Sending email…")
    send_email_via_sendgrid(subject, body)

    print("Done.")


if __name__ == "__main__":
    main()

import os
import feedparser
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser
from email.mime.text import MIMEText
import smtplib
import json
from openai import OpenAI

# ========= CONFIG =========

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Email settings (use an app password / SMTP relay)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
RECIPIENT_EMAIL = "YOUR_EMAIL_HERE"   # where you want the pack

# Core sources – add/remove as you like
RSS_FEEDS = [
    # Harvard Business Review
    "https://feeds.hbr.org/harvardbusiness",
    # McKinsey Insights (all)
    "https://www.mckinsey.com/insights/rss",
    # Marketing Week – latest news
    "http://www.marketingweek.co.uk/include/qbe/rss_latest_news.xml",
]

# Lookback window for "current"
LOOKBACK_DAYS = 14

# ========= CORE LOGIC =========

client = OpenAI(api_key=OPENAI_API_KEY)

def fetch_recent_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    articles = []

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            # Try to parse published date; if missing, skip
            published = None
            for key in ("published", "updated", "created"):
                if key in entry:
                    try:
                        published = dateparser.parse(entry[key])
                        break
                    except Exception:
                        pass

            if not published:
                continue

            # Normalise to UTC if naive
            if not published.tzinfo:
                published = published.replace(tzinfo=timezone.utc)

            if published < cutoff:
                continue

            articles.append({
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", "").strip(),
                "summary": entry.get("summary", "")[:400],
                "published": published.isoformat()
            })

    return articles


def select_and_enrich_articles(articles):
    """
    Ask the model to:
    - pick the best 5–6 for YOUR positioning
    - generate hooks + short posts for LinkedIn
    Returns a Python list of dicts.
    """

    if not articles:
        return []

    system_prompt = """
You are an AI Content Strategist for Marmik Vyas.

Marmik is a 24+ year senior marketing & commercial leader:
- Ex Ogilvy, Prudential ICICI AMC, Dell, Lenovo, nbn, ALAT (sovereign-backed tech manufacturing).
- Credibility pillars: marketing transformation, GTM & demand, martech & AI, performance & ROI, exec/board alignment, P&L thinking.

AUDIENCE:
- CEOs/Founders/Commercial leaders (ANZ, APAC, Middle East)
- CMOs & Marketing/Growth leaders
- PE/VC & investors
- Senior Product/Digital/CX leaders

FILTERING RULES:
- Only pick articles that help senior leaders think sharper about:
  - Marketing effectiveness & ROI
  - GTM and demand strategy
  - AI in marketing & growth
  - Customer experience & retention
  - Operating models, org design, transformation
- Exclude junior how-tos, clickbait, generic AI hype, or deep infra with no C-level angle.
- Tone: edgy, clear, commercially grounded, no fluff.
"""

    # Build the user content as a plain string for the Chat Completions API
    user_content = (
        "From the following recent articles, select the 5–6 that best fit the rules. "
        "For each selected article, return JSON ONLY with this schema:\n"
        "[\n"
        "  {\n"
        "    \"title\": \"...\",\n"
        "    \"url\": \"...\",\n"
        "    \"published\": \"ISO8601\",\n"
        "    \"primary_audience\": \"CEOs | CMOs | Investors | Product/Digital/CX | Multiple\",\n"
        "    \"why_it_matters\": \"Max 2 sentences, business impact only.\",\n"
        "    \"hook\": \"Max 22-word scroll-stopping opening line.\",\n"
        "    \"li_post\": \"80-140 word post in Marmik's voice: tension → insight → sharp POV → question. No hashtags, no emojis.\"\n"
        "  }\n"
        "]\n\n"
        f"ARTICLES:\n{json.dumps(articles[:60])}"
    )

    # Use the stable Chat Completions API that is available in the default OpenAI SDK
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        temperature=0.2
    )

    raw = response.choices[0].message.content

    try:
        data = json.loads(raw)
        return data[:6]
    except Exception:
        # Emit the raw text to the GitHub Actions log to help debug if needed
        print("⚠️ Could not parse JSON from model. Raw output follows:")
        print(raw)
        return []


    system_prompt = """
You are an AI Content Strategist for Marmik Vyas.

Marmik is a 24+ year senior marketing & commercial leader:
- Ex Ogilvy, Prudential ICICI AMC, Dell, Lenovo, nbn, ALAT (sovereign-backed tech manufacturing).
- Credibility pillars: marketing transformation, GTM & demand, martech & AI, performance & ROI, exec/board alignment, P&L thinking.

AUDIENCE:
- CEOs/Founders/Commercial leaders (ANZ, APAC, Middle East)
- CMOs & Marketing/Growth leaders
- PE/VC & investors
- Senior Product/Digital/CX leaders

FILTERING RULES:
- Only pick articles that help senior leaders think sharper about:
  - Marketing effectiveness & ROI
  - GTM and demand strategy
  - AI in marketing & growth
  - Customer experience & retention
  - Operating models, org design, transformation
- Exclude junior how-tos, clickbait, generic AI hype, or deep infra with no C-level angle.
- Tone: edgy, clear, commercially grounded, no fluff.
"""

    user_prompt = {
        "role": "user",
        "content": (
            "From the following recent articles, select the 5–6 that best fit the rules. "
            "For each selected article, return JSON ONLY with this schema:\n"
            "[\n"
            "  {\n"
            "    \"title\": \"...\",\n"
            "    \"url\": \"...\",\n"
            "    \"published\": \"ISO8601\",\n"
            "    \"primary_audience\": \"CEOs | CMOs | Investors | Product/Digital/CX | Multiple\",\n"
            "    \"why_it_matters\": \"Max 2 sentences, business impact only.\",\n"
            "    \"hook\": \"Max 22-word scroll-stopping opening line.\",\n"
            "    \"li_post\": \"80-140 word post in Marmik's voice: tension → insight → sharp POV → question. No hashtags, no emojis.\"\n"
            "  }\n"
            "]\n\n"
            f"ARTICLES:\n{json.dumps(articles[:60])}"  # cap for token sanity
        )
    }

    response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt["content"]}
    ]
)

raw = response.choices[0].message.content


    try:
    data = json.loads(raw)
    return data[:6]
except Exception:
    print("⚠️ Could not parse JSON output from model. Raw output:")
    print(raw)
    return []



def build_email_html(curated):
    if not curated:
        return """
        <h2>Sunday LinkedIn Content Pack</h2>
        <p>No suitable fresh articles found this week. Consider posting a POV on:</p>
        <ul>
          <li>AI in marketing beyond vanity pilots</li>
          <li>What CEOs should really expect from martech</li>
          <li>Turning brand into measurable P&L impact</li>
        </ul>
        """

    rows = []
    for i, item in enumerate(curated, start=1):
        rows.append(f"""
        <tr>
          <td style="vertical-align:top; padding:12px 8px; border-bottom:1px solid #eee;">
            <strong>{i}. {item['title']}</strong><br>
            <a href="{item['url']}">{item['url']}</a><br>
            <em>Audience:</em> {item['primary_audience']}<br>
            <em>Why it matters:</em> {item['why_it_matters']}<br><br>
            <strong>Hook:</strong><br>
            {item['hook']}<br><br>
            <strong>Draft post:</strong><br>
            {item['li_post']}
          </td>
        </tr>
        """)

    html = f"""
    <html>
      <body style="font-family: -apple-system,BlinkMacSystemFont,system-ui,sans-serif; color:#111;">
        <h2>Sunday LinkedIn Content Pack</h2>
        <p>Curated for your positioning: executive-grade, AI + growth + GTM + performance. Pick, tweak 1%, post.</p>
        <table width="100%" cellspacing="0" cellpadding="0">
          {''.join(rows)}
        </table>
      </body>
    </html>
    """
    return html


def send_email(subject, html_body):
    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = RECIPIENT_EMAIL

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def main():
    articles = fetch_recent_articles()
    curated = select_and_enrich_articles(articles)
    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)


if __name__ == "__main__":
    main()


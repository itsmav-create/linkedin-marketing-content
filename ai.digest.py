# ai.digest.py
# Sends a Sunday-night LinkedIn content pack via email.
# Sources: 20 reputable marketing/business/AI publications via RSS.
# Delivery: SMTP (works with SendGrid). Scheduling: GitHub Actions (cron).
# Author: Marmik’s Content Agent

import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
from dateutil import parser as dateparser
from openai import OpenAI

# ========= CONFIG (env-first; safe for GitHub Actions) =========

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# SMTP config (override via GitHub secrets)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendgrid.net")  # default to SendGrid
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")                       # for SendGrid, this is literally "apikey"
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")               # your SendGrid API key
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)          # must be a verified Single Sender in SendGrid
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "your@email.com")

# Lookback window for "current"
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))

# Sydney-time guard: only proceed if it's exactly 21:00 in Australia/Sydney
ENFORCE_SYDNEY_21H = os.getenv("ENFORCE_SYDNEY_21H", "true").lower() in ("1", "true", "yes")

# ========= SOURCES: Top 20 feeds =========
# Feeds are easy to tweak; if one is unavailable, we skip it gracefully.
RSS_FEEDS = [
    # Strategy / Leadership / Management
    "https://feeds.hbr.org/harvardbusiness",                    # Harvard Business Review
    "https://sloanreview.mit.edu/feed/",                        # MIT Sloan Management Review
    "https://www.mckinsey.com/insights/rss",                    # McKinsey Insights (broad)
    "https://www.bain.com/insights/rss/",                       # Bain Insights
    "https://www.bcg.com/rss",                                  # BCG Perspectives/Insights

    # Big consulting / enterprise insight hubs
    "https://www2.deloitte.com/us/en/insights/rss.html",        # Deloitte Insights (US feed)
    "https://www.accenture.com/us-en/blogs/blogs-rss",          # Accenture (blogs)
    "https://www.ey.com/en_gl/rss",                             # EY Global RSS

    # Marketing & advertising industry
    "http://www.marketingweek.co.uk/include/qbe/rss_latest_news.xml",  # Marketing Week
    "https://www.thedrum.com/rss",                               # The Drum
    "https://www.campaignlive.co.uk/rss",                        # Campaign
    "https://adage.com/section/rss.xml",                         # Ad Age (general)
    "https://www.warc.com/latest-news-rss",                      # WARC (news feed; some content paywalled)

    # Platform & growth insights
    "https://www.thinkwithgoogle.com/intl/en-apac/feed/",        # Think with Google (APAC)
    "https://openai.com/blog/rss",                               # OpenAI blog
    "https://stripe.com/blog/feed.rss",                          # Stripe blog (growth/product/fintech)
    "https://a16z.com/feed/",                                    # a16z
    "https://www.sequoiacap.com/article/feed/",                  # Sequoia Capital (articles)

    # Regional relevance / B2B institute
    "https://sloanreview.mit.edu/asia-pacific/feed/",            # MIT SMR APAC (if empty, feedparser returns 0)
    "https://business.linkedin.com/marketing-solutions/blog.rss" # LinkedIn Marketing Solutions blog
]

# ========= OPENAI CLIENT =========
client = OpenAI(api_key=OPENAI_API_KEY)


# ========= CORE FUNCTIONS =========

def fetch_recent_articles():
    """Fetch recent articles within LOOKBACK_DAYS from the RSS feeds."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    articles = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"⚠️ Feed parse error: {feed_url} -> {e}")
            continue

        if not getattr(feed, "entries", None):
            print(f"ℹ️ No entries in feed (skip): {feed_url}")
            continue

        for entry in feed.entries:
            # Parse date
            published = None
            for key in ("published", "updated", "created"):
                if key in entry:
                    try:
                        published = dateparser.parse(entry[key])
                        break
                    except Exception:
                        pass

            if not published:
                # Some feeds omit dates; include as "recent" fallback
                published = datetime.now(timezone.utc)

            if not published.tzinfo:
                published = published.replace(tzinfo=timezone.utc)
            if published < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": (entry.get("summary") or "")[:800],
                "published": published.isoformat(),
                "source": feed.get("feed", {}).get("title", feed_url)
            })

    # Deduplicate by URL
    seen = set()
    deduped = []
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if a["url"] in seen:
            continue
        seen.add(a["url"])
        deduped.append(a)

    print(f"✅ Collected {len(deduped)} recent items from {len(RSS_FEEDS)} feeds.")
    return deduped


def select_and_enrich_articles(articles):
    """
    Use OpenAI to pick the best 5–6 articles and generate hooks + short posts.
    Returns list[dict].
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
""".strip()

    user_content = (
        "From the following recent articles, select the 5–6 that best fit the rules. "
        "Return JSON ONLY with this schema:\n"
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
        f"ARTICLES:\n{json.dumps(articles[:120])}"  # cap for token sanity
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.25,
    )

    raw = resp.choices[0].message.content
    try:
        data = json.loads(raw)
        # Ensure minimal keys exist; add defaults if the model missed any
        cleaned = []
        for item in data[:6]:
            cleaned.append({
                "title": item.get("title", "").strip(),
                "url": item.get("url", "").strip(),
                "published": item.get("published", ""),
                "primary_audience": item.get("primary_audience", "Multiple"),
                "why_it_matters": item.get("why_it_matters", ""),
                "hook": item.get("hook", ""),
                "li_post": item.get("li_post", ""),
            })
        return [x for x in cleaned if x["title"] and x["url"]]
    except Exception:
        print("⚠️ Could not parse JSON from model. Raw output follows:")
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
            <strong>{i}. {item.get('title','')}</strong><br>
            <a href="{item.get('url','')}">{item.get('url','')}</a><br>
            <em>Audience:</em> {item.get('primary_audience','')}<br>
            <em>Why it matters:</em> {item.get('why_it_matters','')}<br><br>
            <strong>Hook:</strong><br>
            {item.get('hook','')}<br><br>
            <strong>Draft post:</strong><br>
            {item.get('li_post','')}
          </td>
        </tr>
        """)

    html = f"""
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;color:#111;">
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
    msg["From"] = FROM_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    print(f"✅ Email sent to {RECIPIENT_EMAIL} via {SMTP_HOST}:{SMTP_PORT} as {FROM_EMAIL}")


def main():
    # Sydney-time guard (so cron can stay in UTC and we still hit exactly 21:00 local)
    if ENFORCE_SYDNEY_21H:
        now_syd = datetime.now(ZoneInfo("Australia/Sydney"))
        if now_syd.hour != 21:
            print(f"⏭️ Skipping run (Sydney time = {now_syd.isoformat()}); not 21:00.")
            return

    # Sanity checks
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_USER/SMTP_PASSWORD not set in secrets")
    if not RECIPIENT_EMAIL:
        raise RuntimeError("RECIPIENT_EMAIL not set in secrets")
    if not FROM_EMAIL:
        raise RuntimeError("FROM_EMAIL not set (must be a verified Single Sender in SendGrid)")

    articles = fetch_recent_articles()
    curated = select_and_enrich_articles(articles)
    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)


if __name__ == "__main__":
    main()

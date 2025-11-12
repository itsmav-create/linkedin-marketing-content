# ai.digest.py
# Sunday LinkedIn Content Pack for Marmik Vyas
# Curates 5–6 executive-level insights weekly (AI, marketing, GTM, business strategy)
# Sources: 20+ RSS feeds with automatic backups if fewer than 6 valid picks
# Runs weekly at 9pm Sydney (guarded), delivered by SendGrid via GitHub Actions

import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from openai import OpenAI
import feedparser
from dateutil import parser as dateparser

# ======== ENVIRONMENT CONFIG ========

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))
ENFORCE_SYDNEY_21H = os.getenv("ENFORCE_SYDNEY_21H", "true").lower() in ("1", "true", "yes")

client = OpenAI(api_key=OPENAI_API_KEY)

# ======== PRIMARY FEEDS ========

RSS_FEEDS = [
    "https://feeds.hbr.org/harvardbusiness",
    "https://sloanreview.mit.edu/feed/",
    "https://www.mckinsey.com/insights/rss",
    "https://www.bain.com/insights/rss/",
    "https://www.bcg.com/rss",
    "https://www2.deloitte.com/us/en/insights/rss.html",
    "https://www.accenture.com/us-en/blogs/blogs-rss",
    "https://www.ey.com/en_gl/rss",
    "http://www.marketingweek.co.uk/include/qbe/rss_latest_news.xml",
    "https://www.thedrum.com/rss",
    "https://www.campaignlive.co.uk/rss",
    "https://adage.com/section/rss.xml",
    "https://www.warc.com/latest-news-rss",
    "https://www.thinkwithgoogle.com/intl/en-apac/feed/",
    "https://openai.com/blog/rss",
    "https://stripe.com/blog/feed.rss",
    "https://a16z.com/feed/",
    "https://www.sequoiacap.com/article/feed/",
    "https://sloanreview.mit.edu/asia-pacific/feed/",
    "https://business.linkedin.com/marketing-solutions/blog.rss",
]

# ======== BACKUP FEEDS ========

BACKUP_FEEDS = [
    "https://www.fastcompany.com/rss",                     # innovation / leadership
    "https://techcrunch.com/feed/",                        # business tech
    "https://www.cmo.com/rss",                             # Adobe CMO.com
    "https://www.socialmediatoday.com/rss.xml",            # social trends
    "https://www.forbes.com/leadership/feed/",
    "https://fortune.com/feed/",
    "https://www.inc.com/rss",                             # entrepreneurship
    "https://www.marketingdive.com/feeds/news/",
    "https://www.smartcompany.com.au/feed/",               # ANZ SME focus
    "https://medium.com/feed/@briansolis",                 # thought leadership
]


# ======== FETCH ========

def fetch_recent_articles(feeds):
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    articles = []

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"⚠️ Error parsing feed {feed_url}: {e}")
            continue
        if not getattr(feed, "entries", None):
            print(f"ℹ️ Empty feed: {feed_url}")
            continue
        for entry in feed.entries:
            pub_date = None
            for k in ("published", "updated", "created"):
                if k in entry:
                    try:
                        pub_date = dateparser.parse(entry[k])
                        break
                    except Exception:
                        pass
            if not pub_date:
                pub_date = datetime.now(timezone.utc)
            if not pub_date.tzinfo:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff:
                continue
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            if not title or not url:
                continue
            articles.append({
                "title": title,
                "url": url,
                "summary": (entry.get("summary", "") or "")[:600],
                "published": pub_date.isoformat(),
                "source": feed.get("feed", {}).get("title", feed_url)
            })
    # deduplicate
    seen = set()
    deduped = []
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if a["url"] not in seen:
            seen.add(a["url"])
            deduped.append(a)
    return deduped


# ======== CURATE VIA OPENAI ========

def select_and_enrich_articles(articles):
    if not articles:
        return []

    system_prompt = """
You are the AI Content Strategist for Marmik Vyas — a senior marketing and commercial leader (ex-Ogilvy, Dell, Lenovo, nbn, ALAT).
Your job is to find thought-worthy business and marketing ideas for LinkedIn posts that appeal to senior leaders.

Audience:
- CEOs, CMOs, Growth Heads, Product/Digital/CX leaders, Investors.
Focus on:
- AI in marketing & business transformation
- Marketing effectiveness & GTM strategy
- Customer experience, retention, performance loops
- Org design, leadership, and transformation insights.

Exclude:
- Basic how-tos, shallow AI hype, tools lists, or clickbait.
Tone:
- Edgy but professional, insight-rich, commercial, and concise.
""".strip()

    user_content = (
        "From these recent articles, pick 5–6 that match the brief above. "
        "Return JSON ONLY in this format:\n"
        "[{"
        "\"title\": \"\", "
        "\"url\": \"\", "
        "\"published\": \"\", "
        "\"primary_audience\": \"CEOs | CMOs | Product | Investors | Multi\", "
        "\"why_it_matters\": \"Max 2 sentences.\", "
        "\"hook\": \"Max 20-word scroll-stopping line.\", "
        "\"li_post\": \"80–140 word post in Marmik’s tone: tension → insight → POV → question.\""
        "}]\n\n"
        f"ARTICLES:\n{json.dumps(articles[:100])}"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.25,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
    )

    raw = resp.choices[0].message.content
    try:
        parsed = json.loads(raw)
        return parsed[:6]
    except Exception:
        print("⚠️ Model output not valid JSON. Raw output:")
        print(raw)
        return []


# ======== EMAIL BUILDER ========

def build_email_html(curated):
    if not curated:
        return "<h3>No suitable articles this week. Maybe post a POV on martech ROI or AI use-cases?</h3>"
    rows = []
    for i, item in enumerate(curated, 1):
        rows.append(f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #ddd;">
            <strong>{i}. {item.get('title','')}</strong><br>
            <a href="{item.get('url','')}">{item.get('url','')}</a><br>
            <em>Audience:</em> {item.get('primary_audience','')}<br>
            <em>Why it matters:</em> {item.get('why_it_matters','')}<br><br>
            <strong>Hook:</strong> {item.get('hook','')}<br><br>
            <strong>Draft Post:</strong><br>
            {item.get('li_post','')}
          </td>
        </tr>
        """)
    return f"""
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">
        <h2>Sunday LinkedIn Content Pack</h2>
        <p>Curated from 20+ business, marketing & AI sources. Tailored for your C-level network.</p>
        <table width="100%" cellspacing="0" cellpadding="0">
          {''.join(rows)}
        </table>
      </body>
    </html>
    """


# ======== SEND MAIL ========

def send_email(subject, html_body):
    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    print(f"✅ Email sent to {RECIPIENT_EMAIL} via {SMTP_HOST}")


# ======== MAIN ========

def main():
    if ENFORCE_SYDNEY_21H:
        now_syd = datetime.now(ZoneInfo("Australia/Sydney"))
        if now_syd.hour != 21:
            print(f"⏭️ Skipping run (Sydney time: {now_syd.isoformat()}) – not 21:00.")
            return

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials missing")

    primary = fetch_recent_articles(RSS_FEEDS)
    curated = select_and_enrich_articles(primary)

    # fallback if fewer than 6 curated
    if len(curated) < 5:
        print("⚠️ Fewer than 5 curated results – fetching backup feeds.")
        backup = fetch_recent_articles(BACKUP_FEEDS)
        combined = primary + backup
        curated = select_and_enrich_articles(combined)

    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)


if __name__ == "__main__":
    main()

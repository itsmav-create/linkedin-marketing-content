# ai.digest.py
# Sunday LinkedIn Content Pack for Marmik Vyas
# Curates 5–6 executive-level insights weekly (AI, marketing, GTM, business strategy)
# Sources: 20+ RSS feeds with automatic backups if fewer than 6 valid picks
# Runs weekly (guarded for 21:00 Sydney on schedule), delivered by SendGrid API via GitHub Actions

import os
import json
from email.utils import formatdate
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
from dateutil import parser as dateparser
from openai import OpenAI
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ======== ENVIRONMENT CONFIG ========

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("MARKET_DIGEST_FROM")      # verified sender (e.g., itsmav@gmail.com)
RECIPIENT_EMAIL = os.getenv("LI_CONTENT_EMAIL")    # destination (your Yahoo)
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))
# Only enforce the 9pm Sydney guard on scheduled runs; the workflow sets this false for manual runs
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
    "https://www.fastcompany.com/rss",
    "https://techcrunch.com/feed/",
    "https://www.cmo.com/rss",
    "https://www.socialmediatoday.com/rss.xml",
    "https://www.forbes.com/leadership/feed/",
    "https://fortune.com/feed/",
    "https://www.inc.com/rss",
    "https://www.marketingdive.com/feeds/news/",
    "https://www.smartcompany.com.au/feed/",
    "https://medium.com/feed/@briansolis",
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

            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": (entry.get("summary", "") or "")[:600],
                "published": pub_date.isoformat(),
                "source": feed.get("feed", {}).get("title", feed_url)
            })

    # Deduplicate by URL and sort newest first
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

    system_prompt = (
        "You are the AI Content Strategist for Marmik Vyas — a senior marketing and commercial leader "
        "(ex-Ogilvy, Dell, Lenovo, nbn, ALAT). Your job is to find thought-worthy business and marketing ideas "
        "for LinkedIn posts that appeal to senior leaders.\n\n"
        "Audience:\n"
        "- CEOs, CMOs, Growth Heads, Product/Digital/CX leaders, Investors.\n"
        "Focus on:\n"
        "- AI in marketing & business transformation\n"
        "- Marketing effectiveness & GTM strategy\n"
        "- Customer experience, retention, performance loops\n"
        "- Org design, leadership, and transformation insights.\n\n"
        "Exclude: basic how-tos, shallow AI hype, tools lists, clickbait. "
        "Tone: edgy but professional, insight-rich, commercial, concise."
    )

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
    sent_date = formatdate(localtime=True)
    return f"""
    <html>
      <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">
        <h2>Sunday LinkedIn Content Pack</h2>
        <p style="color:#555;margin-top:-6px;">Generated {sent_date}</p>
        <p>Curated from 20+ business, marketing & AI sources. Tailored for your C-level network.</p>
        <table width="100%" cellspacing="0" cellpadding="0">
          {''.join(rows)}
        </table>
      </body>
    </html>
    """


# ======== SEND MAIL (SendGrid API) ========

def send_email(subject, html_body):
    if not SENDGRID_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY missing")
    if not FROM_EMAIL:
        raise RuntimeError("MARKET_DIGEST_FROM (verified sender) missing")
    if not RECIPIENT_EMAIL:
        raise RuntimeError("LI_CONTENT_EMAIL (recipient) missing")

    msg = Mail(
        from_email=FROM_EMAIL,
        to_emails=[e.strip() for e in RECIPIENT_EMAIL.split(",") if e.strip()],
        subject=subject,
        html_content=html_body
    )
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    resp = sg.send(msg)
    print(f"SENDGRID_STATUS {resp.status_code} to={RECIPIENT_EMAIL}")


# ======== MAIN ========

def main():
    # Guard: only enforce 21:00 Sydney for scheduled runs (workflow sets ENV accordingly)
    if ENFORCE_SYDNEY_21H:
        now_syd = datetime.now(ZoneInfo("Australia/Sydney"))
        if now_syd.hour != 21:
            print(f"⏭️ Skipping run (Sydney time: {now_syd.isoformat()}) – not 21:00.")
            return

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    primary = fetch_recent_articles(RSS_FEEDS)
    curated = select_and_enrich_articles(primary)

    if len(curated) < 5:
        print("⚠️ Fewer than 5 curated results – fetching backup feeds.")
        backup = fetch_recent_articles(BACKUP_FEEDS)
        combined = primary + backup
        curated = select_and_enrich_articles(combined)

    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)


if __name__ == "__main__":
    main()

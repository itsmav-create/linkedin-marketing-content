# ai.digest.py
# Sunday LinkedIn Content Pack for Marmik Vyas
# Curates 5–6 executive-level insights (AI, marketing, GTM, leadership)
# Uses RSS + OpenAI + SendGrid API. Optimized for speed with timeouts, caps, and diagnostics.

import os
import json
import time
from email.utils import formatdate
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import feedparser
from dateutil import parser as dateparser
from openai import OpenAI
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ======== ENV / KNOBS ========

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

FROM_EMAIL = os.getenv("MARKET_DIGEST_FROM")        # verified SendGrid sender (e.g., itsmav@gmail.com)
RECIPIENT_EMAIL = os.getenv("LI_CONTENT_EMAIL")      # destination (e.g., your Yahoo)

# Lookback & guard
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))        # change via workflow env
ENFORCE_SYDNEY_21H = os.getenv("ENFORCE_SYDNEY_21H", "true").lower() in ("1", "true", "yes")

# Performance controls (override via workflow env if needed)
FEED_HTTP_TIMEOUT = int(os.getenv("FEED_HTTP_TIMEOUT", "8"))   # seconds per HTTP request
MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "15"))
MAX_ARTICLES_TOTAL = int(os.getenv("MAX_ARTICLES_TOTAL", "120"))
MAX_TO_MODEL = int(os.getenv("MAX_TO_MODEL", "80"))
PREFER_RECENT_DAYS = int(os.getenv("PREFER_RECENT_DAYS", "45"))
TIMEBOX_SECONDS = int(os.getenv("TIMEBOX_SECONDS", "120"))     # overall fetch budget

# Diagnostics
DIAGNOSTICS = os.getenv("DIAGNOSTICS", "0") in ("1", "true", "True", "yes")
IGNORE_CUTOFF = os.getenv("IGNORE_CUTOFF", "0") in ("1", "true", "True", "yes")

# OpenAI
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
client = OpenAI(api_key=OPENAI_API_KEY)

# ======== FEEDS ========

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

# ======== FETCH (fast & instrumented) ========

def fetch_recent_articles(feeds):
    start_ts = time.time()
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    articles = []

    headers = {"User-Agent": "AI-Digest/1.0 (+https://github.com/marmik)"}

    for feed_url in feeds:
        if time.time() - start_ts > TIMEBOX_SECONDS:
            print(f"⏱️ Timebox reached ({TIMEBOX_SECONDS}s). Stopping feed fetch.")
            break

        try:
            resp = requests.get(feed_url, headers=headers, timeout=FEED_HTTP_TIMEOUT)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"⚠️ Fetch/parse error {feed_url}: {e}")
            continue

        entries = getattr(feed, "entries", []) or []
        if DIAGNOSTICS:
            print(f"🔎 Feed ok: {feed_url} → entries={len(entries)}")

        if not entries:
            print(f"ℹ️ Empty feed: {feed_url}")
            continue

        kept_here = 0
        for entry in entries[:MAX_ENTRIES_PER_FEED]:
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

            if not IGNORE_CUTOFF and pub_date < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": (entry.get("summary", "") or "")[:280],  # shorter = fewer tokens
                "published": pub_date.isoformat(),
                "source": feed.get("feed", {}).get("title", feed_url)
            })
            kept_here += 1
            if DIAGNOSTICS and kept_here <= 3:
                print(f"   • KEEP: {title[:100]}")

        if DIAGNOSTICS:
            print(f"   → kept {kept_here} (LOOKBACK_DAYS={LOOKBACK_DAYS}, IGNORE_CUTOFF={IGNORE_CUTOFF})")

        if len(articles) >= MAX_ARTICLES_TOTAL:
            print(f"🔪 Reached MAX_ARTICLES_TOTAL={MAX_ARTICLES_TOTAL}.")
            break

    # Deduplicate (by URL) & sort newest first
    seen = set()
    deduped = []
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if a["url"] not in seen:
            seen.add(a["url"])
            deduped.append(a)

    # Prefer recent, then older, cap total passed to model
    prefer_cut = datetime.now(timezone.utc) - timedelta(days=PREFER_RECENT_DAYS)
    recent = [a for a in deduped if dateparser.parse(a["published"]) >= prefer_cut]
    older  = [a for a in deduped if dateparser.parse(a["published"]) <  prefer_cut]
    shortlisted = (recent[:MAX_TO_MODEL] if len(recent) >= 5 else (recent + older))[:MAX_TO_MODEL]

    print(f"📦 Totals: fetched={len(articles)} deduped={len(deduped)} shortlisted={len(shortlisted)}")
    return shortlisted

# ======== CURATE VIA OPENAI (with fallback) ========

def select_and_enrich_articles(articles):
    if not articles:
        return []

    system_prompt = (
        "You are the AI Content Strategist for Marmik Vyas — a senior marketing and commercial leader "
        "(ex-Ogilvy, Dell, Lenovo, nbn, ALAT). Find thought-worthy business & marketing ideas for LinkedIn "
        "that appeal to senior leaders.\n\n"
        "Audience: CEOs, CMOs, Growth, Product/Digital/CX leaders, Investors.\n"
        "Focus: AI in marketing & transformation; marketing effectiveness & GTM; CX/retention/performance loops; "
        "org design, leadership & change.\n"
        "Exclude: basic how-tos, shallow AI hype, tools lists, clickbait.\n"
        "Tone: edgy, professional, commercial, concise.\n"
        "Prefer pieces from the last 4–6 weeks for freshness, but include older if the strategic insight is exceptional."
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
        f"ARTICLES:\n{json.dumps(articles[:MAX_TO_MODEL])}"
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=0.25,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
        )
        raw = resp.choices[0].message.content
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return parsed[:6]
        raise ValueError("Parsed JSON empty or not a list")
    except Exception as e:
        print(f"⚠️ Model output not valid JSON or selection failed: {e}")
        # Fallback: ensure we always produce something
        fallback = []
        for a in articles[:6]:
            fallback.append({
                "title": a["title"],
                "url": a["url"],
                "published": a["published"],
                "primary_audience": "Multi",
                "why_it_matters": "High-signal piece for leaders.",
                "hook": a["title"][:80],
                "li_post": f"{a['title']} — worth a read. What’s your take on this trend?"
            })
        return fallback

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

# ======== SEND (SendGrid API) ========

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
    # Only enforce 21:00 Sydney on scheduled runs (workflow sets ENFORCE_SYDNEY_21H=false for manual)
    if ENFORCE_SYDNEY_21H:
        now_syd = datetime.now(ZoneInfo("Australia/Sydney"))
        if now_syd.hour != 21:
            print(f"⏭️ Skipping run (Sydney time: {now_syd.isoformat()}) – not 21:00.")
            return

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    # Primary
    primary = fetch_recent_articles(RSS_FEEDS)
    curated = select_and_enrich_articles(primary)

    # Fallback to backups if needed
    if len(curated) < 5:
        print("⚠️ Fewer than 5 curated results – fetching backup feeds.")
        backup = fetch_recent_articles(BACKUP_FEEDS)
        combined = primary + backup
        curated = select_and_enrich_articles(combined)

    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)

if __name__ == "__main__":
    main()

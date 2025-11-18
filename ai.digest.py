# -*- coding: utf-8 -*-
# ai.digest.py — Reddit-free version
# Sunday LinkedIn Content Pack for Marmik Vyas
# Curates 10 executive-level insights (AI, marketing, GTM, leadership)
# Uses RSS, OpenAI, and SendGrid.
# Default lookback: 90 days. Prefers last 45 days. Runs weekly via GitHub Actions.

import os
import json
from email.utils import formatdate
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

import requests
import feedparser
from dateutil import parser as dateparser

from openai import OpenAI
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ------------------------------------------------------------------------------
# Feeds (No Reddit)
# ------------------------------------------------------------------------------

RSS_FEEDS: List[str] = [
    # Strategy & Consulting
    "https://feeds.hbr.org/harvardbusiness",
    "https://sloanreview.mit.edu/feed/",
    "https://www.mckinsey.com/featured-insights/rss",
    "https://www.bain.com/insights/feed/",
    "https://www.bcg.com/feeds/publications",
    "https://www2.deloitte.com/insights/us/en/rss.html",
    "https://www.accenture.com/us-en/blogs/blogs-rss",

    # Marketing & Effectiveness
    "https://www.marketingweek.com/feed/",
    "https://www.thedrum.com/rss",
    "https://adage.com/rss.xml",
    "https://www.campaignlive.co.uk/rss",
    "https://www.warc.com/contents/rss",
    "https://feeds.feedburner.com/ThinkWithGoogle",
    "https://www.socialmediaexaminer.com/feed/",
    "https://martech.org/feed/",

    # Tech / AI / Product / Growth
    "https://openai.com/blog/rss.xml",
    "https://www.sequoiacap.com/article/rss/",
    "https://a16z.com/feed/",
    "https://www.stripe.com/blog/rss",
    "https://www.databricks.com/blog/feed",
    "https://www.snowflake.com/en/feed/",
]

# Backup feeds
BACKUP_FEEDS: List[str] = [
    "https://www.fastcompany.com/rss.xml",
    "https://techcrunch.com/feed/",
    "https://www.forbes.com/most-popular/feed/",
    "https://fortune.com/feed/",
    "https://www.inc.com/rss.xml",
    "https://www.marketingdive.com/feeds/news/",
    "https://www.smartcompany.com.au/feed/",
]

# ------------------------------------------------------------------------------
# Behaviour settings
# ------------------------------------------------------------------------------

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "90"))
PREFER_RECENT_DAYS = int(os.getenv("PREFER_RECENT_DAYS", "45"))
MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "15"))
MAX_ARTICLES_TOTAL = int(os.getenv("MAX_ARTICLES_TOTAL", "200"))
MAX_TO_MODEL = int(os.getenv("MAX_TO_MODEL", "80"))
CURATED_COUNT = int(os.getenv("CURATED_COUNT", "10"))

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

ENFORCE_SYDNEY_21H = os.getenv("ENFORCE_SYDNEY_21H", "true").lower() in ("1", "true", "yes")

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    if not url:
        return ""
    base = url.split("#")[0]
    if "?" in base:
        root, qs = base.split("?", 1)
        kept = []
        for p in qs.split("&"):
            if not p.lower().startswith("utm_"):
                kept.append(p)
        return root + ("?" + "&".join(kept) if kept else "")
    return base


def safe_get(url: str, timeout: int = 10):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Marmik-AI-Digest/1.0"})
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"⚠️ Error fetching {url}: {e}")
        return None


def parse_entry(entry: Any) -> Dict[str, Any]:
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    summary = entry.get("summary", "") or entry.get("description", "") or ""
    summary = summary.strip()

    published = None
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                published = dateparser.parse(raw)
                break
            except:
                pass

    if not published:
        published = datetime.now(timezone.utc)

    source = ""
    if link:
        try:
            host = link.split("//", 1)[1].split("/", 1)[0]
            source = host.replace("www.", "")
        except:
            source = ""

    return {
        "title": title,
        "link": link,
        "summary": summary,
        "published": published.isoformat(),
        "source": source,
    }


def fetch_recent_articles(feeds: List[str]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    articles = []

    for url in feeds:
        resp = safe_get(url)
        if not resp:
            continue
        parsed = feedparser.parse(resp.content)
        for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
            art = parse_entry(entry)
            try:
                dt = dateparser.parse(art["published"])
            except:
                dt = datetime.now(timezone.utc)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if dt >= cutoff:
                art["published_dt"] = dt
                articles.append(art)

    # Sort
    articles.sort(key=lambda a: a["published_dt"], reverse=True)

    # Dedupe
    seen = set()
    final = []
    for a in articles:
        key = (normalize_url(a["link"]), a["title"].lower())
        if key not in seen:
            seen.add(key)
            final.append(a)

    # Cap
    final = final[:MAX_ARTICLES_TOTAL]

    # Recent preference
    prefer_cut = datetime.now(timezone.utc) - timedelta(days=PREFER_RECENT_DAYS)
    recent = [a for a in final if a["published_dt"] >= prefer_cut]
    older = [a for a in final if a["published_dt"] < prefer_cut]

    shortlisted = (recent + older)[:MAX_TO_MODEL]
    print(f"📚 Articles: {len(articles)}, Unique: {len(final)}, Shortlisted: {len(shortlisted)}")

    return shortlisted

# ------------------------------------------------------------------------------
# OpenAI Curation
# ------------------------------------------------------------------------------

def select_and_enrich_articles(articles: List[Dict[str, Any]]):
    if not articles:
        return []

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    model_articles = [
        {
            "id": i,
            "title": a["title"],
            "link": a["link"],
            "summary": a["summary"],
            "published": a["published"],
            "source": a["source"],
        }
        for i, a in enumerate(articles)
    ]

    system_prompt = f"""
You are an editorial director curating a Sunday reading pack for a senior marketing & commercial leader.

Pick the TOP {CURATED_COUNT} articles that:
- are executive level
- are strategic, not tactical
- relate to: AI in marketing, GTM, measurement/ROI, leadership, performance
- prefer last 4–6 weeks unless exceptional
Return ONLY JSON list with:
title, link, source, published, one_sentence, why_it_matters, angle_for_linkedin
"""

    user_content = json.dumps(model_articles, ensure_ascii=False)

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.strip("`").replace("json\n", "").replace("json\r\n", "")

        curated = json.loads(raw)
        curated = curated[:CURATED_COUNT]
        print(f"✅ Model returned {len(curated)} items.")
        return curated

    except Exception as e:
        print(f"⚠️ OpenAI failed: {e}")

    # fallback
    fallback = []
    for a in articles[:CURATED_COUNT]:
        fallback.append({
            "title": a["title"],
            "link": a["link"],
            "source": a["source"],
            "published": a["published"],
            "one_sentence": a["summary"][:240],
            "why_it_matters": "Useful strategic insight.",
            "angle_for_linkedin": "Summarise the insight and add your perspective.",
        })
    return fallback

# ------------------------------------------------------------------------------
# Email Builder
# ------------------------------------------------------------------------------

def build_email_html(curated: List[Dict[str, Any]]):
    created = formatdate(localtime=True)
    rows = []

    for i, art in enumerate(curated, 1):
        rows.append(f"""
        <tr>
          <td style="padding:16px; border-bottom:1px solid #eee; font-family:Arial;">
            <div style="font-size:13px; color:#888;">#{i}</div>
            <div style="font-size:16px; font-weight:bold;">
              <a href="{art['link']}" style="color:#0366d6; text-decoration:none;">{art['title']}</a>
            </div>
            <div style="font-size:12px; color:#666;">
              {art['source']} • {art['published']}
            </div>
            <div><strong>Summary:</strong> {art['one_sentence']}</div>
            <div><strong>Why this matters:</strong> {art['why_it_matters']}</div>
            <div><strong>Angle for LinkedIn:</strong> {art['angle_for_linkedin']}</div>
          </td>
        </tr>
        """)

    rows_html = "\n".join(rows)

    return f"""
    <html><body>
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center">
          <table width="720" cellpadding="0" cellspacing="0" style="background:#fff;">
            <tr><td style="padding:20px; font-family:Arial;">
              <h2>Sunday LinkedIn Content Pack</h2>
              <div style="font-size:12px; color:#666;">Generated {created}</div>
            </td></tr>
            {rows_html}
          </table>
        </td>
      </tr>
    </table>
    </body></html>
    """

# ------------------------------------------------------------------------------
# SendGrid mailer
# ------------------------------------------------------------------------------

def send_email(subject: str, html: str):
    api_key = os.getenv("SENDGRID_API_KEY")
    sender = os.getenv("MARKET_DIGEST_FROM")
    recipient = os.getenv("LI_CONTENT_EMAIL")

    if not api_key or not sender or not recipient:
        print("❌ Missing SENDGRID_API_KEY / FROM / TO")
        return

    try:
        sg = SendGridAPIClient(api_key)
        msg = Mail(from_email=sender, to_emails=recipient, subject=subject, html_content=html)
        resp = sg.send(msg)
        print(f"✉️ Email sent: {resp.status_code}")
    except Exception as e:
        print(f"❌ SendGrid error: {e}")

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

def main():
    if ENFORCE_SYDNEY_21H:
        now = datetime.now(ZoneInfo("Australia/Sydney"))
        if now.hour != 21:
            print(f"⏭️ Skipping – Sydney time is {now.isoformat()}, not 21:00")
            return

    primary = fetch_recent_articles(RSS_FEEDS)
    curated = select_and_enrich_articles(primary)

    if len(curated) < max(3, CURATED_COUNT // 2):
        print("⚠️ Too few items — using backup feeds")
        backup = fetch_recent_articles(BACKUP_FEEDS)
        merged = primary + backup

        # dedupe again
        seen = set()
        uniq = []
        for a in merged:
            k = (normalize_url(a["link"]), a["title"].lower())
            if k not in seen:
                seen.add(k)
                uniq.append(a)

        curated = select_and_enrich_articles(uniq)

    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)


if __name__ == "__main__":
    main()

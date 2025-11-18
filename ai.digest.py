# -*- coding: utf-8 -*-
# ai.digest.py
# Sunday LinkedIn Content Pack for Marmik Vyas
# Curates 10 executive-level insights (AI, marketing, GTM, leadership)
# Uses RSS (including Reddit), OpenAI, and SendGrid.
# Default lookback: 90 days. Prefers last 45 days. Runs weekly via GitHub Actions.

import os
import json
import time
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
# Configuration
# ------------------------------------------------------------------------------

# Primary high-signal feeds (strategy, AI, marketing, GTM)
RSS_FEEDS: List[str] = [
    # Strategy / leadership / consulting
    "https://feeds.hbr.org/harvardbusiness",
    "https://sloanreview.mit.edu/feed/",
    "https://www.mckinsey.com/featured-insights/rss",
    "https://www.bain.com/insights/feed/",
    "https://www.bcg.com/feeds/publications",
    "https://www2.deloitte.com/insights/us/en/rss.html",
    "https://www.accenture.com/us-en/blogs/blogs-rss",

    # Marketing / advertising / effectiveness
    "https://www.marketingweek.com/feed/",
    "https://www.thedrum.com/rss",
    "https://adage.com/rss.xml",
    "https://www.campaignlive.co.uk/rss",
    "https://www.warc.com/contents/rss",
    "https://feeds.feedburner.com/ThinkWithGoogle",
    "https://www.socialmediaexaminer.com/feed/",
    "https://martech.org/feed/",

    # Tech, AI, product & growth
    "https://openai.com/blog/rss.xml",
    "https://www.sequoiacap.com/article/rss/",
    "https://a16z.com/feed/",
    "https://www.stripe.com/blog/rss",
    "https://www.databricks.com/blog/feed",
    "https://www.snowflake.com/en/feed/",
]

# Reddit aggregators (curated by the model for relevance)
REDDIT_FEEDS: List[str] = [
    "https://www.reddit.com/r/marketing/.rss",
    "https://www.reddit.com/r/digital_marketing/.rss",
    "https://www.reddit.com/r/MachineLearning/.rss",
    "https://www.reddit.com/r/Entrepreneur/.rss",
    "https://www.reddit.com/r/startups/.rss",
    "https://www.reddit.com/r/business/.rss",
]

# Backup / long-tail feeds in case primary feeds are too sparse
BACKUP_FEEDS: List[str] = [
    "https://www.fastcompany.com/rss.xml",
    "https://techcrunch.com/feed/",
    "https://www.forbes.com/most-popular/feed/",
    "https://fortune.com/feed/",
    "https://www.inc.com/rss.xml",
    "https://www.marketingdive.com/feeds/news/",
    "https://www.smartcompany.com.au/feed/",
]

# Behavioural settings via env
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
    """Rough normalization to help dedupe URLs."""
    if not url:
        return ""
    # Strip URL fragments and simple tracking params
    base = url.split("#")[0]
    # Kill obvious utm_* query params while keeping others
    if "?" in base:
        root, qs = base.split("?", 1)
        kept_params = []
        for part in qs.split("&"):
            if part.lower().startswith("utm_"):
                continue
            kept_params.append(part)
        if kept_params:
            return root + "?" + "&".join(kept_params)
        return root
    return base


def safe_get(url: str, timeout: int = 10) -> requests.Response | None:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Marmik-AI-Digest/1.0"})
        resp.raise_for_status()
        return resp
    except Exception as e:
        print(f"⚠️ Error fetching {url}: {e}")
        return None


def parse_entry(entry: Any) -> Dict[str, Any]:
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()

    # Try to resolve a publication date
    published = None
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                published = dateparser.parse(raw)
                break
            except Exception:
                continue

    if not published:
        # Fall back to current UTC time if missing
        published = datetime.now(timezone.utc)

    summary = entry.get("summary", "") or entry.get("description", "") or ""
    summary = summary.strip()

    source = ""
    if "source" in entry and isinstance(entry["source"], dict):
        source = entry["source"].get("title") or ""
    if not source and "feedburner_origlink" in entry:
        source = "Feedburner"

    # Rough guess at source from link
    if not source and link:
        try:
            host = link.split("//", 1)[1].split("/", 1)[0]
            source = host.replace("www.", "")
        except Exception:
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
    articles: List[Dict[str, Any]] = []

    for url in feeds:
        resp = safe_get(url)
        if not resp:
            continue
        parsed = feedparser.parse(resp.content)
        entries = parsed.entries[:MAX_ENTRIES_PER_FEED]

        for entry in entries:
            art = parse_entry(entry)
            try:
                pub_dt = dateparser.parse(art["published"])
            except Exception:
                pub_dt = datetime.now(timezone.utc)

            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)

            if pub_dt < cutoff:
                continue

            art["published_dt"] = pub_dt
            articles.append(art)

    # Sort by recency
    articles.sort(key=lambda a: a["published_dt"], reverse=True)

    # Dedupe across all feeds by normalized link + lowercased title
    seen_keys = set()
    deduped: List[Dict[str, Any]] = []
    for art in articles:
        key = (normalize_url(art["link"]), art["title"].strip().lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(art)

    # Trim hard cap
    if len(deduped) > MAX_ARTICLES_TOTAL:
        deduped = deduped[:MAX_ARTICLES_TOTAL]

    # Prefer the last PREFER_RECENT_DAYS where possible
    prefer_cut = datetime.now(timezone.utc) - timedelta(days=PREFER_RECENT_DAYS)
    recent = [a for a in deduped if a["published_dt"] >= prefer_cut]
    older = [a for a in deduped if a["published_dt"] < prefer_cut]

    shortlisted = (recent + older)[:MAX_TO_MODEL]
    print(f"📚 Fetched {len(articles)} articles → {len(deduped)} unique → {len(shortlisted)} shortlisted.")
    return shortlisted


# ------------------------------------------------------------------------------
# OpenAI curation
# ------------------------------------------------------------------------------

def select_and_enrich_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not articles:
        return []

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Minimal projection for the model (no datetime objects)
    model_articles = [
        {
            "id": idx,
            "title": a["title"],
            "link": a["link"],
            "summary": a["summary"],
            "published": a["published"],
            "source": a["source"],
        }
        for idx, a in enumerate(articles)
    ]

    system_prompt = """
You are an editorial director curating a Sunday reading pack for a senior marketing and commercial leader
(Marmik Vyas) who specialises in:
- C-level marketing leadership (brand + performance + martech) across B2C and B2B
- AI in marketing, data-driven GTM, CDP/CRM, measurement and ROI
- Business strategy, turnaround, and growth in telco, tech, and services

From the supplied JSON list of articles (blogs, reports, Reddit discussions, etc.), pick the TOP {count} that:
- Offer EXECUTIVE-LEVEL insight (not basic how-tos or tool lists)
- Are actionable for marketing / growth / GTM / leadership decisions
- Balance: AI in marketing, GTM strategy, measurement/ROI, and broader leadership/strategy
- Prefer pieces from the last 4–6 weeks for freshness, but include older if the strategic insight is exceptional
- For Reddit threads: only include if they contain deep, concrete discussion (case studies, data, real experiments).
  Ignore memes, shallow Q&A, or generic motivational posts.

Return pure JSON ONLY, as a list of objects with:
- title
- link
- source
- published (as-is from input)
- one_sentence
- why_it_matters (2–3 sentences)
- angle_for_linkedin (a sharp, contrarian or practical angle Marmik could take in a LI post)
""".format(count=CURATED_COUNT)

    user_content = (
        "Here is the JSON array of candidate articles:\n\n"
        + json.dumps(model_articles, ensure_ascii=False)
        + "\n\nSelect and return only the JSON array as described."
    )

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
        # In case the model wraps JSON in markdown ```json ... ```
        if raw.startswith("```"):
            raw = raw.strip("`")
            # Remove leading json\n if present
            raw = raw.replace("json\n", "").replace("json\r\n", "")
        curated = json.loads(raw)
        if isinstance(curated, list):
            # Truncate defensively to CURATED_COUNT
            curated = curated[:CURATED_COUNT]
            print(f"✅ Model returned {len(curated)} curated articles.")
            return curated
    except Exception as e:
        print(f"⚠️ OpenAI curation failed: {e}")

    # Fallback: simple top-N by recency with minimal fields
    print("⚠️ Falling back to recency-based selection.")
    fallback = []
    for a in articles[:CURATED_COUNT]:
        fallback.append(
            {
                "title": a["title"],
                "link": a["link"],
                "source": a["source"],
                "published": a["published"],
                "one_sentence": a["summary"][:260] + ("..." if len(a["summary"]) > 260 else ""),
                "why_it_matters": "Useful recent piece on marketing, AI, GTM or leadership.",
                "angle_for_linkedin": "Share a concise summary, then add one sharp observation from your own experience.",
            }
        )
    return fallback


# ------------------------------------------------------------------------------
# Email construction
# ------------------------------------------------------------------------------

def build_email_html(curated: List[Dict[str, Any]]) -> str:
    created = formatdate(localtime=True)
    rows = []

    for idx, art in enumerate(curated, start=1):
        title = art.get("title", "Untitled")
        link = art.get("link", "#")
        source = art.get("source", "")
        published = art.get("published", "")
        one_sentence = art.get("one_sentence", "")
        why = art.get("why_it_matters", "")
        angle = art.get("angle_for_linkedin", "")

        row = f"""
        <tr>
          <td style="padding:16px; border-bottom:1px solid #eee; font-family:Arial, sans-serif; font-size:14px; line-height:1.5;">
            <div style="font-size:13px; color:#888;">#{idx}</div>
            <div style="font-size:16px; font-weight:bold; margin:4px 0;">
              <a href="{link}" style="color:#0366d6; text-decoration:none;">{title}</a>
            </div>
            <div style="font-size:12px; color:#666; margin-bottom:6px;">
              {source} &middot; {published}
            </div>
            <div style="margin-bottom:6px;"><strong>Summary:</strong> {one_sentence}</div>
            <div style="margin-bottom:6px;"><strong>Why this matters:</strong> {why}</div>
            <div style="margin-bottom:0;"><strong>Angle for LinkedIn:</strong> {angle}</div>
          </td>
        </tr>
        """
        rows.append(row)

    rows_html = "\n".join(rows) if rows else """
        <tr>
          <td style="padding:16px; font-family:Arial, sans-serif;">
            No suitable articles found this week. Maybe take this as a sign to write a fresh POV post instead.
          </td>
        </tr>
    """

    html = f"""
    <html>
    <head>
      <meta charset="utf-8" />
      <title>Sunday LinkedIn Content Pack</title>
    </head>
    <body style="margin:0; padding:0; background-color:#f5f5f5;">
      <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
        <tr>
          <td align="center" style="padding:24px 12px;">
            <table cellpadding="0" cellspacing="0" width="100%" style="max-width:720px; background-color:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 0 0 1px rgba(0,0,0,0.05);">
              <tr>
                <td style="padding:20px 24px; border-bottom:1px solid #eee; font-family:Arial, sans-serif;">
                  <div style="font-size:20px; font-weight:bold; margin-bottom:4px;">
                    Sunday LinkedIn Content Pack
                  </div>
                  <div style="font-size:12px; color:#666;">
                    Curated for Marmik Vyas &middot; Generated {created}
                  </div>
                </td>
              </tr>
              {rows_html}
              <tr>
                <td style="padding:12px 24px 16px; border-top:1px solid #eee; font-family:Arial, sans-serif; font-size:11px; color:#999;">
                  Tip: When you share any of these on LinkedIn, lead with a punchy takeaway and
                  then paste the link in the first comment if you want to maximise dwell and discussion.
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
    return html


# ------------------------------------------------------------------------------
# SendGrid mailer
# ------------------------------------------------------------------------------

def send_email(subject: str, html_content: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("MARKET_DIGEST_FROM")
    to_email = os.getenv("LI_CONTENT_EMAIL")

    if not api_key or not from_email or not to_email:
        print("❌ SENDGRID_API_KEY, MARKET_DIGEST_FROM or LI_CONTENT_EMAIL missing – not sending email.")
        return

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        html_content=html_content,
    )

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✉️ Email sent: status={response.status_code}")
    except Exception as e:
        print(f"❌ Error sending email via SendGrid: {e}")


# ------------------------------------------------------------------------------
# Main orchestration
# ------------------------------------------------------------------------------

def main() -> None:
    # Time guard: only send at 21:00 Sydney time if enforced
    if ENFORCE_SYDNEY_21H:
        now_syd = datetime.now(ZoneInfo("Australia/Sydney"))
        if now_syd.hour != 21:
            print(f"⏭️ Skipping run – Sydney time is {now_syd.isoformat()}, not 21:00.")
            return

    # Fetch from primary + Reddit feeds
    primary_articles = fetch_recent_articles(RSS_FEEDS + REDDIT_FEEDS)

    curated = select_and_enrich_articles(primary_articles)

    # If we ended up with very few curated (e.g., < half of target), try backup feeds too
    if len(curated) < max(3, CURATED_COUNT // 2):
        print("⚠️ Fewer curated articles than expected – fetching backup feeds.")
        backup_articles = fetch_recent_articles(BACKUP_FEEDS)
        combined = primary_articles + backup_articles

        # Dedupe again before sending to model
        seen_keys = set()
        uniq_combined = []
        for a in combined:
            key = (normalize_url(a["link"]), a["title"].strip().lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            uniq_combined.append(a)

        curated = select_and_enrich_articles(uniq_combined)

    html = build_email_html(curated)
    send_email("Marmik | Sunday LinkedIn Content Pack", html)


if __name__ == "__main__":
    main()

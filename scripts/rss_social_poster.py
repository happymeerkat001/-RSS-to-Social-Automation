#!/usr/bin/env python3
"""RSS-to-social automation for ContentStudio queued posts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from time import mktime
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.socialmediatoday.com/feeds/news/",
]

DEFAULT_DB_PATH = Path.home() / ".rss_social_poster" / "posted.db"
CONTENTSTUDIO_API_BASE = "https://api.contentstudio.io/api/v1"
CLAUDE_MODEL = "claude-sonnet-4-20250514"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    summary: str
    published_at: datetime
    feed_url: str


def log(event: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    print(json.dumps(record, sort_keys=True), flush=True)


def strip_html(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value or "")
    text = unescape(parser.text())
    return re.sub(r"\s+", " ", text).strip()


def entry_datetime(entry: Any) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)

    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if not value:
            continue
        try:
            parsed_dt = parsedate_to_datetime(value)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            return parsed_dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass

    return datetime.now(timezone.utc)


def entry_url(entry: Any) -> str | None:
    if entry.get("link"):
        return str(entry["link"]).strip()

    links = entry.get("links") or []
    for link in links:
        href = link.get("href")
        if href:
            return str(href).strip()

    return None


def parse_feeds(feed_urls: list[str]) -> list[Article]:
    try:
        import feedparser
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: feedparser. Install dependencies with "
            "`python3 -m pip install -r requirements.txt`."
        ) from exc

    articles: list[Article] = []

    for feed_url in feed_urls:
        parsed = feedparser.parse(feed_url)
        status = getattr(parsed, "status", None)
        if parsed.bozo:
            log("feed_parse_warning", feed_url=feed_url, error=str(parsed.bozo_exception))

        for entry in parsed.entries:
            url = entry_url(entry)
            title = strip_html(entry.get("title", ""))
            if not url or not title:
                continue

            summary = strip_html(
                entry.get("summary")
                or entry.get("description")
                or entry.get("subtitle")
                or ""
            )
            articles.append(
                Article(
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=entry_datetime(entry),
                    feed_url=feed_url,
                )
            )

        log("feed_parsed", feed_url=feed_url, status=status, entries=len(parsed.entries))

    articles.sort(key=lambda article: article.published_at, reverse=True)
    return articles


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posted (
            url TEXT PRIMARY KEY,
            title TEXT,
            posted_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def is_posted(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM posted WHERE url = ?", (url,)).fetchone()
    return row is not None


def mark_posted(conn: sqlite3.Connection, article: Article) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO posted (url, title, posted_at) VALUES (?, ?, ?)",
        (article.url, article.title, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def first_unposted(conn: sqlite3.Connection, articles: list[Article]) -> Article | None:
    for article in articles:
        if not is_posted(conn, article.url):
            return article
    return None


def generate_caption(article: Article, api_key: str) -> str:
    try:
        import anthropic
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: anthropic. Install dependencies with "
            "`python3 -m pip install -r requirements.txt`."
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""
Write a concise B2B agency-style social caption for LinkedIn, Facebook, and Instagram.

Requirements:
- 2 short paragraphs max
- Professional, useful, and clear
- No hype, no emojis
- Include 2-3 relevant hashtags
- Do not include the article URL
- Return only the caption text

Article title:
{article.title}

Article summary:
{article.summary or "No summary provided."}
""".strip()

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=350,
        temperature=0.6,
        messages=[{"role": "user", "content": prompt}],
    )

    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)

    caption = "\n".join(parts).strip()
    if not caption:
        raise RuntimeError("Claude returned an empty caption")
    return caption


def publish_to_contentstudio(
    caption: str,
    article_url: str,
    api_key: str,
    workspace_id: str,
    account_ids: list[str],
) -> dict[str, Any]:
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: requests. Install dependencies with "
            "`python3 -m pip install -r requirements.txt`."
        ) from exc

    endpoint = f"{CONTENTSTUDIO_API_BASE}/workspaces/{workspace_id}/posts"
    payload = {
        "content": {"text": f"{caption}\n\n{article_url}"},
        "accounts": account_ids,
        "scheduling": {"publish_type": "queued"},
    }
    response = requests.post(
        endpoint,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )

    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text}

    if not response.ok:
        raise RuntimeError(
            f"ContentStudio POST failed with HTTP {response.status_code}: {body}"
        )

    return body


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_account_ids(value: str) -> list[str]:
    account_ids = [part.strip() for part in value.split(",") if part.strip()]
    if not account_ids:
        raise RuntimeError("CONTENTSTUDIO_ACCOUNT_IDS must contain at least one account ID")
    return account_ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post the newest unposted RSS article to the ContentStudio queue."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse feeds and generate a caption, but skip ContentStudio publish and DB insert.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite DB path. Default: {DEFAULT_DB_PATH}",
    )
    return parser.parse_args()


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args()

    try:
        anthropic_api_key = require_env("ANTHROPIC_API_KEY")
        contentstudio_api_key = os.getenv("CONTENTSTUDIO_API_KEY")
        contentstudio_workspace_id = os.getenv("CONTENTSTUDIO_WORKSPACE_ID")
        contentstudio_account_ids = os.getenv("CONTENTSTUDIO_ACCOUNT_IDS")

        if not args.dry_run:
            contentstudio_api_key = require_env("CONTENTSTUDIO_API_KEY")
            contentstudio_workspace_id = require_env("CONTENTSTUDIO_WORKSPACE_ID")
            contentstudio_account_ids = require_env("CONTENTSTUDIO_ACCOUNT_IDS")

        account_ids = (
            parse_account_ids(contentstudio_account_ids)
            if contentstudio_account_ids
            else []
        )

        conn = connect_db(args.db_path)
        articles = parse_feeds(FEEDS)
        log("articles_collected", count=len(articles))

        article = first_unposted(conn, articles)
        if article is None:
            log("no_unposted_articles")
            return 0

        log(
            "article_selected",
            title=article.title,
            url=article.url,
            published_at=article.published_at.isoformat(),
            feed_url=article.feed_url,
        )

        caption = generate_caption(article, anthropic_api_key)
        log("caption_generated", chars=len(caption))

        if args.dry_run:
            print("\n--- DRY RUN ---")
            print(caption)
            print()
            print(article.url)
            return 0

        result = publish_to_contentstudio(
            caption=caption,
            article_url=article.url,
            api_key=contentstudio_api_key or "",
            workspace_id=contentstudio_workspace_id or "",
            account_ids=account_ids,
        )
        mark_posted(conn, article)
        log("contentstudio_queued", url=article.url, response=result)
        return 0

    except Exception as exc:
        log("error", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())

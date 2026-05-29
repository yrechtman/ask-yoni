"""Poll the Substack RSS feed, ingest any new posts (with section splitting).

Idempotent. Matches on slug. New posts get their slug as post_id (initial
backfill used the numeric Substack ID; both are valid TEXT primary keys).

Each new post is inserted into substack_posts, then handed to
app.sections.process_post() to do the section split + chunk + embed.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import tiktoken
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from markdownify import MarkdownConverter
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.sections import process_post  # noqa: E402

RSS_URL = "https://99d.substack.com/feed"
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


class _CleanConverter(MarkdownConverter):
    def convert_img(self, el, text, parent_tags): return ""
    def convert_figure(self, el, text, parent_tags): return ""
    def convert_picture(self, el, text, parent_tags): return ""


def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "svg", "button"]):
        tag.decompose()
    md = _CleanConverter(heading_style="ATX", bullets="-", strip=["img", "figure", "picture"]).convert_soup(soup)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        slug = urlparse(link).path.rsplit("/", 1)[-1]
        if not slug:
            continue
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "subtitle": (item.findtext("description") or "").strip() or None,
            "slug": slug,
            "url": link,
            "published_at": parsedate_to_datetime(item.findtext("pubDate")),
            "content_html": item.findtext("content:encoded", default="", namespaces=NS),
        })
    return items


def existing_slugs(cur: psycopg.Cursor) -> set[str]:
    cur.execute("SELECT slug FROM substack_posts")
    return {row[0] for row in cur.fetchall()}


def _start_run(dsn: str) -> int | None:
    """Insert a substack_sync_runs row, return its id. Best-effort — None on failure."""
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO substack_sync_runs (status) VALUES ('running') RETURNING id")
            run_id = cur.fetchone()[0]
            conn.commit()
            return run_id
    except Exception as e:
        print(f"warn: could not record sync_run start ({e!r})")
        return None


def _finish_run(dsn: str, run_id: int | None, status: str, new_posts: int, note: str | None) -> None:
    if run_id is None:
        return
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE substack_sync_runs
                SET finished_at = NOW(), status = %s, new_posts = %s, note = %s
                WHERE id = %s
                """,
                (status, new_posts, note, run_id),
            )
            conn.commit()
    except Exception as e:
        print(f"warn: could not record sync_run finish ({e!r})")


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set")
    dsn = os.environ["DATABASE_URL"]

    run_id = _start_run(dsn)
    new_count = 0
    failed_slugs: list[str] = []

    try:
        print(f"fetching {RSS_URL}")
        req = urllib.request.Request(RSS_URL, headers={"User-Agent": "ask-yoni-sync/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            feed_bytes = r.read()

        feed_items = parse_feed(feed_bytes)
        print(f"feed: {len(feed_items)} items")

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            have = existing_slugs(cur)
        new_items = [it for it in feed_items if it["slug"] not in have]
        if not new_items:
            print("up to date")
            _finish_run(dsn, run_id, "ok", 0, None)
            return

        client = OpenAI()
        enc = tiktoken.get_encoding("cl100k_base")

        print(f"new: {len(new_items)} posts -> processing")
        for item in new_items:
            body_md = html_to_markdown(item["content_html"])
            if not body_md or len(body_md) < 50:
                print(f"  skip {item['slug']}: empty body")
                continue

            word_count = len(body_md.split())

            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO substack_posts
                            (post_id, slug, url, title, subtitle, published_at, audience, body_md, word_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE SET
                            title        = EXCLUDED.title,
                            subtitle     = EXCLUDED.subtitle,
                            published_at = EXCLUDED.published_at,
                            body_md      = EXCLUDED.body_md,
                            word_count   = EXCLUDED.word_count
                        RETURNING post_id
                        """,
                        (item["slug"], item["slug"], item["url"], item["title"],
                         item["subtitle"], item["published_at"], "everyone", body_md, word_count),
                    )
                    post_id = cur.fetchone()[0]

                    n_sec, n_chunks = process_post(cur, client, enc, {
                        "post_id": post_id,
                        "slug": item["slug"],
                        "title": item["title"],
                        "subtitle": item["subtitle"],
                        "body_md": body_md,
                    })
                    conn.commit()
                    new_count += 1
                    print(f"  + {item['slug']}: {word_count} words, {n_sec} sections, {n_chunks} chunks")
                except Exception as e:
                    conn.rollback()
                    failed_slugs.append(item["slug"])
                    print(f"  ! {item['slug']}: FAILED ({e!r})")

        status = "failed" if failed_slugs and new_count == 0 else "ok"
        note = f"failed: {','.join(failed_slugs)}" if failed_slugs else None
        _finish_run(dsn, run_id, status, new_count, note)
    except Exception as e:
        _finish_run(dsn, run_id, "failed", new_count, repr(e)[:500])
        raise


if __name__ == "__main__":
    main()

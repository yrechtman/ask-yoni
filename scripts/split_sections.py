"""Backfill: split posts into sections + chunks for every row in substack_posts.

Idempotent. Defaults to all posts. Set ONLY_SLUG=foo to process a single post,
or ONLY_MISSING=1 to skip posts that already have sections (useful after a
partial run).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.sections import process_post  # noqa: E402


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set")

    only_slug = os.getenv("ONLY_SLUG")
    only_missing = os.getenv("ONLY_MISSING") == "1"
    client = OpenAI()
    enc = tiktoken.get_encoding("cl100k_base")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            if only_slug:
                cur.execute(
                    "SELECT post_id, slug, title, subtitle, body_md FROM substack_posts WHERE slug = %s",
                    (only_slug,),
                )
            elif only_missing:
                cur.execute(
                    """
                    SELECT p.post_id, p.slug, p.title, p.subtitle, p.body_md
                    FROM substack_posts p
                    LEFT JOIN substack_sections sec ON sec.post_id = p.post_id
                    WHERE sec.id IS NULL
                    ORDER BY p.published_at
                    """
                )
            else:
                cur.execute(
                    "SELECT post_id, slug, title, subtitle, body_md FROM substack_posts ORDER BY published_at"
                )
            posts = [
                {"post_id": r[0], "slug": r[1], "title": r[2], "subtitle": r[3], "body_md": r[4]}
                for r in cur.fetchall()
            ]

        print(f"processing {len(posts)} posts")
        total_sections = 0
        total_chunks = 0
        for i, post in enumerate(posts, 1):
            with conn.cursor() as cur:
                try:
                    n_sec, n_chunks = process_post(cur, client, enc, post)
                    conn.commit()
                    total_sections += n_sec
                    total_chunks += n_chunks
                    print(f"  [{i}/{len(posts)}] {post['slug']}: {n_sec} sections, {n_chunks} chunks")
                except Exception as e:
                    conn.rollback()
                    print(f"  [{i}/{len(posts)}] {post['slug']}: FAILED ({e!r})")

        print(f"\ntotal: {total_sections} sections, {total_chunks} chunks")


if __name__ == "__main__":
    main()

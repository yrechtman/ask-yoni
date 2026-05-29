"""Upsert the local JSONL corpus into Postgres.

Reads posts.jsonl + chunks.jsonl, upserts into substack_posts and substack_chunks.
Safe to re-run; ON CONFLICT updates content but preserves indexed_at on existing rows.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb  # noqa: F401  (pulls in psycopg extras)

ROOT = Path(__file__).resolve().parent.parent
POSTS_FILE = ROOT / "data" / "posts.jsonl"
CHUNKS_FILE = ROOT / "data" / "chunks.jsonl"


def vector_literal(v: list[float]) -> str:
    """pgvector accepts a string like '[1.0,2.0,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def main() -> None:
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set; check .env")
    if not POSTS_FILE.exists() or not CHUNKS_FILE.exists():
        raise SystemExit("missing posts.jsonl or chunks.jsonl; run ingest.py + embed.py first")

    posts = [json.loads(line) for line in POSTS_FILE.open()]
    chunks = [json.loads(line) for line in CHUNKS_FILE.open()]
    print(f"loading {len(posts)} posts, {len(chunks)} chunks -> Postgres")

    post_rows = [
        (
            p["post_id"], p["slug"], p["url"], p["title"], p.get("subtitle") or None,
            p["date"], p.get("audience") or None, p["body_md"], p["word_count"],
        )
        for p in posts
    ]
    chunk_rows = [
        (c["post_id"], c["chunk_idx"], c["text"], c["token_count"], vector_literal(c["embedding"]))
        for c in chunks
    ]
    affected_post_ids = list({c["post_id"] for c in chunks})

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO substack_posts
                (post_id, slug, url, title, subtitle, published_at, audience, body_md, word_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (post_id) DO UPDATE SET
                slug         = EXCLUDED.slug,
                url          = EXCLUDED.url,
                title        = EXCLUDED.title,
                subtitle     = EXCLUDED.subtitle,
                published_at = EXCLUDED.published_at,
                audience     = EXCLUDED.audience,
                body_md      = EXCLUDED.body_md,
                word_count   = EXCLUDED.word_count
            """,
            post_rows,
        )
        print(f"  inserted {len(post_rows)} posts")

        cur.execute(
            "DELETE FROM substack_chunks WHERE post_id = ANY(%s)",
            (affected_post_ids,),
        )

        cur.executemany(
            """
            INSERT INTO substack_chunks
                (post_id, chunk_idx, text, token_count, embedding)
            VALUES (%s, %s, %s, %s, %s::vector)
            """,
            chunk_rows,
        )
        print(f"  inserted {len(chunk_rows)} chunks")

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM substack_posts")
        n_posts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM substack_chunks")
        n_chunks = cur.fetchone()[0]
        print(f"done. substack_posts={n_posts}, substack_chunks={n_chunks}")


if __name__ == "__main__":
    main()

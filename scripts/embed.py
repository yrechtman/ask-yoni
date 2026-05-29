"""Chunk posts and embed each chunk via OpenAI text-embedding-3-small.

Input:  data/posts.jsonl
Output: data/chunks.jsonl  (post_id, chunk_idx, text, token_count, embedding)

Chunking is paragraph-aware: paragraphs are accumulated up to a token budget,
with the post title prepended to every chunk so retrieval keeps context.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
POSTS_FILE = ROOT / "data" / "posts.jsonl"
CHUNKS_FILE = ROOT / "data" / "chunks.jsonl"

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
CHUNK_TOKENS = 500
EMBED_BATCH = 96  # OpenAI accepts up to 2048 inputs; 96 is comfortable.


def chunk_post(title: str, body_md: str, enc: tiktoken.Encoding) -> list[str]:
    """Split body into ~CHUNK_TOKENS chunks on paragraph boundaries.

    Each chunk is returned with `# {title}\n\n` prefixed so retrieval has context.
    """
    paragraphs = [p.strip() for p in body_md.split("\n\n") if p.strip()]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for p in paragraphs:
        p_tokens = len(enc.encode(p))
        if current and current_tokens + p_tokens > CHUNK_TOKENS:
            chunks.append(current)
            current, current_tokens = [], 0
        current.append(p)
        current_tokens += p_tokens
    if current:
        chunks.append(current)

    prefix = f"# {title}\n\n"
    return [prefix + "\n\n".join(c) for c in chunks]


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set; check .env")
    if not POSTS_FILE.exists():
        raise SystemExit(f"missing input: {POSTS_FILE}; run ingest.py first")

    client = OpenAI()
    enc = tiktoken.get_encoding("cl100k_base")

    posts = [json.loads(line) for line in POSTS_FILE.open()]
    records: list[dict] = []
    for p in posts:
        for idx, text in enumerate(chunk_post(p["title"], p["body_md"], enc)):
            records.append({
                "post_id": p["post_id"],
                "slug": p["slug"],
                "chunk_idx": idx,
                "text": text,
                "token_count": len(enc.encode(text)),
            })

    total_tokens = sum(r["token_count"] for r in records)
    print(f"chunking: {len(posts)} posts -> {len(records)} chunks ({total_tokens:,} tokens)")
    print(f"embedding via {EMBED_MODEL} in batches of {EMBED_BATCH}...")

    CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_FILE.open("w") as out:
        for start in range(0, len(records), EMBED_BATCH):
            batch = records[start:start + EMBED_BATCH]
            resp = client.embeddings.create(
                model=EMBED_MODEL,
                input=[r["text"] for r in batch],
            )
            for r, item in zip(batch, resp.data):
                if len(item.embedding) != EMBED_DIM:
                    raise SystemExit(f"unexpected embedding dim {len(item.embedding)}")
                r["embedding"] = item.embedding
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"  {min(start + EMBED_BATCH, len(records))}/{len(records)}")

    print(f"wrote {len(records)} chunks -> {CHUNKS_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

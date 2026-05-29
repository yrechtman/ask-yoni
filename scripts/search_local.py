"""Sanity-check search against the local chunks.jsonl.

Usage: search_local.py "your query here"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_FILE = ROOT / "data" / "chunks.jsonl"


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: search_local.py "query"')
    query = " ".join(sys.argv[1:])

    load_dotenv(ROOT / ".env")
    client = OpenAI()
    q_emb = client.embeddings.create(
        model="text-embedding-3-small",
        input=[query],
    ).data[0].embedding

    chunks = [json.loads(line) for line in CHUNKS_FILE.open()]
    scored = [(dot(q_emb, c["embedding"]), c) for c in chunks]
    scored.sort(reverse=True, key=lambda x: x[0])

    print(f'query: "{query}"\n')
    seen_slugs: set[str] = set()
    shown = 0
    for score, c in scored:
        if c["slug"] in seen_slugs:
            continue
        seen_slugs.add(c["slug"])
        snippet = c["text"].split("\n\n", 1)[-1][:240].replace("\n", " ")
        print(f"[{score:.3f}] {c['slug']} (chunk {c['chunk_idx']})")
        print(f"        {snippet}\n")
        shown += 1
        if shown >= 5:
            break


if __name__ == "__main__":
    main()

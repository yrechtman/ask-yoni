"""MCP server for ask-yoni: semantic search over Yoni Rechtman's 99% Derisible.

Run locally:  uvicorn app.server:app --host 127.0.0.1 --port 8091

A Substack post often packages multiple distinct topics; we index at the
section level so search hits the right idea, not the whole issue.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from openai import OpenAI
from psycopg_pool import ConnectionPool
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LANDING_HTML = (Path(__file__).resolve().parent / "landing.html").read_text()

EMBED_MODEL = "text-embedding-3-small"
MAX_QUERY_CHARS = 1000
MAX_LIMIT = 20
SNIPPET_CHARS = 280

_pool: ConnectionPool | None = None
_openai: OpenAI | None = None


def db() -> ConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        _pool = ConnectionPool(dsn, min_size=1, max_size=4, kwargs={"options": "-c statement_timeout=10000"})
    return _pool


def openai_client() -> OpenAI:
    global _openai
    if _openai is None:
        _openai = OpenAI()
    return _openai


def embed_query(query: str) -> str:
    """Embed a query string; return a pgvector literal."""
    resp = openai_client().embeddings.create(model=EMBED_MODEL, input=[query])
    v = resp.data[0].embedding
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def snippet(chunk_text: str) -> str:
    """First N chars of the chunk body (after the `# title` prefix)."""
    body = chunk_text.split("\n\n", 1)[-1]
    s = " ".join(body.split())
    return s[:SNIPPET_CHARS] + ("…" if len(s) > SNIPPET_CHARS else "")


mcp = FastMCP(
    "ask-yoni",
    instructions=(
        "Semantic search over Yoni Rechtman's newsletter '99% Derisible' "
        "(99d.substack.com). Posts often package multiple distinct topics; "
        "search results are at the section level. Use search_99d to find "
        "topics, get_post to fetch a full issue, get_section to fetch one "
        "section, list_recent to see the latest posts."
    ),
    # We bind 127.0.0.1 because nginx terminates TLS in front. FastMCP would
    # otherwise auto-enable DNS-rebinding protection (only allowing Host:
    # localhost), which rejects legitimate proxied requests with Host: yoni.fyi.
    # Disable it — the protection only makes sense for purely-local MCP servers.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
def search_99d(
    query: Annotated[str, Field(description="Free-form question or topic to search Yoni's writing for.")],
    limit: Annotated[int, Field(description="Max number of section-level matches to return (1-20).", ge=1, le=MAX_LIMIT)] = 5,
    include_meta: Annotated[bool, Field(description="Include sections marked as meta (link lists, sign-offs, unrelated promos). Default false.")] = False,
) -> list[dict[str, Any]]:
    """Search Yoni Rechtman's newsletter 99% Derisible at the section level.

    A 99D issue often packages 2-8 distinct topics separated by horizontal rules.
    This tool returns matches at the section level, so you get the right idea
    rather than the whole issue. Use it when the user asks what Yoni thinks
    about a topic, or whether he has written about something.

    Each result includes the section's title and parent issue's title/url/date.
    Pass the parent slug to get_post() for the full issue, or (slug, section_idx)
    to get_section() for just the matching section.

    By default, sections classified as 'meta' (link lists, sign-offs, unrelated
    promos) are filtered out. Pass include_meta=true to include them.
    """
    if not query.strip():
        return []
    if len(query) > MAX_QUERY_CHARS:
        query = query[:MAX_QUERY_CHARS]

    q_lit = embed_query(query)
    with db().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH section_best AS (
                SELECT DISTINCT ON (sec.id)
                    sec.id AS section_id,
                    c.text AS chunk_text,
                    1 - (c.embedding <=> %s::vector) AS score
                FROM substack_chunks c
                JOIN substack_sections sec ON sec.id = c.section_id
                WHERE (%s OR sec.kind = 'main')
                ORDER BY sec.id, c.embedding <=> %s::vector
            )
            SELECT
                p.title       AS post_title,
                p.slug        AS post_slug,
                p.url         AS post_url,
                p.published_at,
                p.subtitle    AS post_subtitle,
                sec.title     AS section_title,
                sec.summary   AS section_summary,
                sec.section_idx,
                sec.anchor,
                sec.kind,
                sb.chunk_text,
                sb.score
            FROM section_best sb
            JOIN substack_sections sec ON sec.id = sb.section_id
            JOIN substack_posts p ON p.post_id = sec.post_id
            ORDER BY sb.score DESC
            LIMIT %s
            """,
            (q_lit, include_meta, q_lit, limit),
        )
        rows = cur.fetchall()

    return [
        {
            "section_title": section_title,
            "section_summary": section_summary,
            "section_idx": section_idx,
            "post_title": post_title,
            "post_slug": post_slug,
            "post_url": post_url,
            "date": published_at.date().isoformat(),
            "kind": kind,
            "score": round(float(score), 4),
            "excerpt": snippet(chunk_text),
        }
        for (post_title, post_slug, post_url, published_at, post_subtitle,
             section_title, section_summary, section_idx, anchor, kind,
             chunk_text, score) in rows
    ]


@mcp.tool()
def get_post(
    slug: Annotated[str, Field(description="Post slug, e.g. 'ai-accenture-not-accenture-for-ai'.")],
) -> dict[str, Any] | None:
    """Fetch a full 99% Derisible issue by slug.

    Slugs come from search_99d() / list_recent() results or from a Substack URL
    like https://99d.substack.com/p/<slug>. Returns title, url, date, subtitle,
    word_count, the full markdown body of the issue, and a list of its sections
    (each with title, kind, summary). Returns null if the slug is unknown.
    """
    with db().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT post_id, title, slug, url, published_at, subtitle, body_md, word_count
            FROM substack_posts WHERE slug = %s
            """,
            (slug,),
        )
        row = cur.fetchone()
        if not row:
            return None
        post_id, title, slug, url, published_at, subtitle, body_md, word_count = row

        cur.execute(
            """
            SELECT section_idx, title, kind, summary, word_count
            FROM substack_sections
            WHERE post_id = %s
            ORDER BY section_idx
            """,
            (post_id,),
        )
        sections = [
            {"section_idx": idx, "title": t, "kind": k, "summary": s, "word_count": wc}
            for (idx, t, k, s, wc) in cur.fetchall()
        ]

    return {
        "title": title,
        "slug": slug,
        "url": url,
        "date": published_at.date().isoformat(),
        "subtitle": subtitle,
        "word_count": word_count,
        "body_md": body_md,
        "sections": sections,
    }


@mcp.tool()
def get_section(
    slug: Annotated[str, Field(description="Parent post slug, e.g. 'ai-accenture-not-accenture-for-ai'.")],
    section_idx: Annotated[int, Field(description="Zero-based section index within the post.", ge=0)],
) -> dict[str, Any] | None:
    """Fetch a single section of a 99% Derisible issue.

    Use this when search_99d returns a section you want the full text of, but
    you don't want the whole issue. Returns the section's title, body markdown,
    kind, summary, and the parent post's title/url/date.
    """
    with db().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                sec.title, sec.body_md, sec.kind, sec.summary, sec.word_count, sec.anchor,
                p.title, p.slug, p.url, p.published_at
            FROM substack_sections sec
            JOIN substack_posts p ON p.post_id = sec.post_id
            WHERE p.slug = %s AND sec.section_idx = %s
            """,
            (slug, section_idx),
        )
        row = cur.fetchone()
    if not row:
        return None
    (section_title, body_md, kind, summary, word_count, anchor,
     post_title, post_slug, post_url, published_at) = row
    return {
        "section_title": section_title,
        "section_idx": section_idx,
        "kind": kind,
        "summary": summary,
        "word_count": word_count,
        "anchor": anchor,
        "body_md": body_md,
        "post_title": post_title,
        "post_slug": post_slug,
        "post_url": post_url,
        "date": published_at.date().isoformat(),
    }


@mcp.tool()
def list_recent(
    limit: Annotated[int, Field(description="How many recent issues to return (1-20).", ge=1, le=MAX_LIMIT)] = 10,
) -> list[dict[str, Any]]:
    """List the most recent 99% Derisible issues, newest first.

    Returns title, slug, url, date, subtitle, and a count of sections for each.
    Use when the user wants to see what Yoni has been writing about lately, or
    to browse the archive.
    """
    with db().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                p.title, p.slug, p.url, p.published_at, p.subtitle,
                COUNT(sec.id) FILTER (WHERE sec.kind = 'main') AS main_sections
            FROM substack_posts p
            LEFT JOIN substack_sections sec ON sec.post_id = p.post_id
            GROUP BY p.post_id
            ORDER BY p.published_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {
            "title": title,
            "slug": slug,
            "url": url,
            "date": published_at.date().isoformat(),
            "subtitle": subtitle,
            "main_section_count": int(main_sections or 0),
        }
        for (title, slug, url, published_at, subtitle, main_sections) in rows
    ]


class BrowserLandingMiddleware(BaseHTTPMiddleware):
    """Serve a help page when a browser hits /mcp.

    Real MCP clients send `Accept: text/event-stream`. Browsers send
    `Accept: text/html,...` and otherwise get a cryptic JSON-RPC error
    ("Not Acceptable: Client must accept text/event-stream"). Intercept
    those and return setup instructions instead.
    """

    async def dispatch(self, request, call_next):
        if request.method == "GET" and request.url.path.rstrip("/") == "/mcp":
            accept = request.headers.get("accept", "")
            if "text/event-stream" not in accept and "text/html" in accept:
                return HTMLResponse(LANDING_HTML)
        return await call_next(request)


app = mcp.streamable_http_app()
app.add_middleware(BrowserLandingMiddleware)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

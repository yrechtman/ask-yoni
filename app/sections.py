"""Shared logic for splitting a Substack post into classified sections.

Used by both the one-shot corpus backfill (scripts/split_sections.py) and the
incremental RSS sync (scripts/rss_sync.py).
"""
from __future__ import annotations

import re
from typing import Literal

import psycopg
import tiktoken
from openai import OpenAI
from pydantic import BaseModel

CLASSIFIER_MODEL = "gpt-4o-mini"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
CHUNK_TOKENS = 500
EMBED_BATCH = 96

_HR_LINE = re.compile(r"^---\s*$", re.MULTILINE)
_H3_SPLIT = re.compile(r"(?=^### )", re.MULTILINE)  # split *before* h3 lines
_HEADING_AT_TOP = re.compile(r"^(#{2,4})\s+(.+?)\s*\n", re.MULTILINE)
_SLUG_BAD = re.compile(r"[^a-z0-9]+")


class SectionInfo(BaseModel):
    title: str
    kind: Literal["main", "meta"]
    summary: str


class _PostSections(BaseModel):
    sections: list[SectionInfo]


def split_body(body_md: str) -> list[str]:
    """Split a post into candidate sections.

    First on horizontal rules (`---`), then within each chunk on `### h3`
    headings — those typically mark a distinct topic within a longer post.
    Sub-bullets and `####` headings stay inside their section.
    """
    sections: list[str] = []
    for hr_part in _HR_LINE.split(body_md):
        for h3_part in _H3_SPLIT.split(hr_part):
            text = h3_part.strip()
            if text:
                sections.append(text)
    return sections


def extract_initial_title(section_text: str) -> tuple[str | None, str]:
    """If the section starts with a heading, return (title, body_without_heading)."""
    m = _HEADING_AT_TOP.match(section_text)
    if m:
        return m.group(2).strip(), section_text[m.end():].strip()
    return None, section_text


def slugify(text: str) -> str:
    return _SLUG_BAD.sub("-", text.lower()).strip("-")[:60]


def classify_post(client: OpenAI, post_title: str, post_subtitle: str | None,
                  initial: list[tuple[str | None, str]]) -> list[SectionInfo]:
    """One LLM call to classify all sections in a post."""
    parts = []
    for i, (title, body) in enumerate(initial):
        marker = f"=== SECTION {i} ===\n"
        if title:
            marker += f"(existing heading: {title!r})\n"
        parts.append(marker + body)
    body = "\n\n".join(parts)

    system = (
        "You classify sections of a Substack newsletter by Yoni Rechtman (99% Derisible) "
        "for downstream semantic search. Yoni is a seed-stage venture capitalist; his "
        "writing covers VC strategy, AI/services, founders + company-building, and macro "
        "industry cycles. For each section, return:\n"
        "  - title: a short, descriptive title (use the existing heading if it's good; "
        "    otherwise write one that summarizes the section's idea). Title-case, "
        "    no leading 'Section N:' prefix, no trailing punctuation.\n"
        "  - kind: 'main' if the section expresses an idea/argument/observation/take that "
        "    represents Yoni's thinking; 'meta' if the section is just links to other posts, "
        "    a sign-off, an unrelated calendar/event promo, or boilerplate. Personal "
        "    announcements like 'I'm launching X' or 'I'm hiring for Y' are 'main'.\n"
        "  - summary: a single sentence (<= 20 words) capturing the core claim or topic.\n"
        "Return one entry per section, in order. Do not merge or skip sections."
    )
    user = (
        f"POST TITLE: {post_title}\n"
        f"POST SUBTITLE: {post_subtitle or ''}\n"
        f"NUMBER OF SECTIONS: {len(initial)}\n\n"
        f"{body}"
    )

    resp = client.beta.chat.completions.parse(
        model=CLASSIFIER_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=_PostSections,
        temperature=0,
    )
    out = resp.choices[0].message.parsed.sections
    if len(out) != len(initial):
        raise ValueError(f"LLM returned {len(out)} sections, expected {len(initial)}")
    return out


def chunk_section(post_title: str, section_title: str, body_md: str,
                  enc: tiktoken.Encoding) -> list[str]:
    """Chunk a section's body. Prefix each chunk with post + section title for retrieval context."""
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
    prefix = f"# {post_title} — {section_title}\n\n"
    return [prefix + "\n\n".join(c) for c in chunks]


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def _llm_one_line_summary(client: OpenAI, title: str, body_md: str) -> str:
    """One-sentence summary of a single-section post via gpt-4o-mini.

    Used when the post has no Substack subtitle to lean on. First-sentence
    extraction was rejected because the first sentence is often a preamble
    or aside rather than a description of the actual idea.
    """
    resp = client.chat.completions.create(
        model=CLASSIFIER_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Write a one-sentence summary (<=20 words) of the core claim or topic "
                    "of this 99% Derisible post. No leading 'This post is about' or 'Yoni "
                    "argues that'. Just the claim. Title case is fine for proper nouns; "
                    "otherwise sentence case."
                ),
            },
            {"role": "user", "content": f"Title: {title}\n\n{body_md[:6000]}"},
        ],
        temperature=0,
        max_tokens=80,
    )
    return (resp.choices[0].message.content or "").strip().strip('"')[:200]


def fast_path_summary(client: OpenAI, subtitle: str | None, title: str, body_md: str) -> str:
    """Summary for the fast-path single-section case.

    Prefer the Substack subtitle when present (the author wrote it). Otherwise
    have the LLM write a one-line summary — first-sentence extraction was
    rejected because the first sentence is often unrelated preamble.
    """
    if subtitle and subtitle.strip():
        return subtitle.strip()[:200]
    return _llm_one_line_summary(client, title, body_md)


def process_post(cur: psycopg.Cursor, client: OpenAI, enc: tiktoken.Encoding, post: dict) -> tuple[int, int]:
    """Split, classify, chunk, embed, and persist a single post.

    Assumes the post row is already in substack_posts. Deletes any prior
    sections (cascades to chunks) and inserts fresh ones.
    """
    body_md = post["body_md"]
    raw_sections = split_body(body_md)
    if not raw_sections:
        return 0, 0

    initial = [extract_initial_title(s) for s in raw_sections]

    if len(initial) == 1 and initial[0][0] is None:
        infos = [SectionInfo(
            title=post["title"],
            kind="main",
            summary=fast_path_summary(client, post.get("subtitle"), post["title"], initial[0][1]),
        )]
    else:
        try:
            infos = classify_post(client, post["title"], post.get("subtitle"), initial)
        except ValueError as e:
            # LLM disagrees with the split count. Fall back to treating the
            # whole post as one section so we don't lose retrieval entirely.
            print(f"  warn: classifier mismatch for {post.get('slug')!r}, falling back to single section ({e})")
            initial = [(None, body_md)]
            infos = [SectionInfo(title=post["title"], kind="main", summary=(post.get("subtitle") or "")[:200])]

    cur.execute("DELETE FROM substack_sections WHERE post_id = %s", (post["post_id"],))

    section_ids: list[tuple[int, str, str]] = []
    for idx, ((_, body), info) in enumerate(zip(initial, infos)):
        anchor = slugify(info.title)
        word_count = len(body.split())
        cur.execute(
            """
            INSERT INTO substack_sections
                (post_id, section_idx, title, body_md, word_count, kind, summary, anchor)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (post["post_id"], idx, info.title, body, word_count, info.kind, info.summary, anchor),
        )
        section_ids.append((cur.fetchone()[0], info.title, body))

    all_chunk_texts: list[tuple[int, int, str]] = []
    for section_id, section_title, section_body in section_ids:
        for idx, text in enumerate(chunk_section(post["title"], section_title, section_body, enc)):
            all_chunk_texts.append((section_id, idx, text))

    chunk_rows: list[tuple] = []
    for start in range(0, len(all_chunk_texts), EMBED_BATCH):
        batch = all_chunk_texts[start:start + EMBED_BATCH]
        resp = client.embeddings.create(model=EMBED_MODEL, input=[t for _, _, t in batch])
        for (section_id, chunk_idx, text), item in zip(batch, resp.data):
            if len(item.embedding) != EMBED_DIM:
                raise RuntimeError(f"unexpected embedding dim {len(item.embedding)}")
            chunk_rows.append((section_id, chunk_idx, text, len(enc.encode(text)), vector_literal(item.embedding)))

    cur.executemany(
        """
        INSERT INTO substack_chunks
            (section_id, chunk_idx, text, token_count, embedding)
        VALUES (%s, %s, %s, %s, %s::vector)
        """,
        chunk_rows,
    )
    return len(section_ids), len(chunk_rows)

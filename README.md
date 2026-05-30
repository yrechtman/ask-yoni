# ask-yoni

A public MCP server for semantic search over Yoni Rechtman's writing on
[99% Derisible](https://99d.substack.com).

Posts on 99D often package multiple distinct topics in a single issue.
ask-yoni splits each issue into sections, embeds each section, and lets
you find the right idea instead of the whole post.

## Use it

Don't open the URL in a browser — it's an MCP endpoint, not a web page.
Add it as a custom connector in your AI client instead:

    https://yoni.fyi/mcp

**Claude.ai (web or desktop):** Settings → Connectors → Add custom
connector. Paste the URL.

**Claude Code:**

```bash
claude mcp add --transport http ask-yoni https://yoni.fyi/mcp
```

**Cursor:** Settings → MCP → Add new MCP server → HTTP transport, paste
the URL.

If you do hit `https://yoni.fyi/mcp` in a browser, you'll get a setup page
with the same instructions and a copy button — the MCP protocol itself is
unchanged.

It exposes four tools:

| Tool | What it does |
|---|---|
| `search_99d(query, limit=5, include_meta=false)` | Semantic search at the section level. Returns the best matching sections across the archive, each with the parent issue's title/url/date. |
| `get_post(slug)` | Fetch a full Substack issue by slug, including its list of sections. |
| `get_section(slug, section_idx)` | Fetch a single section of an issue. |
| `list_recent(limit=10)` | List the most recent issues, newest first. |

Try things like *"what does Yoni think about AI services companies"*,
*"narrative warfare in VC"*, or *"how should I think about seed pricing"*.

## How it works

```
Substack RSS ──► Markdown ──► substack_posts ──┐
                                               ├──► sections classifier (gpt-4o-mini)
                                               │       │
                                               │       ▼
                                               │   substack_sections (kind: main/meta)
                                               │       │
                                               │       ▼
                                               │   chunker + embedder (text-embedding-3-small)
                                               │       │
                                               │       ▼
                                               └──► substack_chunks (pgvector, HNSW)
                                                       │
                                                       ▼
                                                 MCP server (FastMCP, Streamable HTTP)
                                                       │
                                                       ▼
                                                 https://yoni.fyi/mcp
```

- **Storage:** DigitalOcean Managed Postgres with `pgvector`. Tables: `substack_posts` → `substack_sections` → `substack_chunks`. Sections classified as `meta` (link lists, sign-offs, unrelated promos) are filtered from search by default.
- **Embeddings:** OpenAI `text-embedding-3-small` (1536-dim) over ~500-token paragraph-aware chunks, each prefixed with `# {post_title} — {section_title}` for retrieval context.
- **Section classification:** A single `gpt-4o-mini` call per post classifies each section (`main` vs `meta`) and writes a one-line summary.
- **Hosting:** A small FastMCP/FastAPI server runs on the existing DigitalOcean droplet behind nginx + Let's Encrypt. Deploy is `git push origin main` — the sync cron picks code up on its next tick. Read [CLAUDE.md](./CLAUDE.md) for operational details.
- **Sync:** A cron job hits the Substack RSS feed twice daily (17:00 + 03:00 UTC — primary + backup, spanning the typical Friday-morning publish window). New posts are split, classified, embedded, and inserted. Each run's status, new-post count, and any failed slugs land in `substack_sync_runs`.

## Local development

```bash
# 1) Create venv (Python 3.12+) and install deps
uv venv --python 3.12 .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install mcp fastapi 'uvicorn[standard]'

# 2) .env at the repo root
cat <<EOF > .env
DATABASE_URL=postgres://...
OPENAI_API_KEY=sk-...
EOF

# 3) Run the server
.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8091

# 4) Exercise it
.venv/bin/python scripts/test_mcp.py
```

## Source

- Newsletter: <https://99d.substack.com>
- Repo: <https://github.com/yrechtman/ask-yoni>

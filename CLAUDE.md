# CLAUDE.md — ask-yoni

Operational notes for Claude Code sessions on this repo. User-facing README is in [README.md](./README.md).

## What this is

Public MCP server (`https://yoni.fyi/mcp`) that does semantic search over Yoni Rechtman's newsletter [99% Derisible](https://99d.substack.com). One Substack issue → many sections → many chunks → pgvector. Section-level retrieval so searches find the right *idea*, not the whole issue.

## Stack

- **Python 3.12** + `uv`-managed `.venv`
- **FastMCP** (from the `mcp` SDK) serving Streamable HTTP via FastAPI/Starlette under `uvicorn`
- **psycopg 3** + `psycopg_pool` for Postgres
- **OpenAI**: `text-embedding-3-small` (search), `gpt-4o-mini` (section classifier)
- **pgvector** with HNSW index for semantic search
- No Docker. Plain venv + systemd on the droplet.

## Repo layout

```
ask-yoni/
├── app/
│   ├── __init__.py
│   ├── server.py            # FastMCP app, tools: search_99d / get_post / get_section / list_recent
│   └── sections.py          # Shared split/classify/embed logic (imported by backfill + sync)
├── migrations/
│   ├── 001_substack_archive.sql   # posts + chunks tables
│   ├── 002_sections.sql           # adds sections; rebuilds chunks to reference section_id
│   ├── 003_tags.sql               # adds substack_sync_runs (also added a tags column, since dropped)
│   └── 004_drop_tags.sql          # drops the tags column from 003 (tags experiment rolled back)
├── scripts/
│   ├── ingest.py                  # one-shot: Substack export zip → substack_posts (initial backfill)
│   ├── embed.py                   # legacy v0: chunked posts directly; superseded by split_sections
│   ├── load_to_db.py              # legacy v0: loaded JSONL → Postgres; superseded
│   ├── split_sections.py          # backfill: iterates substack_posts, calls app.sections.process_post
│   ├── rss_sync.py                # cron: poll RSS, insert new posts, call process_post
│   ├── sync-droplet.sh            # cron wrapper: git pull + rss_sync, flocked + monthly log
│   ├── search_local.py            # dev: cosine search against data/chunks.jsonl (pre-DB)
│   ├── test_mcp.py                # end-to-end MCP client test against http://127.0.0.1:8091/mcp
│   ├── ask-yoni.service           # systemd unit (installs to /etc/systemd/system/)
│   ├── nginx-ask-yoni.conf        # full TLS server block for yoni.fyi
│   ├── nginx-ask-yoni-bootstrap.conf  # HTTP-only stub for certbot --webroot first run
│   └── nginx-ratelimit-ask-yoni.conf  # http{}-level limit_req_zone (drops in /etc/nginx/conf.d/)
├── raw-export/              # Substack export zip, gitignored — input for initial backfill only
├── data/                    # processed JSONL intermediates, gitignored
├── logs/                    # gitignored (.gitignore there preserves the dir)
├── requirements.txt
├── README.md
└── CLAUDE.md
```

`scripts/ingest.py`, `scripts/embed.py`, `scripts/load_to_db.py`, `scripts/search_local.py`: kept for reproducibility of the initial backfill from the Substack export. Day-to-day, the source of truth is Postgres; new posts flow through `rss_sync.py` only.

## Schema

```
substack_posts                    -- one row per Substack issue
  post_id      TEXT PK            -- numeric ID from Substack export, OR slug for RSS-sourced posts
  slug         TEXT UNIQUE
  url, title, subtitle, published_at, audience
  body_md      TEXT               -- full markdown body
  word_count   INTEGER
  indexed_at   TIMESTAMPTZ

substack_sections                 -- one row per logical section inside an issue
  id           BIGSERIAL PK
  post_id      → substack_posts(post_id) ON DELETE CASCADE
  section_idx  INTEGER            -- 0..N within the issue
  title        TEXT               -- existing ### heading if good, else LLM-generated
  body_md, word_count
  kind         TEXT               -- 'main' | 'meta'; meta is filtered from default search
  summary      TEXT               -- single-sentence LLM summary
  anchor       TEXT               -- slugified title
  UNIQUE (post_id, section_idx)

substack_chunks                   -- one row per embedding-sized chunk inside a section
  id           BIGSERIAL PK
  section_id   → substack_sections(id) ON DELETE CASCADE
  chunk_idx    INTEGER
  text         TEXT               -- chunk body, prefixed with "# {post_title} — {section_title}"
  token_count  INTEGER
  embedding    vector(1536)       -- HNSW index, vector_cosine_ops
  UNIQUE (section_id, chunk_idx)

substack_sync_runs                -- one row per cron sync run, for health visibility
  id           BIGSERIAL PK
  started_at   TIMESTAMPTZ
  finished_at  TIMESTAMPTZ
  status       TEXT               -- 'running' | 'ok' | 'failed'
  new_posts    INTEGER            -- count of newly ingested posts in this run
  note         TEXT               -- failed slugs or error repr
```

The DB lives in a shared Managed Postgres cluster I run for other personal infra. Tables are prefixed `substack_` for isolation. No separate DB or role — we trust the application code to only touch the substack tables.

## Droplet

- Host alias: **`slow-claude-core`** (in `~/.ssh/config` on the Mac)
- Public IP: **`165.232.150.244`**
- Project path: **`/root/ask-yoni-local`**
- systemd unit: **`ask-yoni.service`** — `uvicorn app.server:app --host 127.0.0.1 --port 8091`
- Logs: `/root/ask-yoni-local/logs/{server.log, sync-YYYY-MM.log}`
- Deploy key on droplet: `/root/.ssh/id_ed25519` (registered against `yrechtman/ask-yoni`)

## Database access

The Postgres cluster is firewall-locked to the droplet only (DigitalOcean
"trusted sources"). Direct connections from anywhere else — including the
Mac — will time out. Two ways to run ad-hoc queries:

1. **(preferred) Via SSH.** Wrap the query in a Python one-liner on the
   droplet — the pattern is everywhere in this doc:

   ```bash
   ssh slow-claude-core 'cd /root/ask-yoni-local && set -a && source .env && set +a && .venv/bin/python -c "
   import os, psycopg
   with psycopg.connect(os.environ[\"DATABASE_URL\"]) as conn, conn.cursor() as cur:
       cur.execute(\"SELECT count(*) FROM substack_posts\")
       print(cur.fetchone())
   "'
   ```

2. **SSH tunnel** if you want a local `psql` / GUI client. From the Mac:

   ```bash
   # DB host is in `DATABASE_URL` on the droplet — read it once, then:
   ssh -N -L 25060:<db-host-from-DATABASE_URL>:25060 slow-claude-core
   ```

   In another shell, point a client at `localhost:25060` with the password
   from the droplet's `/root/ask-yoni-local/.env`. Kill the tunnel when done.

If a connection ever starts timing out from the droplet itself, check
`db-cluster-get-firewall-rules` — the trusted-sources list should have a
`droplet` entry with uuid `559033337`.

## Common operations

### Local dev

```bash
cd "/Users/yonirechtman/Claude Code/ask-yoni"
.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8091
# in another shell:
.venv/bin/python scripts/test_mcp.py
```

### Push code → deploy

```bash
git push origin main
# Wait up to 12h for the next cron tick (17:00 or 03:00 UTC) to pull,
# OR force immediate:
ssh slow-claude-core 'cd /root/ask-yoni-local && git pull && systemctl restart ask-yoni'
```

The sync cron picks up code changes via `git pull --ff-only origin main` in `sync-droplet.sh`, but it does **not** auto-restart the running uvicorn. After server-code (`app/`) changes, restart explicitly.

### Check sync health

```bash
# Most recent runs (one row per sync; 'ok' means no exceptions, even if 0 new posts)
ssh slow-claude-core 'cd /root/ask-yoni-local && set -a && source .env && set +a && .venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ[\"DATABASE_URL\"]) as conn, conn.cursor() as cur:
    cur.execute(\"SELECT started_at, finished_at, status, new_posts, note FROM substack_sync_runs ORDER BY started_at DESC LIMIT 10\")
    for r in cur.fetchall(): print(r)
"'
```

If the most recent `finished_at` is more than ~14h old, the cron is broken.

### Re-classify / re-embed everything

```bash
ssh slow-claude-core 'cd /root/ask-yoni-local && set -a && source .env && set +a && .venv/bin/python scripts/split_sections.py'
```

This rewrites every row in `substack_sections` and `substack_chunks`. Idempotent. Use `ONLY_SLUG=<slug>` for one post, or `ONLY_MISSING=1` to fill gaps only.

### Add a migration

1. Drop the SQL in `migrations/NNN_description.sql`.
2. Apply it from anywhere with DB access (Mac when whitelisted, droplet always):

   ```bash
   ssh slow-claude-core 'cd /root/ask-yoni-local && set -a && source .env && set +a && .venv/bin/python -c "
   import os, psycopg
   with psycopg.connect(os.environ[\"DATABASE_URL\"]) as conn, conn.cursor() as cur:
       cur.execute(open(\"migrations/NNN_description.sql\").read())
       conn.commit()
   "'
   ```

No migration tool — we apply manually and keep the SQL in the repo for the record.

### Watch logs

```bash
ssh slow-claude-core 'tail -f /root/ask-yoni-local/logs/server.log'
ssh slow-claude-core 'tail -f /root/ask-yoni-local/logs/sync-$(date -u +%Y-%m).log'
ssh slow-claude-core 'journalctl -u ask-yoni -n 50 --no-pager'
```

### Force RSS sync

```bash
ssh slow-claude-core 'bash /root/ask-yoni-local/scripts/sync-droplet.sh'
```

### Inspect a single post / section

```bash
ssh slow-claude-core 'cd /root/ask-yoni-local && set -a && source .env && set +a && .venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ[\"DATABASE_URL\"]) as conn, conn.cursor() as cur:
    cur.execute(\"SELECT section_idx, kind, title, summary FROM substack_sections JOIN substack_posts USING (post_id) WHERE slug = %s ORDER BY section_idx\", (\"<slug>\",))
    for r in cur.fetchall(): print(r)
"'
```

## Costs

- **OpenAI embeddings**: ~$0.00002 per search query. Full backfill of the corpus (~500 chunks): ~$0.005.
- **OpenAI gpt-4o-mini classifier**: ~$0.0005 per post. Full corpus pass (~155 posts): ~$0.10.
- **DO droplet + Managed Postgres**: shared with other personal infra, no marginal cost attributable to ask-yoni.
- **Domain**: yoni.fyi registered via Vercel (~$10/yr).

For a public service the only knob that matters is `search_99d` query volume. Rate-limited at nginx (60 req/min/IP, burst 20) — see `scripts/nginx-ratelimit-ask-yoni.conf`. At cap, worst-case spend is ~$0.001/IP/min on embeddings; negligible.

## Gotchas

- **Python 3.12 required.** The system `python3` on macOS is 3.9, which the `mcp` package can't install on. Use `uv venv --python 3.12`.
- **DB connection from Mac can be flaky** — Managed Postgres trusted-sources allowlist may not include all home IPs. Run admin queries from the droplet (`ssh slow-claude-core ...`) when local times out.
- **Substack RSS only returns the most recent 20 items.** For backfill we need the export zip. Day-to-day sync is fine since we publish weekly.
- **post_id is mixed.** Initial backfill used Substack's numeric ID (e.g. `198776230`). RSS sync uses the slug as the post_id. Schema accepts both since `post_id` is TEXT. Always match by `slug` for cross-source identity.
- **Section count varies wildly.** Some posts have 1 section, some have 8. The classifier may merge in edge cases; check section counts after rerunning `split_sections.py`.
- **`app/server.py` changes need `systemctl restart ask-yoni`.** Script changes (in `scripts/`) auto-take-effect on next cron tick because cron re-invokes the script fresh.
- **Cron runs twice a day.** Yoni publishes once a week (Friday mornings ET / ~15 UTC). The 17:00 UTC tick is the primary catch; the 03:00 UTC tick the next morning is the backup. Don't bother making it more aggressive — the marginal latency on a weekly cadence isn't worth the noise.
- **My IP got fail2banned once.** Rapid SSH disconnect-and-reconnect cycles (from killing a hung remote process) tripped fail2ban on the droplet. If `ssh slow-claude-core` starts returning `Permission denied` out of nowhere with no key/config changes, wait ~10 min for the ban to expire — or, with droplet console access, run `fail2ban-client unban <ip>`.

## Useful references

- 99% Derisible: <https://99d.substack.com>
- GitHub repo: <https://github.com/yrechtman/ask-yoni>
- MCP spec: <https://modelcontextprotocol.io>
- FastMCP: <https://github.com/jlowin/fastmcp>

"""Convert the Substack export into a clean JSONL corpus.

Input:  raw-export/posts.csv + raw-export/posts/<post_id>.<slug>.html
Output: data/posts.jsonl (one JSON object per published newsletter post)
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw-export"
OUT = ROOT / "data" / "posts.jsonl"
PUB_HOST = "https://99d.substack.com"


class CleanConverter(MarkdownConverter):
    """Strip Substack image markup; keep everything else."""

    def convert_img(self, el, text, parent_tags):
        return ""

    def convert_figure(self, el, text, parent_tags):
        return ""

    def convert_picture(self, el, text, parent_tags):
        return ""


def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "svg", "button"]):
        tag.decompose()
    md = CleanConverter(heading_style="ATX", bullets="-", strip=["img", "figure", "picture"]).convert_soup(soup)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


def parse_post_id_filename(name: str) -> tuple[str, str]:
    """`12345.some-slug.html` -> ("12345", "some-slug")."""
    stem = name.removesuffix(".html")
    post_id, _, slug = stem.partition(".")
    return post_id, slug


def normalize_date(s: str) -> str:
    """Substack ISO with milliseconds -> ISO 8601 UTC."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat()


def main() -> None:
    posts_csv = RAW / "posts.csv"
    posts_dir = RAW / "posts"
    if not posts_csv.exists() or not posts_dir.exists():
        raise SystemExit(f"missing input: {posts_csv} or {posts_dir}")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    meta_by_csv_id: dict[str, dict] = {}
    with posts_csv.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            meta_by_csv_id[row["post_id"]] = row

    written = 0
    skipped_unpublished = 0
    skipped_no_meta = 0
    skipped_non_newsletter = 0
    skipped_empty = 0

    with OUT.open("w") as out:
        for html_path in sorted(posts_dir.glob("*.html")):
            post_id, slug = parse_post_id_filename(html_path.name)
            csv_id = f"{post_id}.{slug}"
            meta = meta_by_csv_id.get(csv_id)
            if not meta:
                skipped_no_meta += 1
                continue
            if meta.get("is_published", "").lower() != "true":
                skipped_unpublished += 1
                continue
            if meta.get("type") != "newsletter":
                skipped_non_newsletter += 1
                continue

            html = html_path.read_text(encoding="utf-8")
            body_md = html_to_markdown(html)
            if not body_md or len(body_md) < 50:
                skipped_empty += 1
                continue

            record = {
                "post_id": post_id,
                "slug": slug,
                "url": f"{PUB_HOST}/p/{slug}",
                "title": meta.get("title", "").strip(),
                "subtitle": meta.get("subtitle", "").strip(),
                "date": normalize_date(meta["post_date"]),
                "audience": meta.get("audience", ""),
                "body_md": body_md,
                "word_count": len(body_md.split()),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"wrote {written} posts -> {OUT.relative_to(ROOT)}")
    print(f"  skipped: {skipped_unpublished} unpublished, "
          f"{skipped_non_newsletter} non-newsletter, "
          f"{skipped_no_meta} no-metadata, "
          f"{skipped_empty} empty-body")


if __name__ == "__main__":
    main()

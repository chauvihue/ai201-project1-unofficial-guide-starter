"""One-off: convert browser-scraped Reddit JSON (CDP dump) into documents/reddit_*.txt.

Reddit closed self-service API access (Responsible Builder Policy, 2025) and
the public .json endpoints (May 2026), so the threads were captured via
browser automation from old.reddit.com instead. This script reuses the same
cleaning and record format as scrape.py so chunk.py needs no changes.

The capture preserves the comment tree: each comment carries its nesting
depth and the index of its parent, which is rendered as a "[Replying to: ...]"
context line so replies remain interpretable as standalone chunks.
"""

import json
import re
import sys
from pathlib import Path

from scrape import clean_text, should_skip_reddit_comment, write_records

PARENT_EXCERPT_CHARS = 100

cdp_dump = Path(sys.argv[1])
payload = json.loads(cdp_dump.read_text(encoding="utf-8"))
threads = json.loads(payload["result"]["value"])


def excerpt(text: str) -> str:
    """Single-line, bracket-safe excerpt of a parent comment."""
    flat = re.sub(r"\s+", " ", text).strip()
    flat = flat.replace("[", "(").replace("]", ")")
    if len(flat) > PARENT_EXCERPT_CHARS:
        flat = flat[:PARENT_EXCERPT_CHARS].rstrip() + "..."
    return flat


for slug, data in threads.items():
    if "error" in data:
        print(f"SKIPPED {slug}: {data['error']}")
        continue

    title = data["title"]
    header = f"[Thread: {title} | r/umass]"
    records: list[str] = []

    selftext = clean_text(data.get("selftext", ""))
    if selftext:
        records.append(f"[Post | score: OP | depth: 0]\n{selftext}")

    comments = data["comments"]
    cleaned_bodies = [clean_text(c.get("body", "")) for c in comments]

    for i, comment in enumerate(comments):
        body = cleaned_bodies[i]
        if should_skip_reddit_comment(comment.get("author"), body):
            continue

        depth = comment.get("depth", 0)
        record = f"[Comment | score: {comment.get('score', 0)} | depth: {depth}]\n"

        parent_idx = comment.get("parent")
        if parent_idx is not None:
            parent_body = cleaned_bodies[parent_idx]
            if parent_body and parent_body not in ("[removed]", "[deleted]"):
                record += f"[Replying to: {excerpt(parent_body)}]\n"
        elif depth == 0 and selftext:
            record += f"[Replying to: {excerpt(selftext)}]\n"

        record += body
        records.append(record)

    write_records(f"reddit_{slug}.txt", header, records)

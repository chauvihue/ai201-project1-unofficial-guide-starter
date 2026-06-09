"""
scrape.py — Stage 1 of the pipeline: ingestion / scraping.

Pulls raw text from the three sources (Reddit, Rate My Professor, UMass PDFs),
cleans it, and writes one cached file per source into documents/.
Re-run this script to "refresh" the corpus — this is the dynamic part of the
system, kept SEPARATE from query time so evaluation stays reproducible.

Output convention (matches the chunking plan in planning.md):
    documents/reddit_<slug>.txt   -> one comment per record
    documents/rmp_<name>.txt      -> one review per record
    documents/<existing>.pdf      -> already on disk; we extract to .txt

Dependencies (add to requirements.txt and pip install):
    praw                # Reddit API
    ratemyprofessor     # RMP GraphQL wrapper
    pdfplumber          # PDF text extraction
    python-dotenv       # load API keys from .env
"""

import os
import re
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DOCS_DIR = Path("documents")
DOCS_DIR.mkdir(exist_ok=True)

# Reddit thread IDs (the part after /comments/ in the URL).
# e.g. https://www.reddit.com/r/umass/comments/1ot0yto/easy_200_cs_courses/
REDDIT_THREADS = {
    "easy_200_cs_courses": "1ot0yto",
    # TODO: add the other 7 thread IDs from your Documents table
}

# RMP professor IDs (the number at the end of /professor/<id>).
RMP_PROFESSORS = {
    "james_perretta": "3114707",
    # TODO: add the other professor IDs from your Documents table
}

# Reddit API creds — create a "script" app at https://www.reddit.com/prefs/apps
# and add these to your .env (alongside GROQ_API_KEY):
#   REDDIT_CLIENT_ID=...
#   REDDIT_CLIENT_SECRET=...
#   REDDIT_USER_AGENT=unofficial-guide/0.1 by u/yourusername
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")


# ---------------------------------------------------------------------------
# Cleaning helpers (shared)
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Strip the noise that doesn't belong in a chunk.

    TODO (yours to implement):
      - decode HTML entities (&amp; &gt; &#39; -> & > ')
      - remove markdown link syntax [text](url) -> text, and bare URLs
      - drop reddit quote markers (>) and edit footers ("EDIT: ...")
      - collapse runs of whitespace/newlines
    Read one cleaned file by eye before trusting this (M3 checkpoint).
    """
    # placeholder so the skeleton runs; replace with real cleaning
    return text.strip()


def write_records(filename: str, header: str, records: list[str]) -> None:
    """Write a list of self-contained records to documents/<filename>.

    Each record is separated by a blank line so the chunker can treat
    one record == one chunk. `header` is the source context (e.g. thread
    title) prepended to the file for attribution.
    """
    path = DOCS_DIR / filename
    body = "\n\n".join(r for r in records if r.strip())
    path.write_text(f"{header}\n\n{body}\n", encoding="utf-8")
    print(f"wrote {len(records)} records -> {path}")


# ---------------------------------------------------------------------------
# Reddit  (PRAW)
# ---------------------------------------------------------------------------
def get_reddit_client():
    import praw

    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )


def scrape_reddit_thread(reddit, slug: str, thread_id: str) -> None:
    """Walk a thread's comment tree and write one record per comment.

    Each record should carry enough context to stand alone, e.g.:
        [Comment | score: 42]
        COMPSCI 220 is a lot of work but the projects teach you something...
    """
    submission = reddit.submission(id=thread_id)
    submission.comments.replace_more(limit=None)  # expand "load more comments"

    header = f"[Thread: {submission.title} | r/umass]"
    records: list[str] = []

    for comment in submission.comments.list():  # .list() flattens the tree
        # TODO:
        #   - skip deleted/removed bodies and AutoModerator/bot comments
        #   - clean_text(comment.body)
        #   - format as a record with light metadata (score, maybe author)
        body = clean_text(comment.body)
        if body:
            records.append(f"[Comment | score: {comment.score}]\n{body}")

    write_records(f"reddit_{slug}.txt", header, records)


# ---------------------------------------------------------------------------
# Rate My Professor  (ratemyprofessor / GraphQL)
# ---------------------------------------------------------------------------
def scrape_rmp_professor(slug: str, professor_id: str) -> None:
    """Pull all reviews for one professor; write one record per review.

    The `ratemyprofessor` package wraps RMP's GraphQL backend. Inspect the
    object it returns in a REPL first — field names (comment, class name,
    rating, date) drive both the record text and the metadata you'll later
    store in Chroma (useful for the 'filter out old ratings' challenge).
    """
    import ratemyprofessor

    professor = ratemyprofessor.get_professor_by_id(professor_id)  # verify this API
    header = f"[Rate My Professor: {professor.name} | {professor.school.name}]"
    records: list[str] = []

    for rating in professor.get_ratings():
        # TODO:
        #   - clean_text(rating.comment)
        #   - prepend context: which class, the score, the date
        #   - decide how you'll keep date/rating for metadata filtering later
        comment = clean_text(rating.comment)
        if comment:
            records.append(
                f"[Class: {rating.class_name} | rating: {rating.rating} | {rating.date}]\n{comment}"
            )

    write_records(f"rmp_{slug}.txt", header, records)


# ---------------------------------------------------------------------------
# UMass PDFs  (pdfplumber)
# ---------------------------------------------------------------------------
def extract_pdf(pdf_path: Path) -> None:
    """Extract text from one PDF to a parallel .txt cache.

    Course descriptions are structured records (one course block each) —
    you can either dump raw text here and do record-splitting in the chunker,
    or split on the course-code pattern at extraction time. See planning.md.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n\n".join(p.extract_text() for p in pdf.pages if p.extract_text())

    # TODO: light cleaning (page headers/footers repeat on every page — strip them)
    out = pdf_path.with_suffix(".txt")
    out.write_text(text, encoding="utf-8")
    print(f"extracted {pdf_path.name} -> {out.name}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> None:
    # 1. Reddit
    reddit = get_reddit_client()
    for slug, thread_id in REDDIT_THREADS.items():
        scrape_reddit_thread(reddit, slug, thread_id)

    # 2. Rate My Professor
    for slug, prof_id in RMP_PROFESSORS.items():
        scrape_rmp_professor(slug, prof_id)

    # 3. PDFs
    for pdf_path in DOCS_DIR.glob("*.pdf"):
        extract_pdf(pdf_path)


if __name__ == "__main__":
    main()

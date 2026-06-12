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
"""

import html
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DOCS_DIR = Path("documents")
DOCS_DIR.mkdir(exist_ok=True)

REDDIT_THREADS = {
    "easy_200_cs_courses": "1ot0yto",
    "course_recommendations_ms_cs": "1da5coi",
    "thoughts_grad_level_cs_classes": "1aojcc8",
    "fall_24_cs_grad_course": "1bymlyw",
    "easy_cs_electives": "sdcne1",
    "easiest_cs_400_500_courses": "qubmte",
    "cs_courseload_advice": "patv3a",
    "freshman_cs_second_semester": "jdppi6",
}

RMP_PROFESSORS = {
    "james_perretta": "3114707",
    "ella_tuson": "3127793",
    "marc_liberatore": "1948400",
    "phuthipong_bovornkeeratiroj": "2992114",
    "ghazaleh_parvini": "2624866",
    "justin_domke": "2290260",
    "marius_minea": "2416008",
    "cole_reilly": "2912301",
    "joe_chiu": "2420066",
    "mordecai_golin": "2940693",
}

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "unofficial-guide/0.1")

# Repeating PDF headers/footers to strip after extraction.
PDF_NOISE_PATTERNS = [
    re.compile(r"UMassAmherst Course Descriptions\s*", re.I),
    re.compile(r"UMassAmherst \d{4} (?:Spring|Fall)\s*", re.I),
    re.compile(
        r"Manning College of Information and Computer Sciences(?: \d{4} (?:Spring|Fall))?\s*",
        re.I,
    ),
    re.compile(r"SUBJECT CAT#CLASS# DAY/TIME LOCATION INSTRUCTOR\(S\)\s*", re.I),
    re.compile(r"Cat#\s*", re.I),
    re.compile(r"\d{4} (?:Spring|Fall) page \d+ \d+/\s*", re.I),
    re.compile(
        r"SPIRE REGISTRATION INFORMATION for \d{4} (?:Spring|Fall)\s*",
        re.I,
    ),
    re.compile(
        r"PREREQUISITES ELIGIBILITY RESTRICTIONS CLASS NOTES/COMMENTS "
        r"INFORMATION ONLY[^\n]*\n",
        re.I,
    ),
    # Page footers like "1/15/2026 13" and URL remnants split across lines.
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d+\s*$", re.M),
    re.compile(r"^edu/academics/course-overrides\.?\s*$", re.M),
]

MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
BARE_URL_RE = re.compile(r"https?://\S+")
EDIT_FOOTER_RE = re.compile(r"\bEDIT:\s*.*$|\bEdit:\s*.*$", re.I | re.M)
WHITESPACE_RE = re.compile(r"[ \t]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

BOT_AUTHORS = {"automoderator", "bot", "moderator"}
SKIP_BODIES = {"[deleted]", "[removed]"}


# ---------------------------------------------------------------------------
# Cleaning helpers (shared)
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Strip noise that does not belong in a chunk."""
    if not text:
        return ""

    text = html.unescape(text)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = BARE_URL_RE.sub("", text)

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()
        lines.append(stripped)
    text = "\n".join(lines)

    text = EDIT_FOOTER_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text)
    text = MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def write_records(filename: str, header: str, records: list[str]) -> None:
    """Write self-contained records to documents/<filename>."""
    path = DOCS_DIR / filename
    body = "\n\n".join(r for r in records if r.strip())
    path.write_text(f"{header}\n\n{body}\n", encoding="utf-8")
    print(f"wrote {len(records)} records -> {path}")


def should_skip_reddit_comment(author: str | None, body: str) -> bool:
    """Skip deleted, removed, AutoModerator, and bot comments."""
    if not body or body.strip() in SKIP_BODIES:
        return True
    author_lower = (author or "").lower()
    if author_lower in BOT_AUTHORS or author_lower.endswith("bot"):
        return True
    if "i am a bot" in body.lower():
        return True
    return False


def strip_pdf_noise(text: str) -> str:
    """Remove repeating UMass PDF headers and footers."""
    for pattern in PDF_NOISE_PATTERNS:
        text = pattern.sub("", text)
    text = MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Reddit  (PRAW, with .json fallback)
# ---------------------------------------------------------------------------
def get_reddit_client():
    import praw

    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )


def scrape_reddit_thread_json(slug: str, thread_id: str) -> None:
    """Fallback: fetch thread via Reddit's public .json endpoint.

    NOTE (June 2026): Reddit closed self-service API access (Responsible
    Builder Policy) and disabled unauthenticated .json endpoints, so both
    the PRAW path and this fallback return 403 without approved credentials.
    The reddit_*.txt files in documents/ were captured via browser automation
    from old.reddit.com instead — see _browser_reddit_to_txt.py, which
    converts that capture using the same cleaning/record format.
    """
    url = f"https://www.reddit.com/r/umass/comments/{thread_id}/.json"
    headers = {"User-Agent": REDDIT_USER_AGENT}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()

    submission = payload[0]["data"]["children"][0]["data"]
    title = submission["title"]
    header = f"[Thread: {title} | r/umass]"
    records: list[str] = []

    def walk_comments(comments_data):
        for item in comments_data:
            if item["kind"] != "t1":
                continue
            comment = item["data"]
            body = clean_text(comment.get("body", ""))
            author = comment.get("author")
            if should_skip_reddit_comment(author, body):
                continue
            score = comment.get("score", 0)
            records.append(f"[Comment | score: {score}]\n{body}")
            replies = comment.get("replies")
            if isinstance(replies, dict) and replies.get("data"):
                walk_comments(replies["data"]["children"])

    walk_comments(payload[1]["data"]["children"])
    write_records(f"reddit_{slug}.txt", header, records)


def scrape_reddit_thread(reddit, slug: str, thread_id: str) -> None:
    """Walk a thread's comment tree and write one record per comment."""
    submission = reddit.submission(id=thread_id)
    submission.comments.replace_more(limit=None)

    header = f"[Thread: {submission.title} | r/umass]"
    records: list[str] = []

    for comment in submission.comments.list():
        body = clean_text(comment.body)
        if should_skip_reddit_comment(getattr(comment, "author", None), body):
            continue
        records.append(f"[Comment | score: {comment.score}]\n{body}")

    write_records(f"reddit_{slug}.txt", header, records)


def scrape_reddit(slug: str, thread_id: str) -> None:
    reddit = get_reddit_client()
    if reddit is None:
        print(f"PRAW creds missing; using .json fallback for {slug}")
        scrape_reddit_thread_json(slug, thread_id)
        return
    try:
        scrape_reddit_thread(reddit, slug, thread_id)
    except Exception as exc:
        print(f"PRAW failed for {slug} ({exc}); using .json fallback")
        scrape_reddit_thread_json(slug, thread_id)


# ---------------------------------------------------------------------------
# Rate My Professor  (ratemyprofessors-client / GraphQL)
# ---------------------------------------------------------------------------
def scrape_rmp_professor(slug: str, professor_id: str) -> None:
    """Pull all reviews for one professor; write one record per review.

    Uses ratemyprofessors-client (GraphQL wrapper). The milestone brief names
    `ratemyprofessor`, but that package is not on PyPI; this client fulfills
    the same role.
    """
    from rmp_client import RMPClient

    with RMPClient() as client:
        professor = client.get_professor(professor_id)
        school_name = professor.school.name if professor.school else "Unknown"
        header = f"[Rate My Professor: {professor.name} | {school_name}]"
        records: list[str] = []

        for rating in client.iter_professor_ratings(professor_id):
            comment = clean_text(rating.comment or "")
            if not comment:
                continue
            class_name = rating.course_raw or "N/A"
            score = rating.quality
            date_str = rating.date.isoformat() if rating.date else "unknown"
            records.append(
                f"[Class: {class_name} | rating: {score} | {date_str}]\n{comment}"
            )

    write_records(f"rmp_{slug}.txt", header, records)


# ---------------------------------------------------------------------------
# UMass PDFs  (pdfplumber)
# ---------------------------------------------------------------------------
def extract_pdf(pdf_path: Path) -> None:
    """Extract text from one PDF to a parallel .txt cache."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n\n".join(p.extract_text() for p in pdf.pages if p.extract_text())

    text = strip_pdf_noise(clean_text(text))
    out = pdf_path.with_suffix(".txt")
    out.write_text(text, encoding="utf-8")
    print(f"extracted {pdf_path.name} -> {out.name}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> None:
    failures: list[str] = []

    for slug, thread_id in REDDIT_THREADS.items():
        try:
            scrape_reddit(slug, thread_id)
        except Exception as exc:
            failures.append(f"reddit/{slug}: {exc}")
            print(f"FAILED reddit/{slug}: {exc}")

    for slug, prof_id in RMP_PROFESSORS.items():
        try:
            scrape_rmp_professor(slug, prof_id)
        except Exception as exc:
            failures.append(f"rmp/{slug}: {exc}")
            print(f"FAILED rmp/{slug}: {exc}")

    for pdf_path in sorted(DOCS_DIR.glob("*.pdf")):
        try:
            extract_pdf(pdf_path)
        except Exception as exc:
            failures.append(f"pdf/{pdf_path.name}: {exc}")
            print(f"FAILED pdf/{pdf_path.name}: {exc}")

    if failures:
        print(f"\n{len(failures)} source(s) failed:")
        for f in failures:
            print(f"  - {f}")
    else:
        print("\nAll sources scraped successfully.")


if __name__ == "__main__":
    main()

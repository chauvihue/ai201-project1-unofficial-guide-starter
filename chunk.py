"""
chunk.py — Stage 2 of the pipeline: type-aware chunking.

Reads cached documents/*.txt files and writes chunks.jsonl for Milestone 4.
"""

import json
import re
from pathlib import Path
from random import randint

from langchain_text_splitters import RecursiveCharacterTextSplitter

DOCS_DIR = Path("documents")
CHUNKS_PATH = Path("chunks.jsonl")

MAX_CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150

# Reddit: smaller chunks + lighter overlap for embedding alignment and
# efficient use of the generation context window (top-k=5). Split only
# inside a single comment/post — never across separate records.
REDDIT_MAX_CHUNK_SIZE = 650
REDDIT_OVERLAP = 80

# Anchored at line start so course codes mentioned mid-sentence
# (e.g. "... OR COMPSCI 121 WITH A GRADE OF C") don't trigger bogus splits.
# [A-Z]? covers honors/letter-prefixed numbers like "COMPSCI H311", and the
# trailing [A-Z]{0,2} covers suffixed codes like "COMPSCI 590RM"/"COMPSCI 690K".
# Without the suffix, \d{3}\b fails before the letters (no word boundary), so
# e.g. "COMPSCI 590RM" was absorbed into the preceding 589 block.
COURSE_CODE_LOOKAHEAD = re.compile(
    r"(?m)(?=^(?:CICS|COMPSCI|INFO|MATH)\s?[A-Z]?\d{3}[A-Z]{0,2}\b)", re.I
)
COURSE_HEADER_LINE = re.compile(
    r"^(?:CICS|COMPSCI|INFO|MATH)\s+[A-Z]?\d{3}[A-Z]{0,2}\b", re.I
)
COURSE_CODE_EXTRACT = re.compile(
    r"^((?:CICS|COMPSCI|INFO|MATH)\s?[A-Z]?\d{3}[A-Z]{0,2})", re.I
)
SCHEDULE_ROW_LINE = re.compile(r"^U\d+\s", re.I)
REG_COURSE_BLOCK = re.compile(
    r"(?m)(?=^(?:CICS|COMPSCI|INFO|MATH)\s+[A-Z]?\d{3}[A-Z]{0,2}\b)", re.I
)
HAS_LETTERS = re.compile(r"[A-Za-z]")

# Course blocks shorter than this carry no standalone meaning (header-only
# fragments produced by stray line breaks in the PDF extraction).
MIN_COURSE_BLOCK_CHARS = 40

SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)

REDDIT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=REDDIT_MAX_CHUNK_SIZE,
    chunk_overlap=REDDIT_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)

REDDIT_REPLY_PREFIX_RE = re.compile(r'^\(replying to: "[^"]*"\)\n', re.S)

REDDIT_HEADER_RE = re.compile(r"^\[Thread:\s*(.+?)\s*\|\s*r/umass\]", re.I)
RMP_HEADER_RE = re.compile(
    r"^\[Rate My Professor:\s*(.+?)\s*\|\s*(.+?)\]", re.I
)
# Matches "[Comment | score: 12 | depth: 1]" and the OP's "[Post | score: OP | depth: 0]";
# the optional "[Replying to: ...]" line carries parent-comment context.
REDDIT_RECORD_RE = re.compile(
    r"^\[(?:Comment|Post) \| score:\s*(-?\w+)(?:\s*\|\s*depth:\s*(\d+))?\]\n"
    r"(?:\[Replying to:\s*([^\n]*?)\]\n)?"
    r"(.*)",
    re.S,
)
# Records are delimited by their header tags, NOT blank lines — comment and
# review bodies can contain "\n\n" internally (multi-paragraph text).
REDDIT_RECORD_SPLIT = re.compile(r"(?m)^(?=\[(?:Comment|Post) \| score:)")
RMP_RECORD_SPLIT = re.compile(r"(?m)^(?=\[Class:)")
RMP_RECORD_RE = re.compile(
    r"^\[Class:\s*(.+?) \| rating:\s*([\d.]+) \| ([^\]]+)\]\n(.*)", re.S
)

HTML_ARTIFACT_RE = re.compile(r"<|&gt;|&amp;|&lt;")

# Recurring honors/499Y trailer the PDF appends to many course descriptions.
# It carries no course-specific meaning and pollutes embeddings; the trailing
# "Does not count as a CS elective" clause is a negation the embedding model
# can't represent, so it falsely matches *elective* queries. Strip both before
# chunking. The honors trailer is removed only up to "prior to registering."
# so meaningful follow-on sentences (e.g. "Open to graduate ... students only.")
# are preserved.
HONORS_BOILERPLATE_RE = re.compile(
    r"\s*For\s+undergraduates considering graduate studies.*?prior to registering\.",
    re.S | re.I,
)
CS_ELECTIVE_DISCLAIMER_RE = re.compile(
    r"\s*Does not count as a CS elective for the CS major \(BA or BS\)\.",
    re.I,
)
# PDF page footers, e.g. "2026 Spring --- page 9 --- 1/15/2026".
PAGE_FOOTER_RE = re.compile(
    r"(?m)^\s*20\d{2}\s+(?:Spring|Fall|Summer|Winter)\s+---\s+page\s+\d+\s+---.*$"
)


def strip_boilerplate(text: str) -> str:
    """Remove recurring PDF artifacts that add noise to embeddings."""
    text = HONORS_BOILERPLATE_RE.sub("", text)
    text = CS_ELECTIVE_DISCLAIMER_RE.sub("", text)
    text = PAGE_FOOTER_RE.sub("", text)
    # Collapse blank-line runs left behind by the removals.
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text


def semester_from_filename(filename: str) -> str | None:
    name = filename.lower()
    if name.startswith("s26"):
        return "Spring 2026"
    if name.startswith("f26"):
        return "Fall 2026"
    return None


def source_type_from_filename(filename: str) -> str | None:
    name = filename.lower()
    if name.startswith("reddit_"):
        return "reddit"
    if name.startswith("rmp_"):
        return "rmp"
    if name.endswith("_course_description.txt"):
        return "course_description"
    if name.endswith("_course_schedule.txt"):
        return "course_schedule"
    if name.endswith("_reg_info.txt"):
        return "reg_info"
    if "requirement" in name:
        return "degree_requirement"
    return None


def maybe_split(text: str) -> list[str]:
    """Keep whole records under the cap; recursively split oversized ones."""
    text = text.strip()
    if not text or not HAS_LETTERS.search(text):
        return []
    if len(text) <= MAX_CHUNK_SIZE:
        return [text]
    return [c.strip() for c in SPLITTER.split_text(text) if c.strip()]


def maybe_split_reddit(text: str) -> list[str]:
    """Split long Reddit comments with smaller size/overlap than PDF sources.

    When a comment is split, the (replying to: ...) prefix is duplicated on
    every sub-chunk so threaded replies stay interpretable on their own.
    """
    text = text.strip()
    if not text or not HAS_LETTERS.search(text):
        return []

    if len(text) <= REDDIT_MAX_CHUNK_SIZE:
        return [text]

    prefix_match = REDDIT_REPLY_PREFIX_RE.match(text)
    reply_prefix = prefix_match.group(0) if prefix_match else ""
    body = text[len(reply_prefix) :] if reply_prefix else text

    if reply_prefix:
        body_budget = max(REDDIT_MAX_CHUNK_SIZE - len(reply_prefix), 200)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=body_budget,
            chunk_overlap=REDDIT_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        pieces = [p.strip() for p in splitter.split_text(body) if p.strip()]
        return [reply_prefix + piece for piece in pieces]

    return [c.strip() for c in REDDIT_SPLITTER.split_text(text) if c.strip()]


# Graduate-audience keywords. \bgrad\b matches standalone "grad" without
# catching "grade" (e after d => no boundary) or "undergraduate"/"undergrad"
# (no boundary before "grad"), so undergrad terms never read as grad.
AUDIENCE_KEYWORDS_RE = re.compile(
    r"\bMS\b|\bmaster'?s?\b|\bgraduate\b|\bgrad\b|\bPh\.?D\b", re.I
)


def classify_audience(text: str, course_code: str | None) -> str | None:
    """Tag a chunk's intended audience for query-time re-ranking.

    Rules (in priority order):
      - any grad keyword in the text, OR a 600+ course code -> "grad"
      - no keyword and a 500-level code, OR no course code     -> "mixed"
      - no keyword and a sub-500 code                          -> "undergrad"
      - anything indeterminate                                 -> None (safe no-op)
    500-level codes are "mixed" rather than "grad" because they are dual
    undergrad/grad courses at UMass; the keyword check still promotes them
    to "grad" when the text makes the graduate context explicit.
    """
    has_keyword = bool(AUDIENCE_KEYWORDS_RE.search(text or ""))
    match = re.search(r"\d{3}", course_code or "")
    level = int(match.group(0)) if match else None

    if has_keyword:
        return "grad"
    if level is None:
        return "mixed"
    if 500 <= level < 600:
        return "mixed"
    if level >= 600:
        return "grad"
    if level < 500:
        return "undergrad"
    return None


# Reddit threads are classified at the THREAD level, not per comment: a lone
# "grad"/"MS" mention inside an otherwise-undergrad thread must not flip a
# single chunk. The title is the strongest signal; the levels of course codes
# discussed in the body are the fallback. Bare "graduate" still counts here
# (titles are deliberate), but it is the per-comment scan that this avoids.
GRAD_TITLE_RE = re.compile(
    r"\bMS\b|\bmaster'?s?\b|\bgraduate\b|\bgrad\b|\bPhD\b", re.I
)
UNDERGRAD_TITLE_RE = re.compile(
    r"\bfreshman\b|\bsophomore\b|\bfirst[- ]?year\b|\bundergrad(?:uate)?\b", re.I
)
# Standalone 3-digit course numbers (e.g. "400+/500+", "200+"); \b...\b avoids
# matching years like "24" (2 digits) or "2024" (4 digits).
COURSE_NUMBER_RE = re.compile(r"\b(\d{3})\b")


def classify_reddit_audience(thread_title: str, body: str) -> str | None:
    """Classify a whole Reddit thread by title, falling back to body courses."""
    title_codes = [int(n) for n in COURSE_NUMBER_RE.findall(thread_title)]
    grad_signal = bool(GRAD_TITLE_RE.search(thread_title)) or any(
        n >= 500 for n in title_codes
    )
    ug_signal = bool(UNDERGRAD_TITLE_RE.search(thread_title)) or any(
        n < 500 for n in title_codes
    )
    if grad_signal and not ug_signal:
        return "grad"
    if ug_signal and not grad_signal:
        return "undergrad"
    if grad_signal and ug_signal:
        return "mixed"

    # No title signal: decide by the levels of courses discussed in the body.
    body_codes = [int(n) for n in COURSE_NUMBER_RE.findall(body)]
    grad_codes = sum(1 for n in body_codes if n >= 500)
    ug_codes = sum(1 for n in body_codes if n < 500)
    if grad_codes > ug_codes:
        return "grad"
    if ug_codes > grad_codes:
        return "undergrad"
    return "mixed"


def make_chunk(
    text: str,
    source_file: str,
    source_type: str,
    chunk_index: int,
    **extra,
) -> dict:
    chunk = {
        "text": text,
        "source_file": source_file,

        "source_type": source_type,
        "chunk_index": chunk_index,
    }
    chunk.update(extra)
    # A caller may pre-assign audience (Reddit uses a thread-level tag); only
    # fall back to the per-chunk code/keyword classifier when it didn't.
    if "audience" not in chunk:
        chunk["audience"] = classify_audience(text, extra.get("course_code"))
    return chunk


def chunk_reddit_file(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    parts = content.split("\n\n", 1)
    header = parts[0].strip()
    body = parts[1] if len(parts) > 1 else ""
    thread_match = REDDIT_HEADER_RE.match(header)
    thread_title = thread_match.group(1).strip() if thread_match else header
    thread_audience = classify_reddit_audience(thread_title, body)

    chunks: list[dict] = []
    index = 0
    record_index = 0
    for record in REDDIT_RECORD_SPLIT.split(body):
        record = record.strip()
        if not record:
            continue
        match = REDDIT_RECORD_RE.match(record)
        if not match:
            continue
        depth = match.group(2)
        reply_to = match.group(3)
        text = match.group(4).strip()

        # Keep the reply context inside the chunk text so replies like
        # "yeah that one's easy" stay interpretable on their own.
        if reply_to:
            text = f'(replying to: "{reply_to}")\n{text}'

        base_extra = {
            "thread_title": thread_title,
            "record_index": record_index,
            "audience": thread_audience,
        }
        if depth is not None:
            base_extra["depth"] = int(depth)
        if reply_to:
            base_extra["reply_to"] = reply_to

        pieces = maybe_split_reddit(text)
        for sub_idx, piece in enumerate(pieces):
            extra = dict(base_extra)
            if len(pieces) > 1:
                extra["subchunk_index"] = sub_idx
            chunks.append(make_chunk(piece, path.name, "reddit", index, **extra))
            index += 1
        record_index += 1
    return chunks


def chunk_rmp_file(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    parts = content.split("\n\n", 1)
    header = parts[0].strip()
    body = parts[1] if len(parts) > 1 else ""
    header_match = RMP_HEADER_RE.match(header)
    professor = header_match.group(1).strip() if header_match else header

    chunks: list[dict] = []
    index = 0
    for record in RMP_RECORD_SPLIT.split(body):
        record = record.strip()
        if not record:
            continue
        match = RMP_RECORD_RE.match(record)
        if not match:
            continue
        course_code = match.group(1).strip()
        rating = float(match.group(2))
        date = match.group(3).strip()
        text = match.group(4).strip()
        for piece in maybe_split(text):
            chunks.append(
                make_chunk(
                    piece,
                    path.name,
                    "rmp",
                    index,
                    professor=professor,
                    course_code=course_code,
                    rating=rating,
                    date=date,
                )
            )
            index += 1
    return chunks


def chunk_course_descriptions(path: Path) -> list[dict]:
    text = strip_boilerplate(path.read_text(encoding="utf-8")).strip()
    semester = semester_from_filename(path.name)
    blocks = [
        b.strip()
        for b in COURSE_CODE_LOOKAHEAD.split(text)
        if len(b.strip()) >= MIN_COURSE_BLOCK_CHARS and COURSE_HEADER_LINE.match(b)
    ]

    chunks: list[dict] = []
    index = 0
    for block in blocks:
        course_match = COURSE_CODE_EXTRACT.match(block)
        course_code = course_match.group(1).upper().replace("  ", " ") if course_match else None
        for piece in maybe_split(block):
            extra = {"semester": semester}
            if course_code:
                extra["course_code"] = course_code
            chunks.append(
                make_chunk(piece, path.name, "course_description", index, **extra)
            )
            index += 1
    return chunks


def chunk_course_schedule(path: Path) -> list[dict]:
    text = strip_boilerplate(path.read_text(encoding="utf-8")).strip()
    semester = semester_from_filename(path.name)
    chunks: list[dict] = []
    index = 0
    current_course_header = ""

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if COURSE_HEADER_LINE.match(line) and " cr" in line.lower():
            current_course_header = line
            continue
        if SCHEDULE_ROW_LINE.match(line) and current_course_header:
            record = f"{current_course_header}\n{line}"
            course_match = COURSE_CODE_EXTRACT.match(current_course_header)
            course_code = course_match.group(1).upper() if course_match else None
            for piece in maybe_split(record):
                extra = {"semester": semester}
                if course_code:
                    extra["course_code"] = course_code
                chunks.append(
                    make_chunk(piece, path.name, "course_schedule", index, **extra)
                )
                index += 1
    return chunks


def chunk_degree_requirements(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    chunks: list[dict] = []
    for index, piece in enumerate(maybe_split(text)):
        chunks.append(
            make_chunk(piece, path.name, "degree_requirement", index, degree_type="BS", start_term="Fall 2023")
        )
    return chunks


def chunk_reg_info(path: Path) -> list[dict]:
    text = strip_boilerplate(path.read_text(encoding="utf-8")).strip()
    semester = semester_from_filename(path.name)
    blocks = [
        b.strip()
        for b in REG_COURSE_BLOCK.split(text)
        if len(b.strip()) >= MIN_COURSE_BLOCK_CHARS and COURSE_HEADER_LINE.match(b)
    ]

    chunks: list[dict] = []
    index = 0
    for block in blocks:
        course_match = COURSE_CODE_EXTRACT.match(block)
        course_code = course_match.group(1).upper() if course_match else None
        for piece in maybe_split(block):
            extra = {"semester": semester}
            if course_code:
                extra["course_code"] = course_code
            chunks.append(make_chunk(piece, path.name, "reg_info", index, **extra))
            index += 1
    return chunks


def chunk_file(path: Path) -> list[dict]:
    source_type = source_type_from_filename(path.name)
    if source_type == "reddit":
        return chunk_reddit_file(path)
    if source_type == "rmp":
        return chunk_rmp_file(path)
    if source_type == "course_description":
        return chunk_course_descriptions(path)
    if source_type == "course_schedule":
        return chunk_course_schedule(path)
    if source_type == "reg_info":
        return chunk_reg_info(path)
    if source_type == "degree_requirement":
        return chunk_degree_requirements(path)
    return []


def _merge_key(chunk: dict) -> str:
    """Identity key for detecting cross-semester duplicate chunks.

    Two chunks are the same logical record when they share source_type,
    course_code, and the first 120 characters of their text — which happens
    when the same course block is extracted from both the Spring and Fall
    versions of the same document type.
    """
    return "|".join(
        [
            str(chunk.get("source_type", "")),
            str(chunk.get("course_code", "")),
            chunk.get("text", "")[:120].strip(),
        ]
    )


def merge_cross_semester_duplicates(chunks: list[dict]) -> list[dict]:
    """Collapse same-content chunks from different semesters into one.

    When two chunks have the same text and course code but come from different
    semester files (e.g. s26_course_description.txt and f26_course_description.txt),
    keep the first-seen chunk and append the second file's source_file and semester
    to the existing fields as comma-separated values.  This reduces corpus size
    while preserving full attribution.
    """
    seen: dict[str, int] = {}   # merge_key -> index in merged list
    merged: list[dict] = []

    for chunk in chunks:
        key = _merge_key(chunk)
        if key not in seen:
            seen[key] = len(merged)
            merged.append(chunk)
        else:
            existing = merged[seen[key]]
            # Append source_file if not already listed.
            new_src = chunk.get("source_file", "")
            if new_src and new_src not in existing.get("source_file", ""):
                existing["source_file"] = existing["source_file"] + ", " + new_src
            # Append semester if not already listed.
            new_sem = chunk.get("semester")
            if new_sem and new_sem not in str(existing.get("semester", "")):
                old_sem = existing.get("semester") or ""
                existing["semester"] = (old_sem + ", " + new_sem).lstrip(", ")

    return merged


def build_chunks() -> list[dict]:
    all_chunks: list[dict] = []
    for path in sorted(DOCS_DIR.glob("*.txt")):
        if path.name == ".gitkeep":
            continue
        file_chunks = chunk_file(path)
        print(f"chunked {path.name}: {len(file_chunks)} chunks")
        all_chunks.extend(file_chunks)

    before = len(all_chunks)
    all_chunks = merge_cross_semester_duplicates(all_chunks)
    after = len(all_chunks)
    if before != after:
        print(f"merged {before - after} cross-semester duplicate chunks ({before} -> {after})")

    return all_chunks


def write_chunks(chunks: list[dict], output_path: Path = CHUNKS_PATH) -> None:
    with output_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"wrote {len(chunks)} chunks -> {output_path}")


def inspect_chunks(chunks: list[dict]) -> None:
    """Print verification output required by Milestone 3."""
    print("\n=== Chunk Inspection ===")
    print(f"Total chunk count: {len(chunks)}")

    if len(chunks) < 50 or len(chunks) > 2000:
        print(
            f"WARNING: chunk count {len(chunks)} is outside the expected ~50-2000 range"
        )

    bad_chunks = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        if not text.strip():
            bad_chunks.append((i, "empty"))
        elif HTML_ARTIFACT_RE.search(text):
            bad_chunks.append((i, "html artifact"))
        elif len(text.split()) <= 3 and chunk["source_type"] in {"reddit", "rmp"}:
            bad_chunks.append((i, "very short fragment"))

    if bad_chunks:
        print(f"\nFlagged {len(bad_chunks)} bad chunks (showing up to 10):")
        for idx, reason in bad_chunks[:10]:
            preview = chunks[idx]["text"][:120].replace("\n", " ")
            print(f"  [{idx}] {reason}: {preview!r}")
    else:
        print("No empty, HTML-artifact, or one-line fragment chunks detected.")

    print("\nMetadata spot-check (source_file must match origin document):")
    for chunk in chunks[:3]:
        print(f"  {chunk['source_file']} -> {chunk['source_type']}")

    print("\n5 representative chunks:")
    if not chunks:
        print("  (none)")
        return

    # indices = sorted(
    #     {
    #         0,
    #         len(chunks) // 4,
    #         len(chunks) // 2,
    #         (3 * len(chunks)) // 4,
    #         len(chunks) - 1,
    #     }
    # )
    for idx in [randint(0, len(chunks)-1) for x in range(5)]:
        chunk = chunks[idx]
        print(f"\n--- Chunk #{idx} ---")
        for key, value in chunk.items():
            if key == "text":
                preview = value[:400] + ("..." if len(value) > 400 else "")
                print(f"text: {preview}")
            else:
                print(f"{key}: {value}")


def main() -> None:
    chunks = build_chunks()
    write_chunks(chunks)
    inspect_chunks(chunks)


if __name__ == "__main__":
    main()

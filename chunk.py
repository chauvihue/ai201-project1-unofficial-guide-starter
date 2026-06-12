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
# [A-Z]? covers honors/letter-prefixed numbers like "COMPSCI H311".
COURSE_CODE_LOOKAHEAD = re.compile(
    r"(?m)(?=^(?:CICS|COMPSCI|INFO|MATH)\s?[A-Z]?\d{3}\b)", re.I
)
COURSE_HEADER_LINE = re.compile(
    r"^(?:CICS|COMPSCI|INFO|MATH)\s+[A-Z]?\d{3}\b", re.I
)
COURSE_CODE_EXTRACT = re.compile(
    r"^((?:CICS|COMPSCI|INFO|MATH)\s?[A-Z]?\d{3})", re.I
)
SCHEDULE_ROW_LINE = re.compile(r"^U\d+\s", re.I)
REG_COURSE_BLOCK = re.compile(
    r"(?m)(?=^(?:CICS|COMPSCI|INFO|MATH)\s+[A-Z]?\d{3}\b)", re.I
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
    return chunk


def chunk_reddit_file(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    parts = content.split("\n\n", 1)
    header = parts[0].strip()
    body = parts[1] if len(parts) > 1 else ""
    thread_match = REDDIT_HEADER_RE.match(header)
    thread_title = thread_match.group(1).strip() if thread_match else header

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

        base_extra = {"thread_title": thread_title, "record_index": record_index}
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
        class_name = match.group(1).strip()
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
                    class_name=class_name,
                    rating=rating,
                    date=date,
                )
            )
            index += 1
    return chunks


def chunk_course_descriptions(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
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
    text = path.read_text(encoding="utf-8").strip()
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


def chunk_reg_info(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
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
    return []


def build_chunks() -> list[dict]:
    all_chunks: list[dict] = []
    for path in sorted(DOCS_DIR.glob("*.txt")):
        if path.name == ".gitkeep":
            continue
        file_chunks = chunk_file(path)
        print(f"chunked {path.name}: {len(file_chunks)} chunks")
        all_chunks.extend(file_chunks)
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

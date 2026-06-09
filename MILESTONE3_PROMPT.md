# Milestone 3 Implementation Brief — Document Ingestion & Chunking

> **For the AI coding agent (Cursor):** This file is your spec. Implement **only** the
> ingestion and chunking stages described here. Do **not** embed, build a vector store,
> call an LLM, or build a UI — those are Milestones 4–5 and are explicitly out of scope.
> Follow the chunk sizes, overlap, and record-based strategy exactly as stated; if you
> believe a parameter should change, stop and flag it rather than silently deviating, so
> `planning.md` can be updated to match.

---

## 1. Project context

We are building **The Unofficial Guide**: a RAG system that answers plain-language
questions about UMass Amherst CS course selection (electives ~200–500 level) using
*student-generated* knowledge — Reddit threads, Rate My Professor reviews — alongside
official UMass course PDFs.

This milestone covers **Stage 1 (Ingestion) and Stage 2 (Chunking)** of the pipeline:

```
Sources → [Scrape + clean → documents/ cache] → [Chunk into records] → (Embed: M4) → (Generate: M5)
```

**Authoritative spec:** read `planning.md` in the repo root before writing code — it
contains the domain, full document list (24 sources), chunking strategy, and architecture
diagram. This brief operationalizes that spec; if they ever conflict, `planning.md` wins
and you should flag the conflict.

**Existing scaffold:** `scrape.py` already exists as a skeleton with config dicts,
function stubs, and `TODO` markers. Build on it — do not rewrite it from scratch.

---

## 2. Design principles (non-negotiable)

1. **Decouple scraping from query time.** Scraping runs in `scrape.py`, writes raw cleaned
   text to `documents/`, and is re-runnable to "refresh" the corpus. Nothing scrapes the
   web at query time. This keeps evaluation reproducible.
2. **Prefer structured sources over rendered HTML.** Use APIs/JSON endpoints (PRAW, RMP
   GraphQL) rather than scraping rendered pages. Drop to a browser only if an API is
   unavailable.
3. **One semantic unit = one record = one chunk.** A Reddit comment, an RMP review, and a
   single course description block are each self-contained. Keep them whole.
4. **Every chunk carries source metadata** sufficient for attribution later (Milestone 5
   requires citing which document each answer came from).
5. **Inspect before trusting.** Cleaning and chunking must be eyeballed on real output, not
   assumed correct.

---

## 3. Stage 1 — Ingestion (`scrape.py`)

Output target: one cleaned cache file per source in `documents/`, formatted so the chunker
can split it into records.

```
documents/reddit_<slug>.txt     # one comment per record
documents/rmp_<slug>.txt        # one review per record
documents/<name>.txt            # extracted from each <name>.pdf
```

### 3a. Reddit (PRAW)
- Source: 8 threads listed in `planning.md` (Documents table, rows 1–8). Thread IDs go in
  the `REDDIT_THREADS` dict in `scrape.py`.
- Use **PRAW** with credentials from `.env` (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`,
  `REDDIT_USER_AGENT` — a "script" app at reddit.com/prefs/apps).
- Walk the full comment tree: `submission.comments.replace_more(limit=None)` then
  `.comments.list()` to flatten.
- **One comment = one record.** Prepend the **thread title** as a file header so a bare
  comment like "yeah it's an easy A" retains the context of *which course* the thread is about.
- **Skip:** `[deleted]` / `[removed]` bodies, AutoModerator, and bot comments
  ("I am a bot").
- Fallback if PRAW auth is unavailable: the `.json` endpoint (append `.json` to the thread
  URL) with a real `User-Agent` header. Note this in a comment if used.

### 3b. Rate My Professor (`ratemyprofessor` / GraphQL)
- Source: 10 professors listed in `planning.md` (rows 9–18). Professor IDs go in the
  `RMP_PROFESSORS` dict.
- Use the **`ratemyprofessor`** package (wraps RMP's GraphQL backend). RMP is JS-rendered,
  so plain `requests` will not work — do not attempt HTML scraping.
- **IMPORTANT:** field/method names vary by package version. Before relying on them, print
  one professor object and one rating object and confirm the real attribute names
  (comment text, class name, overall rating, date). Adjust the code to match what the
  installed version actually returns.
- **One review = one record.** Capture per review: comment text, the class it's about (if
  present), the numeric rating, and the date — you'll want date/rating available as
  metadata later (supports the "filter outdated ratings" challenge and the metadata-filtering
  stretch).
- Fallback: manual copy into `documents/rmp_<slug>.txt` is acceptable per the project rules
  if scraping is blocked.

### 3c. UMass PDFs (pdfplumber)
- Source: 6 PDFs already on disk in `documents/` (`s26_*`, `f26_*` — descriptions,
  schedules, reg info).
- Extract with **pdfplumber**: `"\n\n".join(p.extract_text() for p in pdf.pages if p.extract_text())`.
- **Strip repeating page headers/footers** — every page repeats
  `"UMassAmherst Course Descriptions / Manning College... / <semester>"`. These pollute
  chunks; remove them.
- Write each PDF's text to a parallel `documents/<name>.txt`.

### 3d. Cleaning (`clean_text`, shared)
Implement the cleaning the skeleton stubs out. Remove:
- HTML entities → decode (`&amp;`→`&`, `&gt;`→`>`, `&#39;`→`'`).
- Markdown link syntax `[text](url)` → `text`; strip bare URLs.
- Reddit quote markers (`>`), edit footers (`EDIT:`, `Edit:`).
- Runs of whitespace/blank lines → collapse.

**Checkpoint:** after running, open one `reddit_*.txt` and one `rmp_*.txt` by eye. If you
see `&gt;`, `[deleted]`, leftover markdown, or bot text, fix cleaning before proceeding.

---

## 4. Stage 2 — Chunking (`chunk.py`)

Create a new module `chunk.py` that reads the cached `documents/*.txt` files and produces a
list of chunk objects ready for embedding in Milestone 4.

### 4a. Strategy (per `planning.md` Chunking section)
Chunking is **type-aware**, because the corpus has three shapes:

| Source type | Strategy | Overlap |
|---|---|---|
| Reddit comments | record-based: one comment → one chunk | 0 |
| RMP reviews | record-based: one review → one chunk | 0 |
| Course descriptions | record-based: split on course-code line, one course block → one chunk | 0 |
| Course schedule / reg info | record-based: one table row/course entry → one chunk | 0 |
| **Fallback for any text that exceeds the size cap** | recursive character splitting | 150 chars |

- **Course-description splitting:** split on the course-code pattern. The descriptions are
  structured records beginning with lines like `CICS 110 ...`, `COMPSCI 305 ...`,
  `INFO 248 ...`. Use a regex lookahead, e.g. `(?=(?:CICS|COMPSCI|INFO|MATH)\s?\d{3})`, so
  each course's title + instructor(s) + description + prerequisites + credits stay in one
  chunk. (Measured reference: ~102 course blocks, median ~851 chars, max ~2655.)
- **Size cap / recursive fallback:** if any single record exceeds **~1000–1200 characters**,
  fall back to recursive character splitting (LangChain `RecursiveCharacterTextSplitter`
  splitting on `\n\n` → `\n` → sentence → char) with **150-char overlap** to stitch the
  split. Records under the cap are emitted whole with no overlap.
- **Filter empties:** drop any chunk with `len(chunk.strip()) == 0`.

### 4b. Chunk object shape
Each chunk must carry metadata for attribution. Produce a list of dicts (or dataclasses):

```python
{
    "text": "<the chunk content>",
    "source_file": "reddit_easy_200_cs_courses.txt",
    "source_type": "reddit" | "rmp" | "course_description" | "course_schedule" | "reg_info",
    "chunk_index": 0,            # position within its source document
    # type-specific (include when available):
    "thread_title": "...",       # reddit
    "professor": "...",          # rmp
    "class_name": "...",         # rmp
    "rating": 4.5,               # rmp
    "date": "...",               # rmp
    "course_code": "COMPSCI 305" # course_description
    "semester": "Spring 2026"    # course_* / reg_info
}
```

Persist the chunk list to disk (e.g. `chunks.jsonl`) so Milestone 4 can load it without
re-scraping.

### 4c. Verification (mirrors the M3 checkpoint — implement this, don't skip)
Add an inspection routine (script or notebook cell) that:
1. **Prints 5 representative chunks** with their metadata. Each must be readable,
   substantive, and self-contained — answerable on its own.
2. **Prints the total chunk count.** Sanity range across the corpus: **roughly 50–2000**.
   Far under 50 → chunks too large; far over 2000 → too small. Report the number.
3. **Flags bad chunks:** any empty strings, HTML artifacts (`<`, `&gt;`), or one-line
   fragments with no standalone meaning.
4. **Spot-checks metadata:** confirm `source_file` matches the document a chunk actually
   came from.

---

## 5. Suggested file layout

```
scrape.py        # Stage 1: scrape + clean → documents/*.txt   (extend existing skeleton)
chunk.py         # Stage 2: documents/*.txt → chunks.jsonl + inspection
documents/       # raw cache (PDFs already here; scraped .txt land here)
chunks.jsonl     # output artifact for Milestone 4
requirements.txt # add deps below
.env             # API keys (never commit)
```

---

## 6. Dependencies & setup
Add to `requirements.txt` and install:
```
praw
ratemyprofessor
pdfplumber
python-dotenv
langchain-text-splitters   # for RecursiveCharacterTextSplitter (or implement equivalently)
```
`.env` additions (alongside existing `GROQ_API_KEY`):
```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=unofficial-guide/0.1 by u/<username>
```

---

## 7. Out of scope (do NOT do in this milestone)
- ❌ Embedding chunks / `SentenceTransformer` / `all-MiniLM-L6-v2`
- ❌ ChromaDB or any vector store
- ❌ Groq / any LLM call, prompt engineering, generation
- ❌ Gradio / Streamlit / any UI
- ❌ The course-description sub-chunking-by-density idea (flagged in `planning.md` as a
  future stretch feature — not now)

---

## 8. Definition of done
- [ ] `scrape.py` runs end-to-end and writes cleaned `documents/*.txt` for all reachable
      Reddit threads, RMP professors, and all 6 PDFs.
- [ ] A manually-read sample of one Reddit and one RMP file is free of HTML entities,
      markdown junk, `[deleted]`, and bot text.
- [ ] `chunk.py` produces `chunks.jsonl` with type-aware records and full metadata.
- [ ] The inspection routine prints 5 clean representative chunks and the total count, and
      the count is within ~50–2000.
- [ ] No embedding/vector/LLM/UI code was added.
- [ ] Any deviation from the chunk size / overlap / strategy in `planning.md` was flagged,
      not made silently.
```

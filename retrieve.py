"""
retrieve.py - Milestone 4: embedding and retrieval.

Loads chunks.jsonl from the Milestone 3 chunking pipeline, embeds each chunk
with all-MiniLM-L6-v2, stores the vectors in persistent ChromaDB with source
metadata, and exposes a top-k retrieval function for Milestone 5 generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from sentence_transformers import SentenceTransformer


CHUNKS_PATH = Path("chunks.jsonl")
CHROMA_DIR = Path("chroma_db")
COLLECTION_NAME = "unofficial_guide_chunks"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_TOP_K = 5
BATCH_SIZE = 64

# Soft re-ranking: over-fetch a wider candidate pool, then nudge candidates
# whose audience matches the query's intent up and definite mismatches down,
# before truncating to top_k. "mixed"/None audiences are left untouched so an
# un-leveled-but-relevant chunk (e.g. an MS Reddit thread) is never excluded.
OVERFETCH_MIN = 20
OVERFETCH_FACTOR = 4
MATCH_BONUS = 0.10
MISMATCH_PENALTY = 0.10

# Query-intent cues. Grad keywords mirror chunk.py's audience classifier; the
# undergrad set covers class-year and degree-track phrasing. \bgrad\b avoids
# matching "grade"/"undergrad" (see chunk.py for the boundary reasoning).
GRAD_INTENT_RE = re.compile(
    r"\bMS\b|\bmaster'?s?\b|\bgraduate\b|\bgrad\b|\bPh\.?D\b", re.I
)
UNDERGRAD_INTENT_RE = re.compile(
    r"\bfreshman\b|\bfirst[- ]?year\b|\bsophomore\b|\bjunior\b|\bsenior\b"
    r"|\bundergrad(?:uate)?\b|\bB[AS]\b",
    re.I,
)


def detect_query_intent(query: str) -> str | None:
    """Classify a query as targeting "grad" or "undergrad" content, else None."""
    if GRAD_INTENT_RE.search(query):
        return "grad"
    if UNDERGRAD_INTENT_RE.search(query):
        return "undergrad"
    return None


def load_chunks(path: Path = CHUNKS_PATH) -> list[dict[str, Any]]:
    """Load chunk records produced by chunk.py."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python chunk.py` before building embeddings."
        )

    chunks: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if not chunk.get("text", "").strip():
                continue
            chunk["_line_number"] = line_number
            chunks.append(chunk)
    return chunks


def make_chunk_id(chunk: dict[str, Any]) -> str:
    """Create a stable unique ID for Chroma upserts."""
    identity = "|".join(
        [
            str(chunk.get("source_file", "")),
            str(chunk.get("source_type", "")),
            str(chunk.get("chunk_index", "")),
            str(chunk.get("record_index", "")),
            str(chunk.get("subchunk_index", "")),
            chunk.get("text", ""),
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return f"chunk-{chunk.get('_line_number', 0)}-{digest}"


def clean_metadata(chunk: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Keep Chroma-compatible metadata values and exclude the document text."""
    metadata: dict[str, str | int | float | bool] = {}
    for key, value in chunk.items():
        if key == "text" or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            metadata[key] = value
        else:
            metadata[key] = json.dumps(value, ensure_ascii=False)
    return metadata


def get_embedding_model() -> SentenceTransformer:
    """Load the local SentenceTransformers embedding model."""
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def get_collection(reset: bool = False) -> Collection:
    """Open the persistent Chroma collection.

    PersistentClient stores vectors on disk in chroma_db/. get_or_create_collection
    either opens an existing collection or creates it on first run. The
    hnsw:space metadata tells Chroma to use cosine distance for nearest-neighbor
    search, which matches the planning.md retrieval approach.
    """
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def batched(items: list[Any], batch_size: int) -> list[list[Any]]:
    """Return fixed-size batches for embedding/upserting."""
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def build_vector_store(
    chunks_path: Path = CHUNKS_PATH,
    reset: bool = True,
    batch_size: int = BATCH_SIZE,
) -> Collection:
    """Embed chunks and persist them to ChromaDB."""
    chunks = load_chunks(chunks_path)
    if not chunks:
        raise ValueError(f"No chunks found in {chunks_path}.")

    model = get_embedding_model()
    collection = get_collection(reset=reset)

    for batch in batched(chunks, batch_size):
        documents = [chunk["text"] for chunk in batch]
        ids = [make_chunk_id(chunk) for chunk in batch]
        metadatas = [clean_metadata(chunk) for chunk in batch]
        embeddings = model.encode(
            documents,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

        # upsert is idempotent: same ID updates the stored vector/metadata
        # instead of creating duplicate rows.
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    return collection


def retrieve(query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    """Return the top-k most relevant chunks for a plain-language query."""
    if not query.strip():
        raise ValueError("Query must not be empty.")

    model = get_embedding_model()
    collection = get_collection(reset=False)
    query_embedding = model.encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()[0]

    # Over-fetch a wider pool so re-ranking has room to reorder, capped at the
    # collection size to avoid Chroma's "n_results > count" warning.
    overfetch = min(max(top_k * OVERFETCH_FACTOR, OVERFETCH_MIN), collection.count())
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=overfetch,
        include=["documents", "metadatas", "distances"],
    )

    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    intent = detect_query_intent(query)

    candidates: list[dict[str, Any]] = []
    for chunk_id, document, metadata, distance in zip(
        ids, documents, metadatas, distances
    ):
        # Lower is better (cosine distance). A match pulls the score down, a
        # definite mismatch pushes it up; "mixed"/None/missing stay neutral.
        adjusted = distance
        if intent is not None:
            audience = metadata.get("audience")
            if audience == intent:
                adjusted -= MATCH_BONUS
            elif audience in ("grad", "undergrad"):  # the opposite, definite level
                adjusted += MISMATCH_PENALTY
        candidates.append(
            {
                "id": chunk_id,
                "distance": distance,
                "adjusted_distance": adjusted,
                "text": document,
                "metadata": metadata,
            }
        )

    candidates.sort(key=lambda c: c["adjusted_distance"])

    retrieved: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates[:top_k], start=1):
        candidate["rank"] = rank
        retrieved.append(candidate)
    return retrieved


def print_results(results: list[dict[str, Any]]) -> None:
    """Pretty-print retrieval output for manual inspection."""
    for result in results:
        metadata = result["metadata"]
        source = metadata.get("source_file", "unknown")
        source_type = metadata.get("source_type", "unknown")
        adjusted = result.get("adjusted_distance", result["distance"])
        print(
            f"\n#{result['rank']} distance={result['distance']:.4f} "
            f"adjusted={adjusted:.4f} audience={metadata.get('audience')}"
        )
        print(f"source={source} type={source_type}")
        if "course_code" in metadata:
            print(f"course_code={metadata['course_code']}")
        if "thread_title" in metadata:
            print(f"thread_title={metadata['thread_title']}")
        if "professor" in metadata:
            print(f"professor={metadata['professor']}")
        print(result["text"][:800])


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed and retrieve course-advice chunks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build the Chroma vector store.")
    build_parser.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    build_parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not delete the existing collection before upserting.",
    )

    query_parser = subparsers.add_parser("query", help="Retrieve chunks for a query.")
    query_parser.add_argument("query")
    query_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)

    args = parser.parse_args()
    if args.command == "build":
        collection = build_vector_store(args.chunks, reset=not args.no_reset)
        print(f"Stored {collection.count()} chunks in {CHROMA_DIR / COLLECTION_NAME}.")
    elif args.command == "query":
        print_results(retrieve(args.query, top_k=args.top_k))


if __name__ == "__main__":
    main()

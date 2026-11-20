"""Section-aware chunking and BM25 index construction for the billing guide.

The guide is split on ``##`` and ``###`` headings. Every heading that carries
body text becomes one chunk with its section number (``"3.2"``), heading text,
parent heading and body. A ``##`` heading whose body is empty (because it only
introduces ``###`` subsections) is not emitted as its own chunk; its title is
recorded as ``parent_heading`` of the subsections instead.

Run ``python -m app.indexer`` to (re)build ``index.json``. The Docker image
does this at build time, and the server only loads the prebuilt file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.bm25 import BM25Index
from app.text import tokenize, tokenize_query

logger = logging.getLogger(__name__)

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCUMENT_PATH = SERVICE_ROOT / "data" / "Mobile_Provider_Billing_Guide.md"
DEFAULT_INDEX_PATH = SERVICE_ROOT / "data" / "index.json"
DEFAULT_DOCUMENT_ID = "MOB-BILLING-2026-01"
INDEX_FORMAT_VERSION = 1

# How many times heading words are repeated in the indexed token stream, so a
# query term that appears in a section title weighs more than one in the body.
HEADING_BOOST = 2

_HEADING_RE = re.compile(r"^(#{2,3})\s+(\d+(?:\.\d+)*)\.?\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_DOC_ID_RE = re.compile(r"\*\*Document ID:\*\*\s*([A-Za-z0-9._-]+)")


@dataclass(frozen=True)
class Chunk:
    section: str
    heading: str
    parent_heading: str | None
    level: int
    text: str


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks (kept in the returned text, not indexed)."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def split_sections(markdown: str) -> list[Chunk]:
    """Split markdown into heading-delimited chunks.

    Text before the first ``##`` heading (title and metadata) is not a section
    and is not returned. Headings inside fenced code blocks are ignored.
    """
    chunks: list[Chunk] = []
    current: dict[str, Any] | None = None
    parent_heading: str | None = None
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        if current is None:
            return
        text = "\n".join(body).strip()
        if text:
            chunks.append(Chunk(text=text, **current))

    for line in markdown.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match is None:
            if current is not None:
                body.append(line)
            continue
        flush()
        hashes, number, title = match.groups()
        level = len(hashes)
        if level == 2:
            parent_heading = title
            current = {"section": number, "heading": title, "parent_heading": None, "level": 2}
        else:
            current = {"section": number, "heading": title, "parent_heading": parent_heading, "level": 3}
        body = []
    flush()
    return chunks


def extract_document_id(markdown: str, default: str = DEFAULT_DOCUMENT_ID) -> str:
    match = _DOC_ID_RE.search(markdown)
    return match.group(1) if match else default


def chunk_tokens(chunk: Chunk) -> list[str]:
    """Tokens indexed for a chunk: boosted heading, parent heading, body."""
    tokens: list[str] = []
    for _ in range(HEADING_BOOST):
        tokens.extend(tokenize(chunk.heading))
    if chunk.parent_heading:
        tokens.extend(tokenize(chunk.parent_heading))
    tokens.extend(tokenize(_strip_code_blocks(chunk.text)))
    return tokens


def build_index(document_path: Path = DEFAULT_DOCUMENT_PATH) -> dict[str, Any]:
    """Read the guide, chunk it and return the serialisable index payload."""
    raw = document_path.read_bytes()
    markdown = raw.decode("utf-8")
    chunks = split_sections(markdown)
    if not chunks:
        raise ValueError(f"No sections found in {document_path}")
    bm25 = BM25Index.build([chunk_tokens(c) for c in chunks])
    return {
        "format_version": INDEX_FORMAT_VERSION,
        "document": document_path.name,
        "document_id": extract_document_id(markdown),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "chunks": [asdict(c) for c in chunks],
        "bm25": bm25.to_dict(),
    }


def write_index(index: dict[str, Any], index_path: Path = DEFAULT_INDEX_PATH) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


class BillingIndex:
    """Loaded, query-ready index. Built once; ``search`` never re-indexes."""

    def __init__(self, payload: dict[str, Any]):
        if payload.get("format_version") != INDEX_FORMAT_VERSION:
            raise ValueError(f"Unsupported index format: {payload.get('format_version')!r}")
        self.document: str = payload["document"]
        self.document_id: str = payload["document_id"]
        self.source_sha256: str = payload["source_sha256"]
        self.chunks: list[dict[str, Any]] = payload["chunks"]
        self.bm25 = BM25Index.from_dict(payload["bm25"])

    @classmethod
    def load(cls, index_path: Path = DEFAULT_INDEX_PATH) -> BillingIndex:
        return cls(json.loads(index_path.read_text(encoding="utf-8")))

    @classmethod
    def load_or_build(
        cls, index_path: Path = DEFAULT_INDEX_PATH, document_path: Path = DEFAULT_DOCUMENT_PATH
    ) -> BillingIndex:
        """Load the prebuilt index; build it once (with a warning) if missing."""
        if index_path.exists():
            index = cls.load(index_path)
            if document_path.exists():
                current = hashlib.sha256(document_path.read_bytes()).hexdigest()
                if current != index.source_sha256:
                    logger.warning("Index %s is stale (document changed); rebuilding once at startup", index_path)
                    return cls(build_index(document_path))
            logger.info("Loaded index %s: %d sections from %s", index_path, len(index.chunks), index.document)
            return index
        logger.warning("Index %s not found; building it once at startup", index_path)
        return cls(build_index(document_path))

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        terms = tokenize_query(query)
        results = []
        for doc_id, score in self.bm25.search(terms, top_k):
            chunk = self.chunks[doc_id]
            results.append(
                {
                    "section": chunk["section"],
                    "heading": chunk["heading"],
                    "parent_heading": chunk["parent_heading"],
                    "text": chunk["text"],
                    "score": round(score, 4),
                }
            )
        return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the BM25 index for the billing guide.")
    parser.add_argument("--document", type=Path, default=Path(os.getenv("MCP_DOCUMENT_PATH", DEFAULT_DOCUMENT_PATH)))
    parser.add_argument("--output", type=Path, default=Path(os.getenv("MCP_INDEX_PATH", DEFAULT_INDEX_PATH)))
    args = parser.parse_args(argv)

    index = build_index(args.document)
    write_index(index, args.output)
    print(
        f"Indexed {len(index['chunks'])} sections of {index['document']} "
        f"({index['document_id']}) -> {args.output}"
    )
    for chunk in index["chunks"]:
        print(f"  {chunk['section']:>4}  {chunk['heading']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

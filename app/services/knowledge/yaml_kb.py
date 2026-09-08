"""YAML-backed knowledge base with keyword retrieval + a redaction lint.

Phase 3 stand-in for the pgvector KB (plan Phase 8). Retrieval is deliberately
simple: lowercased token overlap between the query and each chunk's
title/tags/text, tags weighted higher.

The redaction lint (critique B3) runs the guard's Tier-1 detectors over every
chunk at load time and REFUSES to load any chunk containing a blocked figure —
redaction is enforced here, not left to whoever edits the YAML.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.logging_config import get_logger
from app.services.guard import detectors
from app.services.knowledge.base import KBChunk, KnowledgeBase

logger = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "are",
    "i", "my", "me", "you", "your", "we", "our", "it", "this", "that", "with",
    "what", "how", "do", "does", "can", "about", "tell", "want", "know",
}


class RedactionError(RuntimeError):
    """Raised when a KB chunk contains content that must never be embedded."""


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1}


def _lint_chunk(chunk_id: str, text: str) -> None:
    problems: list[str] = []
    if detectors.find_money(text):
        problems.append(f"cost figure(s): {detectors.find_money(text)}")
    if detectors.find_financing(text):
        problems.append(f"financing term(s): {detectors.find_financing(text)}")
    if detectors.find_pg_cost(text):
        problems.append("PG cost context")
    if problems:
        raise RedactionError(f"KB chunk {chunk_id!r} failed redaction lint: {'; '.join(problems)}")


class YamlKnowledgeBase(KnowledgeBase):
    def __init__(self, chunks: list[KBChunk]) -> None:
        self._chunks = chunks
        self._index: list[tuple[KBChunk, set[str]]] = [
            (c, _tokens(f"{c.title} {' '.join(c.tags)} {c.text}")) for c in chunks
        ]

    @classmethod
    def from_path(cls, path: str | Path, *, strict: bool = True) -> YamlKnowledgeBase:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        loaded: list[KBChunk] = []
        for entry in raw.get("chunks", []):
            cid = str(entry.get("id") or f"chunk-{len(loaded)}")
            text = (entry.get("text") or "").strip()
            try:
                _lint_chunk(cid, text)
            except RedactionError as exc:
                if strict:
                    raise
                logger.error("skipping KB chunk: %s", exc)
                continue
            loaded.append(
                KBChunk(
                    id=cid,
                    title=str(entry.get("title") or cid),
                    text=text,
                    tags=tuple(str(t).lower() for t in (entry.get("tags") or [])),
                )
            )
        logger.info("knowledge base loaded: %d chunks from %s", len(loaded), path)
        return cls(loaded)

    def retrieve(self, query: str, *, k: int = 4) -> list[KBChunk]:
        q = _tokens(query)
        if not q:
            return []
        scored: list[KBChunk] = []
        for chunk, chunk_tokens in self._index:
            tag_tokens = {t for tag in chunk.tags for t in _TOKEN.findall(tag.lower())}
            overlap = q & chunk_tokens
            if not overlap:
                continue
            score = len(overlap) + 1.5 * len(q & tag_tokens)
            scored.append(
                KBChunk(
                    id=chunk.id,
                    title=chunk.title,
                    text=chunk.text,
                    tags=chunk.tags,
                    score=round(score, 2),
                )
            )
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]

    @property
    def size(self) -> int:
        return len(self._chunks)


def load_knowledge_base(path: str | Path, *, strict: bool = True) -> YamlKnowledgeBase:
    return YamlKnowledgeBase.from_path(path, strict=strict)

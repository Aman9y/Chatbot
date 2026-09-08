from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class KBChunk:
    id: str
    title: str
    text: str
    tags: tuple[str, ...] = ()
    score: float = 0.0


class KnowledgeBase(abc.ABC):
    @abc.abstractmethod
    def retrieve(self, query: str, *, k: int = 4) -> list[KBChunk]:
        """Return up to k chunks most relevant to the query, highest score first."""

    @property
    @abc.abstractmethod
    def size(self) -> int: ...

"""Minimal Okapi BM25 implementation (no external search dependencies).

score(D, Q) = sum over q in Q of
    IDF(q) * f(q, D) * (k1 + 1) / (f(q, D) + k1 * (1 - b + b * |D| / avgdl))

with the non-negative IDF variant  IDF(q) = ln(1 + (N - n(q) + 0.5) / (n(q) + 0.5)).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


@dataclass
class BM25Index:
    """Inverted-index style BM25 over a list of pre-tokenized documents."""

    k1: float = DEFAULT_K1
    b: float = DEFAULT_B
    doc_lengths: list[int] = field(default_factory=list)
    term_freqs: list[dict[str, int]] = field(default_factory=list)
    doc_freq: dict[str, int] = field(default_factory=dict)
    avgdl: float = 0.0

    @classmethod
    def build(cls, documents: list[list[str]], k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> BM25Index:
        index = cls(k1=k1, b=b)
        df: Counter[str] = Counter()
        for tokens in documents:
            tf = Counter(tokens)
            index.term_freqs.append(dict(tf))
            index.doc_lengths.append(len(tokens))
            df.update(tf.keys())
        index.doc_freq = dict(df)
        index.avgdl = (sum(index.doc_lengths) / len(index.doc_lengths)) if index.doc_lengths else 0.0
        return index

    @property
    def n_docs(self) -> int:
        return len(self.doc_lengths)

    def idf(self, term: str) -> float:
        n_q = self.doc_freq.get(term, 0)
        return math.log(1.0 + (self.n_docs - n_q + 0.5) / (n_q + 0.5))

    def score(self, query_terms: list[str], doc_id: int) -> float:
        tf = self.term_freqs[doc_id]
        dl = self.doc_lengths[doc_id]
        norm = self.k1 * (1.0 - self.b + self.b * dl / self.avgdl) if self.avgdl else self.k1
        total = 0.0
        for term in query_terms:
            f = tf.get(term)
            if not f:
                continue
            total += self.idf(term) * f * (self.k1 + 1.0) / (f + norm)
        return total

    def search(self, query_terms: list[str], top_k: int) -> list[tuple[int, float]]:
        """Return up to ``top_k`` ``(doc_id, score)`` pairs with score > 0, best first.

        Ties are broken by document order so results are deterministic.
        """
        terms = list(dict.fromkeys(query_terms))
        scored = [(doc_id, self.score(terms, doc_id)) for doc_id in range(self.n_docs)]
        scored = [item for item in scored if item[1] > 0.0]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:top_k]

    def to_dict(self) -> dict[str, Any]:
        return {
            "k1": self.k1,
            "b": self.b,
            "avgdl": self.avgdl,
            "doc_lengths": self.doc_lengths,
            "term_freqs": self.term_freqs,
            "doc_freq": self.doc_freq,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BM25Index:
        return cls(
            k1=float(data["k1"]),
            b=float(data["b"]),
            avgdl=float(data["avgdl"]),
            doc_lengths=[int(x) for x in data["doc_lengths"]],
            term_freqs=[{str(k): int(v) for k, v in tf.items()} for tf in data["term_freqs"]],
            doc_freq={str(k): int(v) for k, v in data["doc_freq"].items()},
        )

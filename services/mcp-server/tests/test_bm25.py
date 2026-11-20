import math

import pytest

from app.bm25 import BM25Index

DOCS = [
    ["internet", "block", "cost", "10"],
    ["phone", "minute", "block", "cost"],
    ["dispute", "window", "15", "day"],
    ["internet", "internet", "allowance", "20", "gb"],
]


@pytest.fixture
def bm25() -> BM25Index:
    return BM25Index.build(DOCS)


def test_defaults_and_stats(bm25):
    assert (bm25.k1, bm25.b) == (1.5, 0.75)
    assert bm25.n_docs == 4
    assert bm25.avgdl == pytest.approx(17 / 4)
    assert bm25.doc_freq["internet"] == 2


def test_rare_terms_have_higher_idf(bm25):
    assert bm25.idf("dispute") > bm25.idf("internet") > 0
    assert bm25.idf("unknown") == pytest.approx(math.log(1 + (4 + 0.5) / 0.5))


def test_ranking_prefers_matching_and_frequent_terms(bm25):
    results = bm25.search(["internet"], top_k=4)
    assert [doc for doc, _ in results] == [3, 0]  # tf=2 beats tf=1; non-matching docs excluded
    assert results[0][1] > results[1][1] > 0


def test_multi_term_query(bm25):
    top_doc, _ = bm25.search(["phone", "cost"], top_k=1)[0]
    assert top_doc == 1


def test_top_k_and_no_match(bm25):
    assert len(bm25.search(["block", "cost"], top_k=1)) == 1
    assert bm25.search(["nothing"], top_k=3) == []


def test_ties_are_deterministic():
    bm25 = BM25Index.build([["a", "b"], ["a", "b"]])
    assert [d for d, _ in bm25.search(["a"], top_k=2)] == [0, 1]


def test_serialisation_roundtrip(bm25):
    restored = BM25Index.from_dict(bm25.to_dict())
    assert restored.search(["internet", "cost"], 4) == bm25.search(["internet", "cost"], 4)

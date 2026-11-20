"""The three required retrieval demos (plus a dispute query) against the real guide."""

import pytest


def sections(billing_index, query, top_k=3):
    return [m["section"] for m in billing_index.search(query, top_k)]


def test_extra_internet_cost(billing_index):
    result = sections(billing_index, "How much does extra internet cost?")
    assert result[0] in {"3.2", "8.1"}
    assert {"3.2", "8.1"} <= set(result)


def test_partial_payment(billing_index):
    result = sections(billing_index, "Can I pay only part of my bill?")
    assert result[0] in {"5.1", "8.3"}
    assert {"5.1", "8.3"} <= set(result)


def test_due_date_and_late_payment(billing_index):
    result = sections(billing_index, "When is my bill due and what happens if I pay late?")
    assert result[0] in {"6.1", "6.2", "6.3"}
    assert set(result) == {"6.1", "6.2", "6.3"}


@pytest.mark.parametrize("query", ["How can I dispute a charge?", "My bill is wrong, how do I file a complaint?"])
def test_dispute(billing_index, query):
    result = sections(billing_index, query)
    assert result[0].startswith("7.") or result[0] == "8.4"
    assert any(s.startswith("7.") for s in result)


def test_results_are_ranked_and_carry_section_references(billing_index):
    matches = billing_index.search("additional minutes price", 5)
    assert matches[0]["section"] == "2.2"
    scores = [m["score"] for m in matches]
    assert scores == sorted(scores, reverse=True)
    for m in matches:
        assert set(m) == {"section", "heading", "parent_heading", "text", "score"}


def test_unrelated_query_returns_no_matches(billing_index):
    assert billing_index.search("zebra giraffe", 3) == []

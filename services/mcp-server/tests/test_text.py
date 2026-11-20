from app.text import expand_query, stem, tokenize, tokenize_query


def test_tokenize_lowercases_and_strips_punctuation_and_stopwords():
    assert tokenize("How much does the EXTRA Internet cost?!") == ["extra", "internet", "cost"]


def test_stemming_maps_inflections_to_same_term():
    assert stem("charges") == stem("charged") == stem("charge") == "charg"
    assert stem("payments") == stem("payment")
    assert stem("disputes") == stem("disputed") == stem("dispute")
    assert stem("minutes") == stem("minute")


def test_stemming_leaves_numbers_and_short_words():
    assert stem("1000") == "1000"
    assert stem("gb") == "gb"
    assert stem("due") == "due"


def test_synonym_expansion():
    assert "additional" in tokenize_query("extra")
    assert "internet" in tokenize_query("mobile data")
    late = tokenize_query("late")
    assert {"due", stem("overdue")} <= set(late)
    assert stem("charge") in tokenize_query("price")
    assert stem("charge") in tokenize_query("cost")


def test_expand_query_keeps_original_terms_first_without_duplicates():
    expanded = expand_query(["extra", "extra", "internet"])
    assert expanded[:2] == ["extra", "internet"]
    assert len(expanded) == len(set(expanded))

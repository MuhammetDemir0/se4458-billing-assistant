import json

from app import indexer
from app.indexer import BillingIndex, build_index, split_sections

EXPECTED_SECTIONS = [
    "1",
    "2.1", "2.2", "2.3",
    "3.1", "3.2", "3.3",
    "4.1", "4.2", "4.3", "4.4",
    "5.1", "5.2", "5.3",
    "6.1", "6.2", "6.3",
    "7.1", "7.2", "7.3",
    "8.1", "8.2", "8.3", "8.4",
    "9",
]  # fmt: skip


def test_section_ids_in_document_order(index_payload):
    assert [c["section"] for c in index_payload["chunks"]] == EXPECTED_SECTIONS


def test_headings_and_parent_headings(index_payload):
    by_id = {c["section"]: c for c in index_payload["chunks"]}
    assert by_id["3.2"]["heading"] == "Additional internet blocks"
    assert by_id["3.2"]["parent_heading"] == "Internet pricing"
    assert by_id["5.1"]["heading"] == "Full and partial payments"
    assert by_id["6.3"]["parent_heading"] == "Late payments and restrictions"
    assert by_id["8.3"]["heading"] == "Can I pay only part of my bill?"
    assert by_id["1"]["heading"] == "Scope and terminology"
    assert by_id["1"]["parent_heading"] is None
    assert by_id["9"]["heading"] == "Guidance for AI/MCP implementation"


def test_chunk_text_is_section_body(index_payload):
    by_id = {c["section"]: c for c in index_payload["chunks"]}
    assert by_id["3.2"]["text"].startswith("Each additional **10 GB block** costs **$10**.")
    assert "35 GB costs $70" in by_id["3.2"]["text"]
    assert "###" not in by_id["3.2"]["text"]
    # The json example in section 9 stays in the returned text.
    assert '"section": "3.2"' in by_id["9"]["text"]


def test_document_metadata(index_payload):
    assert index_payload["document"] == "Mobile_Provider_Billing_Guide.md"
    assert index_payload["document_id"] == "MOB-BILLING-2026-01"
    assert len(index_payload["source_sha256"]) == 64


def test_split_sections_ignores_preamble_empty_parents_and_code_headings():
    md = (
        "# Title\nintro text\n\n"
        "## 1. Alpha\nalpha body\n\n"
        "## 2. Beta\n\n"
        "### 2.1 Gamma\n```\n## 9. Not a heading\n```\ngamma body\n"
    )
    chunks = split_sections(md)
    assert [(c.section, c.heading, c.parent_heading, c.level) for c in chunks] == [
        ("1", "Alpha", None, 2),
        ("2.1", "Gamma", "Beta", 3),
    ]
    assert "## 9. Not a heading" in chunks[1].text


def test_cli_writes_index_and_server_loads_it(tmp_path):
    out = tmp_path / "index.json"
    assert indexer.main(["--output", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["chunks"]) == len(EXPECTED_SECTIONS)
    loaded = BillingIndex.load_or_build(out)
    assert loaded.search("dispute", 1)[0]["section"].startswith(("7", "8.4"))


def test_load_or_build_builds_once_when_index_missing(tmp_path):
    index = BillingIndex.load_or_build(tmp_path / "missing.json")
    assert len(index.chunks) == len(EXPECTED_SECTIONS)


def test_load_or_build_rebuilds_stale_index(tmp_path):
    payload = build_index()
    payload["source_sha256"] = "0" * 64
    payload["chunks"] = payload["chunks"][:1]
    path = tmp_path / "index.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert len(BillingIndex.load_or_build(path).chunks) == len(EXPECTED_SECTIONS)

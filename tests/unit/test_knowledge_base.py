import pytest

from app.services.knowledge.yaml_kb import RedactionError, YamlKnowledgeBase, load_knowledge_base


def test_seed_kb_loads_clean():
    kb = load_knowledge_base("app/knowledge/kb.yaml", strict=True)
    assert kb.size >= 8


def test_new_fact_chunks_present_and_figure_free():
    kb = load_knowledge_base("app/knowledge/kb.yaml", strict=True)
    ids = {c.id for c in kb.retrieve("fmge nmc criteria india vs abroad admission", k=10)}
    assert {"fmge-next", "nmc-criteria", "india-vs-abroad"} & ids


def test_retrieval_ranks_relevant_chunks():
    kb = load_knowledge_base("app/knowledge/kb.yaml", strict=True)
    hits = kb.retrieve("my son wants MBBS in Georgia, is it safe?", k=3)
    assert hits
    ids = [c.id for c in hits]
    assert "country-georgia" in ids
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))


def test_retrieval_empty_for_gibberish():
    kb = load_knowledge_base("app/knowledge/kb.yaml", strict=True)
    assert kb.retrieve("zzzz qqqq", k=3) == []


def test_redaction_lint_rejects_cost_figure(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "chunks:\n"
        "  - id: leak\n"
        "    title: Germany cost\n"
        "    tags: [germany]\n"
        "    text: MBBS in Germany costs about 40 lakh total.\n",
        encoding="utf-8",
    )
    with pytest.raises(RedactionError):
        YamlKnowledgeBase.from_path(bad, strict=True)


def test_redaction_lint_skips_in_non_strict_mode(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "chunks:\n"
        "  - id: leak\n"
        "    title: x\n"
        "    text: costs 40 lakh\n"
        "  - id: good\n"
        "    title: y\n"
        "    text: Georgia has English-medium programmes.\n",
        encoding="utf-8",
    )
    kb = YamlKnowledgeBase.from_path(bad, strict=False)
    assert kb.size == 1

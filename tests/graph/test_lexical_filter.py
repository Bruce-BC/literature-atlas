"""Library-derived lexical filter: no word list, the corpus statistics decide (synthetic text only)."""
from atlas.taxonomy import LEXICAL_MIN_DOCS, concept_quality, lexical_model, lexical_verdict

TOPICS = [("phage", "therapy"), ("optimal", "transport"), ("neural", "network"), ("genome", "assembly")]


def _papers(count=80):
    rows = []
    for index in range(count):
        a, b = TOPICS[index % len(TOPICS)]
        # "but"/"identifying"/"remains" occur once in every abstract and never in a title; topic words repeat and head the title.
        rows.append({"id": f"p{index}", "title": f"{a} {b} study {index + 10}",
                     "abstract": f"we show that {a} {b} matters but identifying it remains hard . the {a} {b} model uses {a} {b} data {index} 000 samples",
                     "conceptIds": [], "keywords": [], "keywordMetadata": []})
    return rows


def test_model_needs_a_minimum_library():
    assert lexical_model(_papers(LEXICAL_MIN_DOCS - 1)) is None
    assert lexical_verdict(None, "but identifying") is None                      # no model -> nothing is judged


def test_duplicate_titles_count_once():
    rows = _papers(60)
    assert lexical_model(rows + [dict(row, id=f"dup-{row['id']}") for row in rows])["docs"] == 60


def test_function_words_and_weak_phrases_are_found_from_statistics():
    model = lexical_model(_papers())
    assert lexical_verdict(model, "but") == "function-word"
    assert lexical_verdict(model, "but identifying") == "function-word"       # a function word at either edge
    assert lexical_verdict(model, "identifying remains") == "function-word"
    assert lexical_verdict(model, "000 samples") == "low-termhood"            # number fragment
    assert lexical_verdict(model, "phage") is None and lexical_verdict(model, "optimal transport") is None
    assert lexical_verdict(model, "three token label") is None                 # only one- and two-token labels are judged


def test_quality_pass_suppresses_text_derived_noise_but_never_explicit_keywords():
    papers = _papers()
    ids = {"but identifying": "concept:s:e:but-identifying", "phage therapy": "concept:s:e:phage-therapy", "but": "concept:s:e:but"}
    concepts = {cid: {"id": cid, "label": {"en": label, "ko": label}, "aliases": [], "source": "extracted"} for label, cid in ids.items()}
    for paper in papers[:6]:
        paper["conceptIds"] = [ids["but identifying"], ids["but"]]
        paper["keywordMetadata"] = [{"label": "but identifying", "origin": "phrase", "evidenceTier": "metadata"}, {"label": "but", "origin": "keyword", "evidenceTier": "metadata"}]
    result = concept_quality(concepts, papers, {}, server="s", epoch="e")
    by = {entry["id"]: entry for entry in result["ledger"]["entries"]}
    assert by[ids["but identifying"]]["status"] == "suppressed" and by[ids["but identifying"]]["reason"] == "function-word"
    assert by[ids["but"]]["status"] == "active"                                # an explicit keyword is the user's choice
    assert all(ids["but identifying"] not in paper["conceptIds"] for paper in papers)

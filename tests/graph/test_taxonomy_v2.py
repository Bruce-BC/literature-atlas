"""Taxonomy v3: independent gold fixture, catalog, classifier and concept quality contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path

import jsonschema
import pytest

from atlas.graph import GraphEngine
from atlas.taxonomy import (build_facet_catalog, check_facet_invariants, classify_papers, concept_quality, field_stats,
                            taxonomy_input_state, validate_facet_catalog)

ROOT = Path(__file__).parents[2]
GOLD_PATH = ROOT / "tests" / "fixtures" / "taxonomy_gold.json"
# Frozen: changing any case (title/label) changes this hash and must be a reviewed decision.
GOLD_SHA256 = "594c41cc723fca541a770d3097f63f0ec2563037faba1a0f9db149c2621f53f0"
S, E = "gold", "epoch-1"


def _catalog(fields=None, taxonomy=None):
    return build_facet_catalog(S, E, fields or {}, taxonomy)


def _keys(row, kind=None):
    return {m["ontologyKey"] for m in row["directMemberships"] if kind in (None, m["kind"])}


def _primary(row, kind="subject"):
    value = row["primaryByFacet"].get(kind)
    return value.rsplit(":", 1)[-1] if value else None


def _paper(title, abstract="", **extra):
    return {"id": "zotero:gold:0:P", "title": title, "abstract": abstract, **extra}


# ---------------------------------------------------------------- gold fixture
GOLD_V2_PATH = ROOT / "tests" / "fixtures" / "taxonomy_gold_v2.json"
GOLD_V2_SHA256 = "28daba23b893d1c351beecdca43db2a3cad5b0a98eab2fb78725c0b79f231f9c"
POLICY_CORRECTED_IDS = {"g039", "g040", "g041", "g070"}          # predeclared by root before the rule change


def _gold(path=None):
    raw = json.loads((path or GOLD_PATH).read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    for case in cases:  # frozen hash split, independent of classifier output
        case["split"] = "heldout" if int(hashlib.sha256(case["id"].encode()).hexdigest()[:8], 16) % 4 == 0 else "calibration"
    return cases


def _evaluate(cases):
    rows, _ = classify_papers([{"id": c["id"], "title": c["title"], "abstract": c["abstract"], "keywords": c["keywords"], "fullText": c.get("fullText", "")} for c in cases], _catalog())
    tp = fp = fn = 0
    primary_pred = primary_ok = resolvable = covered = abstain = abstain_ok = 0
    analysis_fp = 0
    for case, row in zip(cases, rows):
        exp = case["expected"]
        want = {f"subject:{x}" for x in exp["subjects"]} | {f"method:{x}" for x in exp["methods"]} | {f"tool:{x}" for x in exp["tools"]} | {f"application:{x}" for x in exp["applications"]}
        got = _keys(row)
        tp += len(want & got); fp += len(got - want); fn += len(want - got)
        primary = _primary(row)
        if primary is not None:
            primary_pred += 1; primary_ok += primary == exp["primary"]
        if case["kind"] == "resolvable":
            resolvable += 1; covered += primary is not None
        else:
            abstain += 1; abstain_ok += primary is None
        if "subject:analysis" in got and "subject:analysis" not in want:
            analysis_fp += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"primaryPrecision": primary_ok / primary_pred if primary_pred else 0.0,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "coverage": covered / resolvable if resolvable else 0.0,
            "abstention": abstain_ok / abstain if abstain else 1.0,
            "analysisFalsePositives": analysis_fp, "n": len(cases)}


def test_gold_fixture_is_frozen_diverse_and_split():
    raw = GOLD_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == GOLD_SHA256
    cases = _gold()
    assert len(cases) >= 80
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["title"] for c in cases}) >= 80, "gold cases must be diverse, not repeated titles"
    heldout = [c for c in cases if c["split"] == "heldout"]
    assert len(heldout) >= 20 and len(cases) - len(heldout) >= 40
    assert {"resolvable", "abstain"} == {c["kind"] for c in cases}
    # The fixture must contain the plan's counterexamples.
    titles = " ".join(c["title"] for c in cases).casefold()
    for needle in ("qiime 1", "occupational therapy", "kullback", "wasserstein", "disease burden", "plugin"):
        assert needle in titles


def _gates(m, label):
    assert m["primaryPrecision"] >= 0.95, (label, m)
    assert m["f1"] >= 0.90, (label, m)
    assert m["coverage"] >= 0.80, (label, m)
    assert m["abstention"] == 1.0, (label, m)
    assert m["analysisFalsePositives"] == 0, (label, m)


def test_archived_v1_gold_is_unchanged_and_differs_only_by_the_four_predeclared_ontology_corrections():
    """The archived v1 baseline stays byte-identical; the broad-domain ontology change must show up as
    EXACTLY the four predeclared label corrections (not silently rewritten, not reported as unchanged accuracy)."""
    cases = _gold()
    rows, _ = classify_papers([{"id": c["id"], "title": c["title"], "abstract": c["abstract"], "keywords": c["keywords"]} for c in cases], _catalog())
    mismatched = set()
    for case, row in zip(cases, rows):
        exp = case["expected"]
        if _primary(row) != exp["primary"] or not {f"subject:{x}" for x in exp["subjects"]} <= _keys(row, "subject"):
            mismatched.add(case["id"])
    assert mismatched == POLICY_CORRECTED_IDS
    for split in ("heldout", "calibration"):         # every OTHER v1 case keeps meeting the original gates
        _gates(_evaluate([c for c in cases if c["split"] == split and c["id"] not in POLICY_CORRECTED_IDS]), f"v1-{split}-unaffected")


def test_v2_reference_is_versioned_predeclared_and_preserves_original_cases_and_splits():
    assert hashlib.sha256(GOLD_V2_PATH.read_bytes()).hexdigest() == GOLD_V2_SHA256
    doc = json.loads(GOLD_V2_PATH.read_text(encoding="utf-8"))
    assert doc["policyVersion"] == "ontology-label-policy-v2" and doc["basedOn"]["sha256"] == GOLD_SHA256
    v1 = {c["id"]: c for c in json.loads(GOLD_PATH.read_text(encoding="utf-8"))}
    v2 = {c["id"]: c for c in doc["cases"]}
    assert set(v1) <= set(v2) and len(v2) - len(v1) == len(doc["addedCaseIds"]) >= 25
    assert {c["id"] for c in doc["cases"] if "correctedFrom" in c.get("policyV2", {})} == POLICY_CORRECTED_IDS == set(doc["correctedCaseIds"])
    for cid, old in v1.items():
        new = v2[cid]
        assert (new["title"], new["abstract"], new["keywords"]) == (old["title"], old["abstract"], old["keywords"])      # inputs never edited
        if cid not in POLICY_CORRECTED_IDS:
            assert new["expected"] == old["expected"] and new["kind"] == old["kind"]                                      # unaffected labels untouched
        else:
            assert old["expected"]["primary"] is None and new["expected"]["primary"] == "life-science" and new["policyV2"]["reason"]
    # predeclaration receipt exists and records labels were fixed before the rule change
    receipt = json.loads((ROOT / ".omx" / "context" / "claude-v2-ontology-label-policy-v2.json").read_text(encoding="utf-8")) if (ROOT / ".omx" / "context" / "claude-v2-ontology-label-policy-v2.json").exists() else None
    if receipt is not None:
        assert receipt["fixtureSha256"] == GOLD_V2_SHA256 and receipt["preChangeClassifierPrimaryMismatches"]["count"] >= 19


def test_v2_gold_conformance_gates_synthetic_not_blind_heldout_accuracy():
    cases = _gold(GOLD_V2_PATH)
    original_ids = {c["id"] for c in _gold()}
    for split in ("heldout", "calibration"):
        _gates(_evaluate([c for c in cases if c["split"] == split]), f"v2-{split}")
    _gates(_evaluate([c for c in cases if c["split"] == "heldout" and c["id"] in original_ids]), "v2-original-heldout-ids")
    # broad-domain specifics: every biology positive resolves to Life Science, every near-neighbour negative stays out of it
    positives = [c for c in cases if c["expected"]["primary"] == "life-science"]
    negatives = [c for c in cases if c["id"].startswith("b") and c["expected"]["primary"] is None]
    rows, _ = classify_papers([{"id": c["id"], "title": c["title"], "abstract": c["abstract"], "keywords": c["keywords"], "fullText": c.get("fullText", "")} for c in positives + negatives], _catalog())
    by = {r["paperId"]: r for r in rows}
    assert len(positives) >= 19 and all(_primary(by[c["id"]]) == "life-science" for c in positives)
    assert len(negatives) >= 8 and all("subject:life-science" not in _keys(by[c["id"]]) and _primary(by[c["id"]]) is None for c in negatives)
    assert not any("subject:analysis" in _keys(by[c["id"]]) for c in positives + negatives)             # never Mathematics Analysis


# ------------------------------------------------------------------ classifier
def test_qiime2_amplicon_primary_per_facet_and_no_metagenomics():
    assert validate_facet_catalog(_catalog()) == []
    rows, _ = classify_papers([_paper("QIIME 2 microbiome amplicon analysis", "16S marker gene")], _catalog())
    row = rows[0]
    assert (_primary(row), _primary(row, "tool"), _primary(row, "method")) == ("bioinformatics", "qiime2", "amplicon")
    assert {"tool:qiime2", "method:amplicon", "application:microbiome"} <= _keys(row)
    assert "method:metagenomics" not in _keys(row)
    assert check_facet_invariants(rows, _catalog()) == []


def test_qiime1_or_unversioned_never_becomes_qiime2_tool():
    for title in ("QIIME 1 pipeline for amplicon data", "QIIME: a pipeline for marker gene analysis"):
        rows, _ = classify_papers([_paper(title, "We present an open-source pipeline for amplicon sequencing.")], _catalog())
        assert "tool:qiime2" not in _keys(rows[0]) and _primary(rows[0], "tool") is None
        assert _primary(rows[0]) == "bioinformatics"


def test_qiime2_phage_use_stays_phage_and_development_is_bioinformatics():
    use, _ = classify_papers([_paper("Phage therapy uses QIIME2", "bacteriophage treatment")], _catalog())
    assert _primary(use[0]) == "phage-biology" and "subject:bioinformatics" not in _keys(use[0])
    assert _primary(use[0], "tool") == "qiime2"
    dev, _ = classify_papers([_paper("A QIIME 2 plugin", "We present an extensible plugin for sequence data.")], _catalog())
    assert _primary(dev[0]) == "bioinformatics" and any(m["evidenceRef"]["ruleId"] == "qiime2-development" for m in dev[0]["directMemberships"] if m["ontologyKey"] == "tool:qiime2")


def test_metagenomics_requires_explicit_evidence_and_ancestor_never_primary():
    no, _ = classify_papers([_paper("QIIME2 workflow notes")], _catalog())
    microbiome, _ = classify_papers([_paper("QIIME2 microbiome workflow")], _catalog())
    yes, _ = classify_papers([_paper("Shotgun metagenomics with QIIME2")], _catalog())
    assert "method:metagenomics" not in _keys(no[0]) and _primary(no[0]) is None  # QIIME2 alone: tool facet only, no subject
    assert _primary(no[0], "tool") == "qiime2"
    # policy v2: an explicit microbiome topic is broad-domain Life Science, never an invented Bioinformatics or metagenomics
    assert _primary(microbiome[0]) == "life-science" and "subject:bioinformatics" not in _keys(microbiome[0]) and "method:metagenomics" not in _keys(microbiome[0])
    assert microbiome[0]["directMemberships"][0]["facetId"] and any(m["evidenceRef"]["ruleId"].startswith("broad-domain") for m in microbiome[0]["directMemberships"] if m["kind"] == "subject")
    assert "method:metagenomics" in _keys(yes[0]) and _primary(yes[0], "method") == "metagenomics"
    for row in yes + no:
        assert _primary(row) != "life-science"           # a narrower subject or no subject: the broad root never absorbs these


def test_evidence_refs_have_tier_offsets_and_never_raw_text():
    rows, _ = classify_papers([_paper("QIIME2 amplicon", "marker gene")], _catalog())
    for m in rows[0]["directMemberships"]:
        ref = m["evidenceRef"]
        assert ref["sourceId"] == S and ref["sourceEpoch"] == E and ref["evidenceTier"] in {"metadata", "indexed", "fulltext", "user"}
        assert ref.get("evidenceSpan") is None
        assert "marker gene" not in json.dumps(ref).casefold() and "QIIME2 amplicon" not in json.dumps(ref)
        assert all(isinstance(v, (str, int, float, list, type(None))) for v in ref.values()) and ref.get("spanRange", [0, 0])[0] <= ref.get("spanRange", [0, 0])[1]


def test_title_outranks_abstract_and_fulltext_evidence_tier():
    rows, _ = classify_papers([{"id": "a", "title": "Phage biology", "fullText": "wasserstein distance"}, {"id": "b", "title": "x", "abstract": "Optimal transport maps", "fullText": ""}], _catalog())
    by = {r["paperId"]: {m["ontologyKey"]: m for m in r["directMemberships"]} for r in rows}
    assert by["a"]["subject:phage-biology"]["score"] > by["a"].get("subject:optimal-transport", {"score": 0})["score"]
    assert by["b"]["subject:optimal-transport"]["evidenceRef"]["field"] == "abstract"


def test_generic_biomedical_analysis_abstains_and_primary_is_none():
    rows, _ = classify_papers([_paper("Clinical analysis of disease burden", "patient outcomes")], _catalog())
    assert "subject:analysis" not in _keys(rows[0]) and _primary(rows[0]) is None and rows[0]["status"] == "abstain"


def test_user_confirmed_math_biology_membership_survives_and_wins():
    cat = _catalog({"f-analysis": {"id": "f-analysis", "label": {"en": "Analysis"}, "parentIds": [], "source": "user"}})
    paper = _paper("Mathematical model for microbiome communities and patients")
    rows, _ = classify_papers([paper], cat, user_subjects={paper["id"]: ["f-analysis"]})
    user = [m for m in rows[0]["directMemberships"] if m["origin"] == "user"]
    assert user and rows[0]["status"] == "user"
    assert rows[0]["primaryByFacet"]["subject"] == user[0]["facetId"]


def test_user_primary_override_is_accepted_even_when_classifier_abstains():
    cat = _catalog()
    paper = _paper("An unrelated note")
    target = next(e for e in cat["entities"] if e["ontologyKey"] == "subject:topology")["id"]
    rows, _ = classify_papers([paper], cat, overlay_entities={paper["id"]: {"primaryFacetOverrides": {"subject": target}}})
    assert rows[0]["primaryByFacet"]["subject"] == target
    assert target in {m["facetId"] for m in rows[0]["directMemberships"]}
    assert check_facet_invariants(rows, cat) == []
    bad_kind = next(e for e in cat["entities"] if e["kind"] == "tool")["id"]
    rows, _ = classify_papers([paper], cat, overlay_entities={paper["id"]: {"primaryFacetOverrides": {"subject": bad_kind}}})
    assert rows[0]["primaryByFacet"]["subject"] is None


def test_ambiguous_unrelated_subjects_abstain_primary_but_keep_directs():
    rows, _ = classify_papers([_paper("Optimal transport and KL divergence for variational inference")], _catalog())
    assert {"subject:optimal-transport", "subject:kl-divergence"} <= _keys(rows[0])
    assert _primary(rows[0]) is None and rows[0]["status"] == "ambiguous"


def test_ot_and_kl_acronyms_need_matching_context():
    occupational, _ = classify_papers([_paper("OT services and occupational therapy in hospitals")], _catalog())
    assert "subject:optimal-transport" not in _keys(occupational[0])
    transport, _ = classify_papers([_paper("OT plans with a Wasserstein cost")], _catalog())
    assert "subject:optimal-transport" in _keys(transport[0])
    unrelated_kl, _ = classify_papers([_paper("KL city transport planning")], _catalog())
    assert "subject:kl-divergence" not in _keys(unrelated_kl[0])


# --------------------------------------------------------------------- catalog
def test_catalog_references_use_canonical_entity_ids_with_semantic_ontology_keys():
    cat = _catalog()
    ids = {e["id"] for e in cat["entities"]}
    assert all(e["id"].startswith(f"facet:{S}:{E}:") and e["ontologyKey"] == f"{e['kind']}:{e['key']}" for e in cat["entities"])
    assert all(parent in ids for e in cat["entities"] for parent in e["broaderIds"])
    rows, _ = classify_papers([_paper("QIIME2 amplicon")], cat)
    assert all(m["facetId"] in ids for m in rows[0]["directMemberships"])
    assert all(v is None or v in ids for v in rows[0]["primaryByFacet"].values())


def test_custom_facets_are_source_fenced_normalise_parent_aliases_and_drive_classifier():
    prefix = f"facet:{S}:{E}:"
    config = {"entities": [
        {"id": prefix + "tool:nextflow", "kind": "tool", "label": "Nextflow", "aliases": ["nf-core"], "broaderIds": []},
        {"id": prefix + "subject:systems", "kind": "subject", "label": {"en": "Systems Biology"}, "broaderIds": ["subject:life-science"], "keywords": ["flux balance"]},
        {"id": "facet:other:epoch:tool:stale", "kind": "tool", "label": "Stale", "broaderIds": []},
    ]}
    cat = build_facet_catalog(S, E, {}, {"facetCatalogConfig": config, "inputState": {"status": "valid-custom"}})
    by_id = {e["id"]: e for e in cat["entities"]}
    assert "facet:other:epoch:tool:stale" not in by_id            # foreign source fence ignored
    life = next(e["id"] for e in cat["entities"] if e["ontologyKey"] == "subject:life-science")
    assert by_id[prefix + "subject:systems"]["broaderIds"] == [life]  # semantic alias normalised
    assert validate_facet_catalog(cat) == []
    rows, _ = classify_papers([_paper("Flux balance models orchestrated with nf-core")], cat)
    assert prefix + "tool:nextflow" in {m["facetId"] for m in rows[0]["directMemberships"]}
    assert _primary(rows[0], "subject") == "systems"


def test_cyclic_custom_parents_are_broken_not_published():
    prefix = f"facet:{S}:{E}:"
    config = {"entities": [{"id": prefix + "subject:a", "kind": "subject", "label": "A", "broaderIds": ["subject:b"]},
                           {"id": prefix + "subject:b", "kind": "subject", "label": "B", "broaderIds": ["subject:a"]}]}
    cat = build_facet_catalog(S, E, {}, {"facetCatalogConfig": config, "inputState": {"status": "valid-custom"}})
    assert validate_facet_catalog(cat) == []
    a = next(e for e in cat["entities"] if e["id"].endswith("subject:a")); b = next(e for e in cat["entities"] if e["id"].endswith("subject:b"))
    assert not (b["id"] in a["broaderIds"] and a["id"] in b["broaderIds"])


def test_same_label_different_context_legacy_fields_stay_distinct_and_merge_only_in_same_context():
    fields = {
        f"field:{S}:{E}:mathematics": {"id": f"field:{S}:{E}:mathematics", "label": {"en": "Mathematics"}, "parentIds": [], "aliases": [], "source": "user"},
        f"field:{S}:{E}:analysis": {"id": f"field:{S}:{E}:analysis", "label": {"en": "Analysis"}, "parentIds": [f"field:{S}:{E}:mathematics"], "aliases": [], "source": "user"},
        f"field:{S}:{E}:clinical-analysis": {"id": f"field:{S}:{E}:clinical-analysis", "label": {"en": "Analysis"}, "parentIds": [], "aliases": [], "source": "user"},
    }
    cat = build_facet_catalog(S, E, fields, {"fields": list(fields.values())})
    analyses = [e for e in cat["entities"] if e["kind"] == "subject" and e["label"]["en"] == "Analysis"]
    assert len(analyses) == 2 and len({e["id"] for e in analyses}) == 2
    assert sum(e["origin"] == "builtin" for e in analyses) == 1
    assert {e["legacyFieldId"] for e in analyses} == {f"field:{S}:{E}:analysis", f"field:{S}:{E}:clinical-analysis"}
    assert validate_facet_catalog(cat) == []


def test_taxonomy_input_states_are_distinct_and_never_reenable_builtin():
    assert taxonomy_input_state(None)[0] == "absent"
    assert taxonomy_input_state({})[0] == "absent"
    assert taxonomy_input_state({"fields": [], "inputState": {"status": "intentional-empty-valid"}})[0] == "intentional-empty-valid"
    assert taxonomy_input_state({"fields": [{"id": "x"}]})[0] == "valid-custom"
    assert taxonomy_input_state("garbage")[0] == "invalid"
    assert taxonomy_input_state({"inputState": {"status": "disabled", "reason": "off"}}) == ("disabled", "off")
    for state in ("invalid", "disabled"):
        cat = build_facet_catalog(S, E, {}, {"inputState": {"status": state}})
        assert cat["entities"] == [] and cat["inputState"] == state
    assert build_facet_catalog(S, E, {}, {"inputState": {"status": "intentional-empty-valid"}})["entities"]  # classifier catalog remains


# --------------------------------------------------------------- concept quality
def _concepts(*labels, source="extracted"):
    return {f"concept:s:e:{l.replace(' ', '-')}": {"id": f"concept:s:e:{l.replace(' ', '-')}", "label": {"en": l}, "aliases": [], "source": source} for l in labels}


def _papers(concepts, n=1, text="", origin="title"):
    ids = list(concepts)
    return [{"id": f"p{i}", "title": text, "abstract": "", "conceptIds": list(ids),
             "keywordMetadata": [{"label": c["label"]["en"], "origin": origin, "evidenceTier": "metadata"} for c in concepts.values()]} for i in range(n)]


def test_phrase_protection_and_boilerplate_suppression_with_ledger_resolution():
    concepts = _concepts("been", "burden", "bundles", "disease burden", "fiber bundles", "vector bundles", "auto-classifier:math", "auto-primary:bio", "11M20", "14M25", "52B20", "qiime2")
    result = concept_quality(concepts, _papers(concepts), {})
    ledger = result["ledger"]; status = {e["id"]: e["status"] for e in ledger["entries"]}
    for label in ("been", "burden", "bundles", "auto-classifier:math", "auto-primary:bio"):
        assert status[f"concept:s:e:{label}"] == "suppressed"
    for label in ("disease-burden", "fiber-bundles", "vector-bundles", "11M20", "14M25", "52B20", "qiime2"):
        assert status[f"concept:s:e:{label}"] in {"protected", "active"}, label
    kept = set(result["concepts"]) and {c for p in _papers(concepts) for c in p["conceptIds"]}
    assert ledger["counts"]["raw"] == len(concepts) and set(ledger["raw"]) == set(concepts)
    assert set(ledger["active"]).isdisjoint(ledger["suppressed"])
    assert set(ledger["protected"]) <= set(ledger["active"])          # ownership buckets overlap
    assert kept


def test_suppressed_ids_drop_from_papers_but_stay_in_ledger():
    concepts = _concepts("burden", "disease burden")
    papers = _papers(concepts)
    result = concept_quality(concepts, papers, {})
    assert papers[0]["conceptIds"] == ["concept:s:e:disease-burden"]
    assert "concept:s:e:burden" in result["ledger"]["suppressed"]


def test_df_termhood_filters_text_candidates_in_large_libraries_with_single_doc_exceptions():
    concepts = _concepts("microbiome", "rarity", "11M20")
    papers = _papers(concepts, n=30, origin="abstract")
    for p in papers[1:]:
        p["conceptIds"] = [c for c in p["conceptIds"] if c.endswith("microbiome")]
    result = concept_quality(concepts, papers, {})
    status = {e["id"]: e for e in result["ledger"]["entries"]}
    assert status["concept:s:e:microbiome"]["status"] == "active"
    assert status["concept:s:e:rarity"]["status"] == "suppressed" and status["concept:s:e:rarity"]["reason"] == "low-termhood"
    assert status["concept:s:e:11M20"]["status"] == "protected"       # technical code exception
    keyword_papers = _papers(_concepts("rarity"), n=30, origin="keyword")
    keyword_result = concept_quality(_concepts("rarity"), [dict(p, conceptIds=p["conceptIds"][:1] if i == 0 else []) for i, p in enumerate(keyword_papers)], {})
    assert keyword_result["ledger"]["entries"][0]["status"] == "active"  # explicit keyword exception


def test_user_pins_aliases_exclusions_and_owned_concepts_are_protected_over_deny_rules():
    concepts = _concepts("burden", "been", "bundles")
    concepts["concept:s:e:mine"] = {"id": "concept:s:e:mine", "label": {"en": "burden"}, "aliases": [], "source": "user"}
    overlays = {"pins": {"concept:s:e:burden": True}, "conceptAliases": {"concept:s:e:been": ["were"]},
                "entities": {"concept:s:e:bundles": {"entityType": "concept", "excluded": True}}}
    result = concept_quality(concepts, _papers(concepts), overlays)
    by = {e["id"]: e for e in result["ledger"]["entries"]}
    assert by["concept:s:e:burden"]["status"] == "protected" and by["concept:s:e:been"]["status"] == "protected"
    assert by["concept:s:e:mine"]["status"] == "protected"
    assert by["concept:s:e:bundles"]["status"] == "suppressed" and by["concept:s:e:bundles"]["reason"] == "user-excluded"
    assert "concept:s:e:bundles" in result["ledger"]["user"]


def test_kl_and_ot_canonicalize_only_in_matching_context():
    concepts = _concepts("kl", "ot", "kl divergence", "optimal transport")
    papers = [
        {"id": "m", "title": "Kullback-Leibler divergence with OT plans", "abstract": "wasserstein", "conceptIds": ["concept:s:e:kl", "concept:s:e:ot"], "keywordMetadata": []},
        {"id": "o", "title": "Occupational therapy OT services", "abstract": "", "conceptIds": ["concept:s:e:ot"], "keywordMetadata": []},
        {"id": "x", "title": "KL city planning", "abstract": "", "conceptIds": ["concept:s:e:kl"], "keywordMetadata": []},
    ]
    concept_quality(concepts, papers, {}, server="s", epoch="e")
    by = {p["id"]: p["conceptIds"] for p in papers}
    assert by["m"] == ["concept:s:e:kl-divergence", "concept:s:e:optimal-transport"]
    assert by["o"] == ["concept:s:e:ot"] and by["x"] == ["concept:s:e:kl"]


# ---------------------------------------------------------------------- stats
def test_field_stats_unique_sets_under_multi_membership_and_multi_parent():
    fields = {k: {"id": k, "label": {"en": k}, "parentIds": v} for k, v in {"f:a": [], "f:b": [], "f:c": ["f:a", "f:b"], "f:unknown": []}.items()}
    fields["f:unknown"]["label"] = {"en": "Unclassified"}
    papers = [{"id": "p1", "displayFieldId": "f:c"}, {"id": "p2", "displayFieldId": "f:unknown"}, {"id": "p3", "displayFieldId": "f:a"}, {"id": "p4", "displayFieldId": None}]
    memberships = [{"paperId": "p1", "fieldId": "f:c"}, {"paperId": "p1", "fieldId": "f:a"}, {"paperId": "p3", "fieldId": "f:a"}, {"paperId": "p3", "fieldId": "f:a"}]
    stats = field_stats(papers, fields, memberships)
    rows = {r["fieldId"]: r for r in stats["rows"]}
    assert (rows["f:a"]["direct"], rows["f:a"]["inclusive"], rows["f:a"]["primary"]) == (2, 2, 1)   # p1 counted once
    assert (rows["f:b"]["direct"], rows["f:b"]["inclusive"]) == (0, 1)
    assert rows["f:c"]["primary"] == 1
    assert stats["knownPrimary"] + stats["unknown"] == stats["paperCount"] == 4 and stats["unknownPaperIds"] == ["p2", "p4"]
    assert all(r["primary"] <= r["direct"] <= r["inclusive"] for r in stats["rows"])


# ------------------------------------------------------------------ graph engine
def _build(papers, taxonomy=None, overlays=None, previous=None):
    with tempfile.TemporaryDirectory() as directory:
        return GraphEngine(Path(directory), provider="fake", allow_download=False).build(
            papers, source={"id": S, "serverId": S, "epoch": E, "alias": "fixture", "zoteroApi": "3", "schema": 44},
            taxonomy=taxonomy, overlays=overlays, previous=previous, settings={"fakeVectors": {}})


def test_graph_snapshot_validates_schema_and_projects_facets_into_legacy_fields():
    snapshot = _build([{"key": "A", "title": "QIIME 2 microbiome amplicon study", "abstract": "16S"},
                       {"key": "B", "title": "Bacteriophage therapy and QIIME2", "abstract": ""},
                       {"key": "C", "title": "An unrelated note"}])
    jsonschema.validate(snapshot, json.loads((ROOT / "docs" / "snapshot.schema.json").read_text()))
    by_key = {p["key"]: p for p in snapshot["papers"]}
    labels = {f["id"]: f["label"]["en"] for f in snapshot["fields"]}
    assert labels[by_key["A"]["displayFieldId"]] == "Bioinformatics"       # not Life Science
    assert labels[by_key["B"]["displayFieldId"]] == "Phage Biology"
    assert labels[by_key["C"]["displayFieldId"]] == "Unclassified"
    stats = snapshot["fieldStats"]
    assert stats["knownPrimary"] + stats["unknown"] == stats["paperCount"] == 3 and stats["unknown"] == 1
    assert check_facet_invariants(snapshot["paperFacets"], snapshot["facetCatalog"]) == []
    ledger = snapshot["conceptQualityLedger"]
    assert set(ledger["active"]).isdisjoint(ledger["suppressed"])


def test_stale_generated_concepts_are_resolvable_in_ledger_but_not_graph_nodes():
    previous = {"concepts": [{"id": f"concept:{S}:{E}:been", "label": {"en": "been"}, "aliases": [], "source": "extracted"}]}
    snapshot = _build([{"key": "A", "title": "QIIME 2 microbiome amplicon study", "abstract": "16S"}], taxonomy={}, previous=previous)
    assert f"concept:{S}:{E}:been" not in {c["id"] for c in snapshot["concepts"]}
    entry = next(e for e in snapshot["conceptQualityLedger"]["entries"] if e["id"] == f"concept:{S}:{E}:been")
    assert entry["status"] == "suppressed"


def test_intentional_empty_taxonomy_hides_fields_but_keeps_internal_facets_dangling_free():
    snapshot = _build([{"key": "A", "title": "QIIME 2 microbiome amplicon study"}], taxonomy={"fields": [], "concepts": [], "inputState": {"status": "intentional-empty-valid"}})
    assert snapshot["fields"] == [] and snapshot["memberships"] == []
    paper = snapshot["papers"][0]
    assert paper["fieldIds"] == [] and paper["displayFieldId"] is None
    assert snapshot["taxonomyState"]["status"] == "intentional-empty-valid"
    ids = {e["id"] for e in snapshot["facetCatalog"]["entities"]}
    assert snapshot["paperFacets"][0]["primaryByFacet"]["tool"] in ids
    jsonschema.validate(snapshot, json.loads((ROOT / "docs" / "snapshot.schema.json").read_text()))


def test_invalid_or_disabled_taxonomy_does_not_resurrect_builtin_fields():
    for state in ("invalid", "disabled"):
        snapshot = _build([{"key": "A", "title": "QIIME 2 microbiome amplicon study"}], taxonomy={"fields": [], "inputState": {"status": state, "reason": "operator"}})
        assert snapshot["fields"] == []
        assert snapshot["facetCatalog"]["entities"] == [] and snapshot["taxonomyState"]["status"] == state


def test_user_overlay_facet_assignment_is_accepted_membership_when_classifier_abstains():
    snapshot = _build([{"key": "A", "title": "An unrelated note"}])
    pid = snapshot["papers"][0]["id"]
    topology = next(e["id"] for e in snapshot["facetCatalog"]["entities"] if e["ontologyKey"] == "subject:topology")
    again = _build([{"key": "A", "title": "An unrelated note"}], overlays={"entities": {pid: {"entityType": "paper", "facetAssignments": [topology], "primaryFacetOverrides": {"subject": topology}}}})
    row = again["paperFacets"][0]
    assert row["primaryByFacet"]["subject"] == topology and topology in {m["facetId"] for m in row["directMemberships"]}


def test_msc_codes_survive_from_structured_extra_and_subject_codes_without_leaking_extra():
    from atlas.taxonomy import msc_codes_from_extra
    extra = "MSC2020: 11M20, 14M25; 52B20\nnote: this free text 99Z99 must not be mined\nPMID: 123"   # synthetic test input
    assert msc_codes_from_extra(extra) == ["11M20", "14M25", "52B20"]
    papers = [{"key": "A", "title": "Zeta functions", "extra": extra}, {"key": "B", "title": "Polytopes", "subjectCodes": ["52b20"]}]
    snapshot = _build(papers)
    labels = {c["label"]["en"]: c for c in snapshot["concepts"]}
    assert {"11M20", "14M25", "52B20"} <= set(labels) and "99Z99" not in labels
    ledger = {e["id"]: e for e in snapshot["conceptQualityLedger"]["entries"]}
    assert all(ledger[labels[c]["id"]]["status"] == "protected" for c in ("11M20", "14M25", "52B20"))
    assert all("extra" not in p for p in snapshot["papers"]) and "free text" not in json.dumps(snapshot)


def test_full_pipeline_ot_and_kl_context_per_paper_without_false_shared_merge():
    papers = [
        {"key": "OT1", "title": "Occupational therapy outcomes after stroke", "abstract": "Patients received OT services.", "keywords": ["OT", "stroke"]},
        {"key": "OT2", "title": "Optimal transport with Wasserstein costs", "abstract": "Sinkhorn solvers for OT plans.", "keywords": ["OT", "Optimal Transport"]},
        {"key": "KL1", "title": "Variational inference with Kullback-Leibler divergence", "abstract": "Relative entropy bounds.", "keywords": ["KL", "KL divergence"]},
        {"key": "KL2", "title": "KL city transport planning", "abstract": "Bus networks in a city.", "keywords": ["KL", "transit"]},
    ]
    snapshot = _build(papers)
    by_key = {p["key"]: p for p in snapshot["papers"]}
    label = {c["id"]: c["label"]["en"].casefold() for c in snapshot["concepts"]}
    names = lambda key: {label[c] for c in by_key[key]["conceptIds"]}
    assert "ot" in names("OT1") and "optimal transport" not in names("OT1")          # occupational therapy stays separate
    assert "ot" not in names("OT2") and "optimal transport" in names("OT2")          # mathematical OT canonicalised per paper
    assert "kl divergence" in names("KL1") and "kl" not in names("KL1")
    assert "kl" in names("KL2") and "kl divergence" not in names("KL2")              # unrelated KL abbreviation not merged
    ot_id = next(c for c, l in label.items() if l == "ot")
    optimal_id = next(c for c, l in label.items() if l == "optimal transport")
    assert ot_id != optimal_id and snapshot["taxonomyRedirects"].get(ot_id) != optimal_id   # no global redirect
    # concept membership / keyword aggregate never put OT1 and OT2 on the same concept
    assert not set(by_key["OT1"]["conceptIds"]) & {optimal_id}
    # intentional user alias still merges globally and is protected.
    taxonomy = {"concepts": [{"id": f"concept:{S}:{E}:optimal-transport", "label": {"en": "Optimal Transport"}, "aliases": ["OT"], "source": "user"}], "fields": []}
    aliased = _build(papers, taxonomy=taxonomy)
    labels = {c["id"]: c["label"]["en"].casefold() for c in aliased["concepts"]}
    a_names = {labels[c] for p in aliased["papers"] if p["key"] == "OT1" for c in p["conceptIds"]}
    assert "optimal transport" in a_names and "ot" not in a_names                     # user alias is authoritative, not context-gated


# ----------------------------------------------------------------- evidence performance (semantic equivalence)
import random as _random
import re as _re

import atlas.taxonomy as _taxonomy


def _signature(evidence):
    return {pid: {k: (c.score, c.field, c.span, c.rule, c.hits, c.origin) for k, c in found.items()} for pid, found in evidence.items()}


def _corpus():
    rng = _random.Random(20261118)
    vocab = ["QIIME 2", "q2-diversity plugin", "Q2-2023", "amplicon", "16S rRNA", "marker-gene", "metagenomics", "microbiome", "microbial  community", "phage therapy",
             "Bacteriophage", "Wasserstein", "optimal\ntransport", "OT", "occupational therapist", "Kullback-Leibler", "KL divergence", "relative entropy", "topological space",
             "Markov chain", "stochastic optimization", "gradient descent", "Hilbert space", "real analysis", "mutual information", "entropy", "clinical", "patients", "cohort",
             "nf-core", "Systems  Biology", "flux balance", "gene expression", "genetic algorithm", "cell phone", "protein folding", "yeast", "mitochondria", "cellular automata", "neural network", "bacteria", "ecology", "genome assembly", "the", "of", "and", "analysis", "results", "data", "sequence analysis", "its region", "ITS2 amplicon"]
    papers = []
    for i in range(60):
        make = lambda n: " ".join(rng.choice(vocab) for _ in range(n))
        papers.append({"id": f"c{i}", "title": make(rng.randint(2, 8)), "abstract": make(rng.randint(0, 40)), "keywords": [rng.choice(vocab) for _ in range(rng.randint(0, 3))],
                       "indexedText": make(rng.randint(0, 80)), "fullText": make(rng.randint(0, 80))})
    papers += [{"id": f"g{i}", "title": c["title"], "abstract": c["abstract"], "keywords": c["keywords"]} for i, c in enumerate(_gold())]
    return papers


def _custom_catalog():
    prefix = f"facet:{S}:{E}:"
    return build_facet_catalog(S, E, {}, {"facetCatalogConfig": {"entities": [
        {"id": prefix + "tool:nextflow", "kind": "tool", "label": "Nextflow", "aliases": ["nf-core"]},
        {"id": prefix + "subject:systems", "kind": "subject", "label": "Systems Biology", "keywords": ["flux balance"]}]}, "inputState": {"status": "valid-custom"}})


def test_literal_prefilter_and_part_cache_change_no_evidence_result(monkeypatch):
    papers = _corpus()
    for catalog in (_catalog(), _custom_catalog()):
        monkeypatch.setattr(_taxonomy, "_PREFILTER", False)
        reference = _signature(_taxonomy.collect_evidence(papers, catalog))
        monkeypatch.setattr(_taxonomy, "_PREFILTER", True)
        assert _signature(_taxonomy.collect_evidence(papers, catalog)) == reference
        cache = {}
        assert _signature(_taxonomy.collect_evidence(papers, catalog, cache)) == reference
        assert _signature(_taxonomy.collect_evidence(papers, catalog, cache)) == reference      # second pass served from the cache
        assert any(reference[pid] for pid in reference)                                         # corpus actually exercises the rules
    # derived needles are necessary conditions of their patterns
    for _, _, _, rx, needle in _taxonomy._RULES:
        if needle:
            assert rx.pattern.startswith("\\b") and _re.match(r"^[a-z0-9\-]+$", needle)


class _CountingPattern:
    def __init__(self, rx, log):
        self.rx, self.log, self.pattern = rx, log, rx.pattern
    def search(self, text):
        self.log.append(len(text)); return self.rx.search(text)


def _count_regex_work(monkeypatch):
    log = []
    monkeypatch.setattr(_taxonomy, "_RULES", [(k, r, st, _CountingPattern(rx, log), n) for k, r, st, rx, n in _taxonomy._RULES])
    return log


def test_second_pass_over_unchanged_large_text_does_no_regex_scan_of_that_text(monkeypatch):
    log = _count_regex_work(monkeypatch)
    rng = _random.Random(3)
    big = lambda: " ".join(rng.choice(["patient", "microbiome", "amplicon", "transport", "wasserstein", "analysis", "phage", "data"]) for _ in range(5000))
    papers = [{"id": f"p{i}", "title": "QIIME2 amplicon", "keywords": ["a"], "indexedText": big(), "fullText": big()} for i in range(5)]
    cache = {}
    _taxonomy.collect_evidence(papers, _catalog(), cache)
    first_long = sum(1 for n in log if n > 10000); assert first_long > 0
    log.clear()
    # same text, changed keywords (what happens between the graph's first and final pass): long parts are NOT rescanned
    for paper in papers:
        paper["keywords"] = ["something else"]
    _taxonomy.collect_evidence(papers, _catalog(), cache)
    assert sum(1 for n in log if n > 10000) == 0
    assert sum(len(v[1]) for v in cache.values()) >= first_long


def test_graph_build_scans_large_text_once_not_twice(monkeypatch):
    log = _count_regex_work(monkeypatch)
    rng = _random.Random(5)
    big = lambda: " ".join(rng.choice(["patient", "microbiome", "amplicon", "transport", "analysis", "phage", "data", "sequence"]) for _ in range(4000))
    papers = [{"key": f"K{i}", "title": "QIIME 2 microbiome amplicon study", "abstract": "16S marker gene", "keywords": ["burden", "x"], "indexedText": big()} for i in range(4)]
    _build(papers)
    in_build = sum(1 for n in log if n > 10000)
    log.clear()
    catalog = _catalog()
    direct = [{"id": f"d{i}", "title": p["title"], "abstract": p["abstract"], "indexedText": p["indexedText"]} for i, p in enumerate(papers)]
    _taxonomy.collect_evidence(direct, catalog)                       # one uncached pass = the minimum possible scans
    single_pass = sum(1 for n in log if n > 10000)
    assert single_pass > 0 and in_build == single_pass                # the build (two evidence passes) scans each large part exactly once


def test_regression_gold_h1_ot_therapist_and_q2_generic_code_are_frozen_and_pass():
    path = ROOT / "tests" / "fixtures" / "taxonomy_gold_regression.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "0addf73b3a54e563f00a37880450e2ec29db96b2a8daf8193778dd77ab377916"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len(cases) >= 10 and all(c["id"].startswith("r") for c in cases)       # calibration-only; never part of the frozen held-out file
    heldout_ids = {c["id"] for c in _gold() if c["split"] == "heldout"}
    assert not heldout_ids & {c["id"] for c in cases}
    m = _evaluate(cases)
    assert m["abstention"] == 1.0 and m["primaryPrecision"] == 1.0 and m["f1"] == 1.0 and m["coverage"] == 1.0, m


def test_ot_acronym_needs_strong_math_context_and_occupational_morphology_blocks_it():
    cat = _catalog()
    for title in ("Occupational therapist-led OT sessions with optimal dosing", "OT in an ergotherapy clinic with optimal staffing", "Optimal OT scheduling for therapists"):
        rows, _ = classify_papers([_paper(title, "patients in a clinical cohort")], cat)
        assert "subject:optimal-transport" not in _keys(rows[0]) and _primary(rows[0]) is None, title
    genuine, _ = classify_papers([_paper("OT couplings and Sinkhorn iterations on patient cohorts")], cat)
    assert _primary(genuine[0]) == "optimal-transport"
    user, _ = classify_papers([_paper("Occupational therapist-led OT sessions")], cat,
                              overlay_entities={"zotero:gold:0:P": {"primaryFacetOverrides": {"subject": next(e["id"] for e in cat["entities"] if e["ontologyKey"] == "subject:optimal-transport")}}})
    assert _primary(user[0]) == "optimal-transport" and user[0]["status"] == "user"        # explicit manual override still wins


def test_generic_q2_code_is_not_qiime2_unless_plugin_context():
    cat = _catalog()
    assert "tool:qiime2" not in _keys(classify_papers([_paper("Q2-2023 revenue forecasting")], cat)[0][0])
    assert "tool:qiime2" not in _keys(classify_papers([_paper("Q2-style survey of genomics labs")], cat)[0][0])
    assert "tool:qiime2" in _keys(classify_papers([_paper("The q2-feature-table summary")], cat)[0][0])
    assert "tool:qiime2" in _keys(classify_papers([_paper("A q2-novelty plugin", "We present an extensible plugin.")], cat)[0][0])


def test_full_pipeline_occupational_therapist_ot_paper_is_not_optimal_transport():
    papers = [{"key": "T", "title": "Occupational therapist-led OT sessions with optimal dosing", "abstract": "Patients in a clinical cohort.", "keywords": ["OT"]},
              {"key": "M", "title": "OT plans with Wasserstein costs", "abstract": "Sinkhorn solvers.", "keywords": ["OT"]}]
    snapshot = _build(papers)
    by_key = {p["key"]: p for p in snapshot["papers"]}
    facets = {r["paperId"]: r for r in snapshot["paperFacets"]}
    labels = {f["id"]: f["label"]["en"] for f in snapshot["fields"]}
    assert labels[by_key["T"]["displayFieldId"]] == "Unclassified" and labels[by_key["M"]["displayFieldId"]] == "Optimal Transport"
    assert facets[by_key["T"]["id"]]["primaryByFacet"]["subject"] is None and facets[by_key["M"]["id"]]["primaryByFacet"]["subject"].endswith("subject:optimal-transport")


def test_ledger_merged_into_is_global_only_for_unambiguous_merges_and_context_merges_are_marked_per_paper():
    concepts = _concepts("ot", "kl", "optimal transport", "kl divergence")
    papers = [
        {"id": "m", "title": "OT plans with Wasserstein costs", "abstract": "", "conceptIds": ["concept:s:e:ot"], "keywordMetadata": []},
        {"id": "o", "title": "Occupational therapist-led OT sessions", "abstract": "", "conceptIds": ["concept:s:e:ot"], "keywordMetadata": []},
        {"id": "k", "title": "KL divergence and relative entropy bounds", "abstract": "", "conceptIds": ["concept:s:e:kl"], "keywordMetadata": []},
    ]
    entries = {e["id"]: e for e in concept_quality(concepts, papers, {}, server="s", epoch="e")["ledger"]["entries"]}
    ot, kl = entries["concept:s:e:ot"], entries["concept:s:e:kl"]
    assert ot["status"] == "active" and ot["mergeScope"] == "per-paper" and ot["contextMergedInto"] == ["concept:s:e:optimal-transport"] and ot["contextMergedPaperCount"] == 1
    assert "mergedInto" not in ot                                                    # the shared acronym is never a global redirect
    assert kl["status"] == "suppressed" and kl["reason"] == "merged-into-canonical" and kl["mergeScope"] == "global" and kl["mergedInto"] == ["concept:s:e:kl-divergence"]


def test_real_snapshot_with_mixed_ot_context_validates_schema_and_decodes():
    papers = [{"key": "T", "title": "Occupational therapist-led OT sessions", "keywords": ["OT"]}, {"key": "M", "title": "OT plans with Wasserstein costs", "keywords": ["OT"]}]
    snapshot = _build(papers)
    snapshot["status"] = {"phase": "idle", "stale": False}
    jsonschema.validate(snapshot, json.loads((ROOT / "docs" / "snapshot.schema.json").read_text()))
    entry = next(e for e in snapshot["conceptQualityLedger"]["entries"] if e["label"]["en"] == "OT")
    assert entry["mergeScope"] == "per-paper" and "mergedInto" not in entry and entry["id"] in {c["id"] for c in snapshot["concepts"]}


# ------------------------------------------------------------ broad-domain Life Science (policy v2)
def test_broad_domain_provenance_explains_compound_or_independent_families_and_keeps_invariants():
    cat = _catalog()
    rows, _ = classify_papers([
        {"id": "c", "title": "Mitochondrial dysfunction"},
        {"id": "f", "title": "Mechanisms of stress response", "abstract": "Gene regulation and bacteria in lakes."},
        {"id": "t", "title": "Regulation", "keywords": ["gene expression"]}], cat)
    by = {r["paperId"]: r for r in rows}
    ref = lambda pid: next(m["evidenceRef"] for m in by[pid]["directMemberships"] if m["ontologyKey"] == "subject:life-science")
    assert ref("c")["ruleId"] == "broad-domain-compound" and ref("c")["evidenceTier"] == "metadata" and "spanRange" in ref("c")
    assert ref("f")["ruleId"] == "broad-domain-families" and set(ref("f")["families"]) >= {"molecular", "organism"} and ref("f")["field"] == "abstract"
    assert ref("t")["field"] == "keywords"
    assert all(_primary(r) == "life-science" for r in rows) and check_facet_invariants(rows, cat) == []        # primary is an accepted direct membership
    assert all(m["origin"] == "rule" and m["evidenceRef"]["sourceId"] == S for r in rows for m in r["directMemberships"])


def test_broad_domain_needs_real_evidence_not_lookalikes_or_weak_tiers_or_generic_clinical_or_math_codes():
    cat = _catalog()
    for title, abstract, full in (
            ("Genetic algorithm scheduling", "Genes of the chromosome encode schedules in a cellular automata model.", ""),   # look-alikes blanked even with biological-sounding words
            ("Neural network cell phone models", "", ""), ("Viral marketing campaigns for cell phones", "", ""),
            ("Annual report", "", "A protein and a gene and a cell appear in the appendix."),                                  # full text alone is never enough
            ("A single protein", "", ""),                                                                                      # one weak family only
            ("Clinical analysis of patients in hospital cohorts", "disease burden and mortality", ""),                         # generic clinical
            ("A theorem about 11M20 codes", "proof", ""), ("Generic analysis of statistics", "", "")):
        rows, _ = classify_papers([{"id": "p", "title": title, "abstract": abstract, "fullText": full}], cat)
        assert "subject:life-science" not in _keys(rows[0]) and _primary(rows[0]) is None, title
        assert "subject:analysis" not in _keys(rows[0]) and "subject:mathematics" not in _keys(rows[0])                   # no Mathematics fallback


def test_broad_domain_never_absorbs_narrow_subjects_tools_or_manual_choices():
    cat = _catalog()
    cases = {
        "A QIIME 2 plugin for gut microbiome analysis": ("bioinformatics", "We present an extensible plugin for sequence analysis."),
        "Phage therapy outcomes tracked with QIIME2": ("phage-biology", ""),
        "Wasserstein distances between gene expression profiles": ("optimal-transport", ""),
        "Kullback-Leibler divergence for gene expression clustering": ("kl-divergence", ""),
        "Real analysis of protein folding energy landscapes": ("analysis", "measure theory and Banach space methods"),
    }
    for title, (primary, abstract) in cases.items():
        rows, _ = classify_papers([_paper(title, abstract)], cat)
        assert _primary(rows[0]) == primary and "subject:life-science" not in _keys(rows[0]), title
    only_tool, _ = classify_papers([_paper("QIIME2 workflow notes")], cat)
    assert _primary(only_tool[0], "tool") == "qiime2" and _primary(only_tool[0]) is None
    # a manual choice always wins over the generated broad domain
    target = next(e["id"] for e in cat["entities"] if e["ontologyKey"] == "subject:topology")
    rows, _ = classify_papers([_paper("Protein folding kinetics of membrane receptors")], cat, overlay_entities={"zotero:gold:0:P": {"primaryFacetOverrides": {"subject": target}}})
    assert rows[0]["primaryByFacet"]["subject"] == target and rows[0]["status"] == "user"


def test_full_pipeline_broad_domain_counts_invariants_schema_and_decoder():
    papers = [
        {"key": "L1", "title": "Gut microbiota composition and metabolic syndrome"},
        {"key": "L2", "title": "CRISPR-Cas9 genome editing in zebrafish embryos"},
        {"key": "B1", "title": "A QIIME 2 plugin for amplicon analysis", "abstract": "We present an extensible plugin for sequence analysis."},
        {"key": "P1", "title": "Bacteriophage therapy in patients"},
        {"key": "N1", "title": "Genetic algorithm for job-shop scheduling"},
        {"key": "N2", "title": "Hospital staffing and nurse scheduling"},
    ]
    snapshot = _build(papers)
    snapshot["status"] = {"phase": "idle", "stale": False}
    schema = json.loads((ROOT / "docs" / "snapshot.schema.json").read_text())
    jsonschema.validate(snapshot, schema)
    labels = {f["id"]: f["label"]["en"] for f in snapshot["fields"]}
    display = {p["key"]: labels[p["displayFieldId"]] for p in snapshot["papers"]}
    assert display == {"L1": "Life Science", "L2": "Life Science", "B1": "Bioinformatics", "P1": "Phage Biology", "N1": "Unclassified", "N2": "Unclassified"}
    stats = {labels[r["fieldId"]]: r for r in snapshot["fieldStats"]["rows"]}
    assert (stats["Life Science"]["direct"], stats["Life Science"]["primary"], stats["Life Science"]["inclusive"]) == (2, 2, 4)        # + Bioinformatics + Phage descendants, unique papers
    assert stats["Bioinformatics"]["direct"] == stats["Bioinformatics"]["primary"] == 1
    assert snapshot["fieldStats"]["knownPrimary"] == 4 and snapshot["fieldStats"]["unknown"] == 2 and snapshot["fieldStats"]["paperCount"] == 6
    assert all(r["primary"] <= r["direct"] <= r["inclusive"] for r in snapshot["fieldStats"]["rows"])
    assert check_facet_invariants(snapshot["paperFacets"], snapshot["facetCatalog"]) == []
    by_key = {p["key"]: p["id"] for p in snapshot["papers"]}
    row = next(r for r in snapshot["paperFacets"] if r["paperId"] == by_key["L1"])
    assert any(m["evidenceRef"]["ruleId"].startswith("broad-domain") for m in row["directMemberships"])
    from tests.backend.test_taxonomy_v2_schema import _decode_with_node
    _decode_with_node(snapshot)


def test_broad_domain_evidence_is_cached_per_part_and_identical_with_cache(monkeypatch):
    papers = [{"id": f"x{i}", "title": "Gene expression in yeast", "abstract": "Protein and cell biology " + "word " * 200, "keywords": ["a"]} for i in range(3)]
    cache = {}
    first = _signature(_taxonomy.collect_evidence(papers, _catalog(), cache))
    calls = []
    original = _taxonomy._broad_bio_hits
    monkeypatch.setattr(_taxonomy, "_broad_bio_hits", lambda text: calls.append(len(text)) or original(text))
    for paper in papers:
        paper["keywords"] = ["something else"]          # keywords change between the graph's passes; large parts must not be rescanned
    second = _signature(_taxonomy.collect_evidence(papers, _catalog(), cache))
    assert second.keys() == first.keys() and all(k in second[p] for p in first for k in first[p])
    assert all(n < 100 for n in calls) and len(calls) <= len(papers)         # only the changed keywords part was (re)scanned
    assert _signature(_taxonomy.collect_evidence(papers, _catalog())) == second


# ------------------------------------------------ reserved control tags and historic generated aliases
def test_reserved_control_tags_are_not_scientific_evidence_at_classifier_input():
    cat = _catalog()
    for tag in ("auto-primary:phages-viruses", "auto-primary:microbiome-metagenomics", "auto-classifier:amplicon", "auto-classifier:qiime2"):
        rows, _ = classify_papers([_paper("Structure of a eukaryotic virus capsid", keywords=[tag])], cat)
        assert not _keys(rows[0], "subject") and not _keys(rows[0], "method") and not _keys(rows[0], "tool") and _primary(rows[0]) is None, tag
    # structured tag dicts and plain-string keywords are filtered the same way
    rows, _ = classify_papers([_paper("Notes", keywords=[{"tag": "auto-primary:phages-viruses"}])], cat)
    assert not _keys(rows[0])
    rows, _ = classify_papers([_paper("Notes", keywords="auto-primary:phages-viruses")], cat)
    assert not _keys(rows[0])
    # real keywords still work, a control tag next to them is ignored, and the source paper's tags are never modified
    paper = _paper("Notes", keywords=["bacteriophage", "auto-primary:microbiome-metagenomics"])
    before = copy.deepcopy(paper)
    rows, _ = classify_papers([paper], cat)
    assert _primary(rows[0]) == "phage-biology" and "method:metagenomics" not in _keys(rows[0]) and paper == before
    # manual facet overrides are unaffected by the filter
    topology = next(e["id"] for e in cat["entities"] if e["ontologyKey"] == "subject:topology")
    rows, _ = classify_papers([_paper("Notes", keywords=["auto-primary:phages-viruses"])], cat, overlay_entities={"zotero:gold:0:P": {"primaryFacetOverrides": {"subject": topology}}})
    assert rows[0]["primaryByFacet"]["subject"] == topology


def _previous_with_historic_ot_alias(redirect=True):
    prior_ot, prior_opt = f"concept:{S}:{E}:ot", f"concept:{S}:{E}:optimal-transport"
    previous = {"source": {"id": S, "serverId": S, "epoch": E},
                "concepts": [{"id": prior_opt, "label": {"en": "Optimal Transport"}, "aliases": ["OT"], "source": "extracted"},      # generated alias, never user-confirmed
                             {"id": prior_ot, "label": {"en": "OT"}, "aliases": [], "source": "extracted"}],
                "fields": []}
    if redirect:
        previous["taxonomyRedirects"] = {prior_ot: prior_opt}
    return previous, prior_ot, prior_opt


def test_historic_generated_ot_alias_and_redirect_do_not_globalize_senses_in_a_full_build():
    previous, prior_ot, prior_opt = _previous_with_historic_ot_alias()
    papers = [{"key": "MED", "title": "Occupational therapist-led OT sessions for stroke patients", "abstract": "Clinical cohort.", "keywords": ["OT"]},
              {"key": "MATH", "title": "OT plans with Wasserstein costs", "abstract": "Sinkhorn solvers.", "keywords": ["OT"]}]
    snapshot = _build(papers, previous=previous)
    by_key = {p["key"]: p for p in snapshot["papers"]}
    label = {c["id"]: c["label"]["en"].casefold() for c in snapshot["concepts"]}
    assert prior_opt not in by_key["MED"]["conceptIds"]                                   # medical OT never mapped through the generated alias
    assert {label[c] for c in by_key["MED"]["conceptIds"]} >= {"ot"}
    assert prior_opt in by_key["MATH"]["conceptIds"] and "ot" not in {label[c] for c in by_key["MATH"]["conceptIds"]}      # math OT still canonicalised per paper
    # history preserved, not deleted or applied: the old redirect still resolves and old IDs stay addressable
    assert snapshot["taxonomyRedirects"].get(prior_ot) == prior_opt
    assert not (prior_ot in label and set(by_key["MED"]["conceptIds"]) == {prior_opt})
    entries = {e["id"]: e for e in snapshot["conceptQualityLedger"]["entries"]}
    assert entries[prior_opt].get("mergeScope") in {None, "global", "per-paper"} and "mergedInto" not in entries.get(prior_ot, {})


def test_user_confirmed_ot_alias_remains_authoritative_over_context():
    prior_opt = f"concept:{S}:{E}:optimal-transport"
    taxonomy = {"concepts": [{"id": prior_opt, "label": {"en": "Optimal Transport"}, "aliases": ["OT"], "source": "user"}], "fields": []}
    papers = [{"key": "MED", "title": "Occupational therapist-led OT sessions", "keywords": ["OT"]}]
    snapshot = _build(papers, taxonomy=taxonomy)
    med = snapshot["papers"][0]
    assert prior_opt in med["conceptIds"]                                                   # the user's explicit alias is honoured, not context-gated
    entry = next(e for e in snapshot["conceptQualityLedger"]["entries"] if e["id"] == prior_opt)
    assert entry["status"] == "protected" and entry["protectedBy"] == "user"


# ------------------------------------------------ keyword provenance (no tier laundering) and auditable evidence spans
from atlas.taxonomy import NORMALIZATION_VERSION, resolve_evidence_span, source_keyword_items


def _facet_refs(snapshot, key):
    pid = next(p["id"] for p in snapshot["papers"] if p["key"] == key)
    row = next(r for r in snapshot["paperFacets"] if r["paperId"] == pid)
    return row, {m["ontologyKey"]: m["evidenceRef"] for m in row["directMemberships"]}


def test_derived_keywords_never_launder_indexed_or_abstract_evidence_into_keyword_confidence():
    snapshot = _build([
        {"key": "IDX", "title": "Notes on laboratory methods", "keywords": ["lab"], "indexedText": "bacteriophage lysis kinetics were measured"},
        {"key": "ABS", "title": "Untitled note", "abstract": "bacteriophage lysis kinetics"},
        {"key": "TAG", "title": "Notes", "keywords": ["bacteriophage"]},
    ])
    row, refs = _facet_refs(snapshot, "IDX")
    assert "subject:phage-biology" not in refs and row["primaryByFacet"]["subject"] is None             # indexed-tier 0.55 stays below the direct threshold
    _, refs = _facet_refs(snapshot, "ABS")
    assert refs["subject:phage-biology"]["field"] == "abstract" and refs["subject:phage-biology"]["score"] == 0.8     # not raised to the keyword tier (0.92)
    _, refs = _facet_refs(snapshot, "TAG")
    assert refs["subject:phage-biology"]["field"] == "keywords" and refs["subject:phage-biology"]["score"] == 0.92    # original tags keep precedence
    for paper in snapshot["papers"]:
        assert "_sourceKeywords" not in paper                                                           # private working copy never published
    assert any("bacteriophage" in p["keywords"] for p in snapshot["papers"] if p["key"] == "IDX")        # the derived concept keywords still exist for the graph


def test_source_keyword_items_normalizes_string_mapping_and_list_forms_without_mutation():
    paper = {"keywords": "bacteriophage", "tags": [{"tag": "yeast"}, "auto-primary:phages-viruses", "mouse"]}
    before = copy.deepcopy(paper)
    items = source_keyword_items(paper)
    assert items == ["bacteriophage", {"tag": "yeast"}, "mouse"]                    # a string is ONE keyword, never characters; control tag removed
    assert source_keyword_items({"keywords": {"tag": "x"}}) == [{"tag": "x"}] and source_keyword_items({"keywords": ""}) == [] and source_keyword_items({}) == []
    assert source_keyword_items({"keywords": ("a", "b")}) == ["a", "b"] and paper == before


RAW_FORMS = {
    "title": {"key": "A", "title": "Bacteriophage biology"},
    "title-whitespace": {"key": "A", "title": "Bacteriophage   biology\n notes"},
    "abstract-whitespace": {"key": "A", "title": "x", "abstract": "a  bacteriophage\n study"},
    "keywords-list": {"key": "A", "title": "x", "keywords": ["lab", "bacteriophage"]},
    "keywords-string": {"key": "A", "title": "x", "keywords": "bacteriophage"},
    "tags-only": {"key": "A", "title": "x", "tags": [{"tag": "bacteriophage"}]},
    "keywords-and-tags": {"key": "A", "title": "x", "keywords": ["lab"], "tags": ["bacteriophage"]},
    "indexed": {"key": "A", "title": "x", "keywords": ["lab"], "indexedText": "bacteriophage   lysis\\nkinetics bacteriophage phage"},
}


@pytest.mark.parametrize("name", sorted(RAW_FORMS))
def test_stored_evidence_span_resolves_locally_against_the_original_raw_source_after_a_real_build(name):
    raw = RAW_FORMS[name]
    source_copy = copy.deepcopy(raw)
    snapshot = _build([copy.deepcopy(raw)])
    assert raw == source_copy                                                       # the raw source row is untouched
    _, refs = _facet_refs(snapshot, "A")
    ref = refs.get("subject:phage-biology")
    if name == "indexed":
        assert ref is None                                                          # indexed-only evidence is below the direct threshold (no span stored)
        return
    assert ref is not None and ref["normalization"] == NORMALIZATION_VERSION and ref["inputHash"].startswith("sha256:") and len(ref["spanRange"]) == 2
    assert resolve_evidence_span(raw, ref) == "bacteriophage" or resolve_evidence_span(raw, ref) == "phage"
    # the public ref carries hash + offsets only; no raw text anywhere in the published snapshot
    assert "bacteriophage" not in json.dumps(ref) and "evidenceSpan" in ref and ref["evidenceSpan"] is None


def test_resolver_rejects_edited_inputs_other_normalizations_malformed_refs_and_binds_the_field():
    raw = {"key": "A", "title": "Bacteriophage biology", "abstract": "marker gene amplicon study"}
    snapshot = _build([copy.deepcopy(raw)])
    _, refs = _facet_refs(snapshot, "A")
    ref = refs["subject:phage-biology"]
    assert resolve_evidence_span(raw, ref) == "bacteriophage"
    assert resolve_evidence_span({**raw, "title": "Bacteriophage biology!"}, ref) is None                      # edited field: hash mismatch
    assert resolve_evidence_span({**raw, "title": "Bacteriophage   biology"}, ref) == "bacteriophage"          # whitespace-only differences normalize identically
    assert resolve_evidence_span(raw, {**ref, "normalization": "other-v9"}) is None
    assert resolve_evidence_span(raw, {**ref, "field": "abstract"}) is None                                    # hash binds the exact field text
    assert resolve_evidence_span(raw, {**ref, "spanRange": [5, 2]}) is None and resolve_evidence_span(raw, {**ref, "spanRange": [0, 10 ** 9]}) is None
    assert resolve_evidence_span(raw, {"field": "title"}) is None and resolve_evidence_span(raw, None) is None
    amp = refs.get("method:amplicon")
    assert amp is not None and amp["field"] == "abstract" and resolve_evidence_span(raw, amp) in {"amplicon", "marker gene"}


def test_resolver_parity_on_sync_normalized_source_rows_and_published_refs(tmp_path):
    from tests.backend.test_taxonomy_v2_persistence import Replay, _manager, _settle
    class Rows(Replay):
        def __init__(self):
            super().__init__()
            self.items = {"A1": (1, {"key": "A1", "version": 1, "data": {"itemType": "journalArticle", "title": "QIIME  2 microbiome\\namplicon study", "abstractNote": "16S marker  gene data",
                                                                          "tags": [{"tag": "bacteriophage"}, {"tag": "auto-primary:microbiome-metagenomics"}]}})}
    manager = _manager(tmp_path, replay=Rows())
    manager.sync(); _settle(manager)
    snapshot = manager.snapshot()
    source_papers, _, _ = manager._normalized_records(manager.store.state())          # original raw-normalized rows as the pipeline read them
    before = copy.deepcopy(source_papers)
    row = snapshot["paperFacets"][0]
    checked = 0
    for membership in row["directMemberships"]:
        ref = membership["evidenceRef"]
        if ref.get("spanRange"):
            text = resolve_evidence_span(source_papers[0], ref)
            assert text and text == text.casefold()
            checked += 1
    assert checked >= 3 and source_papers == before
    assert not any("auto-primary" in json.dumps(m) for m in row["directMemberships"]) and "method:metagenomics" not in {m["ontologyKey"] for m in row["directMemberships"]}
    manager.stop_watcher()


# ------------------------------------------------ public, self-contained gold policy record (no .omx dependency)
def test_public_policy_record_pins_the_four_static_corrections_and_unchanged_inputs_and_splits():
    record = json.loads((ROOT / "tests" / "fixtures" / "taxonomy_gold_v2_policy.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((ROOT / "tests" / "fixtures" / "taxonomy_gold_v2_policy.json").read_bytes()).hexdigest() == "2994a6d5cb9b92b44e5b9c14e16531c8702793a8063852d848c9bafe6a882853"
    assert record["archivedV1"]["sha256"] == GOLD_SHA256 == hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest() and record["v2"]["sha256"] == GOLD_V2_SHA256
    assert [c["id"] for c in record["corrections"]] == ["g039", "g040", "g041", "g070"] and {c["id"] for c in record["corrections"]} == POLICY_CORRECTED_IDS
    # the record is a static literal: its expectations are the reviewed values, independent of any classifier output
    assert {c["id"]: (c["title"], c["v1ExpectedPrimary"], c["v2ExpectedPrimary"]) for c in record["corrections"]} == {
        "g039": ("Gut microbiome and diet in infants", None, "life-science"), "g040": ("The skin microbiota across seasons", None, "life-science"),
        "g041": ("Microbial communities in lakes", None, "life-science"), "g070": ("Gene expression analysis in cancer", None, "life-science")}
    assert all(c["reason"] for c in record["corrections"]) and "none is computed from classifier output" in record["purpose"]
    v1 = {c["id"]: c for c in json.loads(GOLD_PATH.read_text(encoding="utf-8"))}
    v2 = {c["id"]: c for c in json.loads(GOLD_V2_PATH.read_text(encoding="utf-8"))["cases"]}
    assert record["archivedV1"]["caseCount"] == len(v1) and record["added"]["count"] == len(v2) - len(v1)
    for cid, old in v1.items():
        assert (v2[cid]["title"], v2[cid]["abstract"], v2[cid]["keywords"]) == (old["title"], old["abstract"], old["keywords"])             # original inputs unchanged
        assert (cid in POLICY_CORRECTED_IDS) == (v2[cid]["expected"] != old["expected"])                                                  # ONLY the four corrections differ
    for cid, case in {c["id"]: c for c in _gold(GOLD_V2_PATH)}.items():                                                                    # splits follow the frozen id-hash rule
        assert case["split"] == ("heldout" if int(hashlib.sha256(cid.encode()).hexdigest()[:8], 16) % 4 == 0 else "calibration")
    assert "not unchanged v1 accuracy" in record["honesty"] and "not blind held-out accuracy" in record["honesty"]

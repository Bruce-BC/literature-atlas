"""Deterministic multifacet taxonomy, evidence classification and concept quality.

The graph builder keeps the legacy ``fields``/``memberships`` projection for
older clients.  This module supplies the richer, source-scoped facet payload.

Design notes
* Facet references (``broaderIds``, ``directMemberships.facetId``,
  ``primaryByFacet``) always use the canonical source/epoch scoped
  ``entity.id``; ``ontologyKey`` is the semantic ``kind:slug`` key.
* ``primary`` is chosen independently per facet kind and is always one of the
  accepted direct memberships.  Abstention is ``None`` (never a fake entity).
* Evidence references carry field/tier/offset metadata, never raw text.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable, Mapping

from .embeddings import normalize_text

FACET_KINDS = ("subject", "method", "tool", "application")
CATALOG_VERSION = "facet-catalog-v4"
RULE_VERSION = "taxonomy-v4"
NORMALIZATION_VERSION = "nfkc-casefold-hyphen-v1"          # text normalization the stored span offsets refer to
ONTOLOGY_POLICY_VERSION = "ontology-label-policy-v2"      # broad-domain Life Science policy; see docs/implementation-interfaces.md
QUALITY_VERSION = "concept-quality-v4"
STATS_VERSION = "field-stats-v3"
INPUT_STATES = ("absent", "uninitialized", "intentional-empty-valid", "valid-custom", "invalid", "disabled")

DIRECT_THRESHOLD = 0.6
PRIMARY_THRESHOLD = 0.75
AMBIGUITY_MARGIN = 0.1
TERMHOOD_THRESHOLD = 0.5
MIN_LIBRARY_FOR_DF = 20
MAX_TEXT = 30000

_HYPHEN = re.compile(r"[‐‑‒–—−]")


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _HYPHEN.sub("-", text)


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", _norm(value)).strip("-")
    return text or "unknown"


def _label(value: Any, fallback: str = "") -> dict[str, str]:
    if isinstance(value, Mapping):
        en = str(value.get("en", fallback)); return {"en": en, "ko": str(value.get("ko", en))}
    return {"en": str(value or fallback), "ko": str(value or fallback)}


def entity_id(server: str, epoch: str, kind: str, slug: str) -> str:
    return f"facet:{server}:{epoch}:{kind}:{slug}"


# --------------------------------------------------------------------------
# Builtin ontology
# --------------------------------------------------------------------------
_BUILTIN: tuple[dict[str, Any], ...] = (
    {"key": "mathematics", "kind": "subject", "label": ("Mathematics", "수학")},
    {"key": "analysis", "kind": "subject", "label": ("Analysis", "해석학"), "broader": ("mathematics",), "aliases": ("mathematical analysis",)},
    {"key": "topology", "kind": "subject", "label": ("Topology", "위상수학"), "broader": ("mathematics",)},
    {"key": "probability", "kind": "subject", "label": ("Probability", "확률론"), "broader": ("mathematics",)},
    {"key": "optimization", "kind": "subject", "label": ("Optimization", "최적화"), "broader": ("mathematics",)},
    {"key": "optimal-transport", "kind": "subject", "label": ("Optimal Transport", "최적수송"), "broader": ("optimization",)},
    {"key": "information-theory", "kind": "subject", "label": ("Information Theory", "정보이론"), "broader": ("mathematics",), "legacy": "information"},
    {"key": "kl-divergence", "kind": "subject", "label": ("KL Divergence", "KL 발산"), "broader": ("information-theory",)},
    {"key": "life-science", "kind": "subject", "label": ("Life Science", "생명과학")},
    {"key": "bioinformatics", "kind": "subject", "label": ("Bioinformatics", "생물정보학"), "broader": ("life-science",)},
    {"key": "phage-biology", "kind": "subject", "label": ("Phage Biology", "파지 생물학"), "broader": ("life-science",), "legacy": "phage"},
    {"key": "genome-biology", "kind": "subject", "label": ("Genome Biology", "유전체 생물학"), "broader": ("life-science",)},
    {"key": "protein-science", "kind": "subject", "label": ("Protein Science", "단백질 과학"), "broader": ("life-science",)},
    {"key": "drug-discovery", "kind": "subject", "label": ("Drug Discovery", "신약 개발"), "broader": ("life-science",)},
    {"key": "statistics", "kind": "subject", "label": ("Statistics", "통계학"), "broader": ("mathematics",)},
    {"key": "computer-science", "kind": "subject", "label": ("Computer Science", "컴퓨터과학")},
    {"key": "machine-learning", "kind": "subject", "label": ("Machine Learning", "기계학습"), "broader": ("computer-science",)},
    {"key": "quantum-computing", "kind": "subject", "label": ("Quantum Computing", "양자컴퓨팅"), "broader": ("computer-science",)},
    {"key": "medicine", "kind": "subject", "label": ("Medicine & Health", "의학·보건")},
    {"key": "amplicon", "kind": "method", "label": ("Amplicon / Marker Gene", "앰플리콘")},
    {"key": "metagenomics", "kind": "method", "label": ("Metagenomics", "메타게놈")},
    {"key": "qiime2", "kind": "tool", "label": ("QIIME 2", "QIIME 2")},
    {"key": "microbiome", "kind": "application", "label": ("Microbiome", "마이크로바이옴")},
    {"key": "phage-therapy", "kind": "application", "label": ("Phage Therapy", "파지 치료")},
)
LEGACY_BUILTIN_SLUG = {"information": "information-theory", "phage": "phage-biology"}
# Gap-filling subjects (taxonomy-v4): they never compete with the core subjects above.  A paper takes one of them as its
# primary only when no core subject could be chosen, so adding them moves papers out of Unclassified and nowhere else.
FALLBACK_SUBJECTS = frozenset({"subject:mathematics", "subject:genome-biology", "subject:protein-science", "subject:drug-discovery", "subject:statistics",
                               "subject:computer-science", "subject:machine-learning", "subject:quantum-computing", "subject:medicine"})


def taxonomy_input_state(taxonomy: Any) -> tuple[str, str | None]:
    """Return ``(state, reason)`` for a stored taxonomy payload."""
    if taxonomy is None:
        return "absent", None
    if not isinstance(taxonomy, Mapping):
        return "invalid", "taxonomy payload is not an object"
    raw = taxonomy.get("inputState")
    status = raw.get("status") if isinstance(raw, Mapping) else raw
    reason = raw.get("reason") if isinstance(raw, Mapping) else None
    status = str(status or "").strip().lower()
    if status in INPUT_STATES:
        return status, (str(reason) if reason else None)
    if taxonomy.get("disabled") is True:
        return "disabled", "taxonomy disabled"
    if taxonomy.get("fields") or taxonomy.get("concepts") or (taxonomy.get("facetCatalogConfig") or {}).get("entities"):
        return "valid-custom", None
    return "absent", None


def _config_entities(taxonomy: Any, server: str, epoch: str) -> list[dict[str, Any]]:
    if not isinstance(taxonomy, Mapping):
        return []
    config = taxonomy.get("facetCatalogConfig")
    rows = config.get("entities", []) if isinstance(config, Mapping) else []
    prefix = f"facet:{server}:{epoch}:"
    result = []
    for row in rows if isinstance(rows, list) else []:
        # Entities fenced to another source/epoch are ignored, never remapped.
        if isinstance(row, Mapping) and str(row.get("id", "")).startswith(prefix) and row.get("kind") in FACET_KINDS:
            result.append(dict(row))
    return result


def build_facet_catalog(server: str, epoch: str, fields: Mapping[str, Mapping[str, Any]] | None = None,
                        taxonomy: Any = None, *, input_state: str | None = None) -> dict[str, Any]:
    state, _ = taxonomy_input_state(taxonomy)
    state = input_state or state
    rows: dict[str, dict[str, Any]] = {}          # entity id -> row
    by_key: dict[str, str] = {}                   # ontologyKey -> entity id
    if state not in {"invalid", "disabled"}:
        for raw in _BUILTIN:
            kind, key = raw["kind"], raw["key"]
            eid = entity_id(server, epoch, kind, key)
            rows[eid] = {"id": eid, "facetId": f"{kind}:{key}", "key": key, "ontologyKey": f"{kind}:{key}", "kind": kind,
                         "label": {"en": raw["label"][0], "ko": raw["label"][1]}, "broaderIds": [], "aliases": list(raw.get("aliases", ())),
                         "origin": "builtin", "legacyFieldId": None, "_broader": [entity_id(server, epoch, kind, b) for b in raw.get("broader", ())]}
            by_key[f"{kind}:{key}"] = eid
    # Custom facet config; parent references may be entity IDs or semantic keys.
    pending_parents: dict[str, list[str]] = {}
    for raw in _config_entities(taxonomy, server, epoch) if state not in {"invalid", "disabled"} else []:
        eid, kind = str(raw["id"]), str(raw["kind"])
        slug = eid.split(":", 4)[-1] if eid.count(":") >= 4 else _slug(raw.get("label"))
        existing = rows.get(eid)
        label = _label(raw.get("label"), slug)
        aliases = [str(x) for x in raw.get("aliases", []) if isinstance(x, str) and x.strip()]
        keywords = [str(x) for x in raw.get("keywords", []) if isinstance(x, str) and x.strip()]
        if existing is not None:   # editing a builtin: merge aliases, keep identity
            existing["aliases"] = sorted(set(existing["aliases"]) | set(aliases))
            if keywords: existing["keywords"] = sorted(set(existing.get("keywords", [])) | set(keywords))
            continue
        ontology = str(raw.get("ontologyKey") or f"{kind}:{slug.rsplit(':', 1)[-1]}")
        rows[eid] = {"id": eid, "facetId": ontology, "key": slug, "ontologyKey": ontology, "kind": kind, "label": label,
                     "broaderIds": [], "aliases": aliases, "origin": "custom", "legacyFieldId": raw.get("legacyFieldId"),
                     "_broader": []}
        if keywords: rows[eid]["keywords"] = keywords
        by_key[ontology] = eid
        pending_parents[eid] = [str(x) for x in raw.get("broaderIds", []) if isinstance(x, str)]
    # Legacy fields: merge with a builtin only when label AND parent context match.
    legacy = dict(fields or {})
    field_entity: dict[str, str] = {}
    order = sorted(legacy, key=lambda f: (len(legacy[f].get("parentIds", []) or []), f))
    resolved_parents: dict[str, set[str]] = {}
    def parent_entities(fid: str, seen: frozenset = frozenset()) -> set[str] | None:
        out = set()
        for pid in legacy.get(fid, {}).get("parentIds", []) or []:
            if pid in seen or pid not in legacy: continue
            if pid not in field_entity: return None
            out.add(field_entity[pid])
        return out
    for _ in range(len(order) + 1):
        progressed = False
        for fid in order:
            if fid in field_entity: continue
            parents = parent_entities(fid)
            if parents is None: continue
            row = legacy[fid]; lbl = row.get("label", {}); text = lbl.get("en") if isinstance(lbl, Mapping) else lbl
            slug = fid.rsplit(":", 1)[-1]
            builtin_key = LEGACY_BUILTIN_SLUG.get(slug, slug)
            target = None
            for ontology, eid in by_key.items():
                cand = rows[eid]
                if cand["origin"] != "builtin" or cand["kind"] != "subject" or cand["legacyFieldId"]: continue
                label_match = _norm(text) == _norm(cand["label"]["en"]) or cand["key"] == builtin_key and str(row.get("source")) == "model"
                if label_match and parents == set(cand["_broader"]):
                    target = eid; break
            if target is None and str(row.get("source")) == "model" and builtin_key == "unknown":
                field_entity[fid] = ""; progressed = True; continue
            if target is not None:
                rows[target]["legacyFieldId"] = fid
                rows[target]["aliases"] = sorted(set(rows[target]["aliases"]) | {str(x) for x in row.get("aliases", [])})
                field_entity[fid] = target
            else:
                slug_l = _slug(text)
                eid = entity_id(server, epoch, "subject", slug_l)
                if eid in rows:
                    eid = entity_id(server, epoch, "subject", f"{slug_l}-{hashlib.sha256(fid.encode()).hexdigest()[:8]}")
                ontology = f"subject:{eid.rsplit(':', 1)[-1]}"
                rows[eid] = {"id": eid, "facetId": ontology, "key": eid.rsplit(":", 1)[-1], "ontologyKey": ontology, "kind": "subject",
                             "label": _label(lbl, slug), "broaderIds": [], "aliases": [str(x) for x in row.get("aliases", [])],
                             "origin": "custom", "legacyFieldId": fid, "_broader": sorted(parents)}
                by_key[ontology] = eid; field_entity[fid] = eid
            progressed = True
        if not progressed: break
    # Normalize parent aliases (semantic key / entity id) BEFORE the cycle check.
    for eid, parents in pending_parents.items():
        rows[eid]["_broader"] = [p for p in dict.fromkeys(by_key.get(p, p) for p in parents)]
    for row in rows.values():
        row["broaderIds"] = [p for p in dict.fromkeys(row.pop("_broader")) if p in rows and p != row["id"] and rows[p]["kind"] == row["kind"]]
    dropped_cycles = _break_cycles(rows)
    entities = sorted(rows.values(), key=lambda item: (item["kind"], item["id"]))
    return {"version": CATALOG_VERSION, "namespace": f"atlas:{server}:{epoch}", "sourceId": server, "sourceEpoch": epoch,
            "entities": entities, "kinds": list(FACET_KINDS), "inputState": state,
            "validationErrors": dropped_cycles + validate_facet_catalog({"entities": entities})}


def find_back_edges(parents: Mapping[str, Iterable[str]]) -> list[tuple[str, str]]:
    """Return the ``(child, parent)`` edges that close a cycle (iterative, O(V+E)).

    Three-colour depth-first search over sorted nodes with an explicit stack, so
    deep chains cannot overflow the recursion limit and shared ancestors (diamond
    DAGs) are visited once.  Edges to unknown nodes are ignored.  Removing the
    returned edges makes the graph acyclic deterministically.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    back: list[tuple[str, str]] = []
    for root in sorted(parents):
        if color.get(root, WHITE) != WHITE:
            continue
        color[root] = GRAY
        stack: list[tuple[str, list[str], int]] = [(root, list(parents.get(root, ())), 0)]
        while stack:
            node, edges, index = stack.pop()
            if index < len(edges):
                stack.append((node, edges, index + 1))
                parent = edges[index]
                if parent not in parents:
                    continue
                state = color.get(parent, WHITE)
                if state == GRAY:
                    back.append((node, parent))
                elif state == WHITE:
                    color[parent] = GRAY
                    stack.append((parent, list(parents.get(parent, ())), 0))
            else:
                color[node] = BLACK
    return back


def _break_cycles(rows: dict[str, dict[str, Any]]) -> list[str]:
    """Drop cycle-closing parent edges deterministically and report each one."""
    back = find_back_edges({eid: row.get("broaderIds", []) for eid, row in rows.items()})
    dropped = set(back)
    for child, parent in dropped:
        rows[child]["broaderIds"] = [p for p in rows[child]["broaderIds"] if p != parent]
    return [f"cyclic parent edge dropped: {child} -> {parent}" for child, parent in sorted(dropped)]


def validate_facet_catalog(catalog: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    entities = [x for x in (catalog.get("entities", []) if isinstance(catalog, Mapping) else []) if isinstance(x, Mapping)]
    by_id = {str(x.get("id")): x for x in entities}
    if len(by_id) != len(entities): errors.append("duplicate facet id")
    for row in entities:
        if row.get("kind") not in FACET_KINDS: errors.append(f"invalid facet kind {row.get('kind')}")
        for parent in row.get("broaderIds", []) or []:
            if parent not in by_id: errors.append(f"unknown parent {row.get('id')}->{parent}")
            elif by_id[parent].get("kind") != row.get("kind"): errors.append(f"cross-kind parent {row.get('id')}->{parent}")
    return errors


def _ancestors(by_id: Mapping[str, Mapping[str, Any]], eid: str) -> set[str]:
    seen: set[str] = set(); stack = list(by_id.get(eid, {}).get("broaderIds", []))
    while stack:
        p = stack.pop()
        if p in seen or p not in by_id: continue
        seen.add(p); stack.extend(by_id[p].get("broaderIds", []))
    return seen


# --------------------------------------------------------------------------
# Evidence rules
# --------------------------------------------------------------------------
_TIER_WEIGHT = {"title": 1.0, "keywords": 0.92, "abstract": 0.8, "indexedText": 0.55, "fullText": 0.45}
_TIER_NAME = {"title": "metadata", "keywords": "metadata", "abstract": "metadata", "indexedText": "indexed", "fullText": "fulltext"}
_PARTS = ("title", "keywords", "abstract", "indexedText", "fullText")

# (ontologyKey, ruleId, strength, regex)
_RULES: list[tuple[str, str, float, re.Pattern[str], str | None]] = []
# Literal prefilter: a rule pattern that starts with ``\b<literal>`` can only match text that
# contains that literal, so a plain substring test (memchr speed) may skip the regex without
# changing any result.  Patterns with top-level alternation or no safe literal prefix are
# never skipped.  ``_PREFILTER`` exists so tests can prove on/off equivalence.
_PREFILTER = True
_META = set("\\()[]{}?*+|.^$")


def _literal_needle(pattern: str) -> str | None:
    text = pattern[2:] if pattern.startswith("\\b") else None
    if text is None:
        return None
    depth, prefix = 0, ""
    for index, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "|" and depth == 0:
            return None
    for char in text:
        if char in _META:
            if char in "?*+{" and prefix:
                prefix = prefix[:-1]
            break
        prefix += char
    return prefix if len(prefix) >= 3 else None


def _add(key: str, rule: str, strength: float, *patterns: str) -> None:
    for pattern in patterns:
        _RULES.append((key, rule, strength, re.compile(pattern), _literal_needle(pattern)))

# Only an explicit QIIME 2 / QIIME2 / q2-* plugin names the tool.  QIIME 1 and
# unversioned "QIIME" must not invent version 2.
# A bare ``q2-<word>`` (e.g. a "Q2-2023" report code) is not evidence: only named
# QIIME 2 plugins count, or any ``q2-<letters>`` token together with explicit
# plugin context (handled in ``collect_evidence``).
_Q2_PLUGINS = ("feature-table", "dada2", "deblur", "diversity", "diversity-lib", "taxa", "feature-classifier", "composition", "longitudinal",
               "phylogeny", "alignment", "emperor", "demux", "cutadapt", "vsearch", "quality-filter", "sample-classifier", "metadata",
               "types", "picrust2", "gneiss", "fragment-insertion", "quality-control", "moshpit", "boots", "vizard", "greengenes2")
_add("tool:qiime2", "qiime2-token", 1.0, r"\bqiime\s?2\b", r"\bq2-(?:" + "|".join(re.escape(x) for x in _Q2_PLUGINS) + r")\b")
_Q2_GENERIC = re.compile(r"\bq2-[a-z][a-z0-9-]+")
_PLUGIN_WORD = re.compile(r"\bplugins?\b")
_QIIME_FAMILY = re.compile(r"\bqiime\s?[12]?\b|\bq2-(?:" + "|".join(re.escape(x) for x in _Q2_PLUGINS) + r")\b")
_add("method:amplicon", "amplicon-term", 1.0, r"\bamplicons?\b", r"\bmarker[- ]gene", r"\b16s(?:\s*r?rna)?\b", r"\b18s(?:\s*r?rna)?\b", r"\bits[12]?\s+(?:region|sequenc|amplicon)")
_add("method:amplicon", "asv-otu", 0.7, r"\b(?:asvs?|otus?)\b")
_add("method:metagenomics", "metagenomics-term", 1.0, r"\bmetagenom", r"\bshotgun\s+(?:metagenom|sequenc)", r"\bmoshpit\b")
_add("application:microbiome", "microbiome-term", 1.0, r"\bmicrobiome", r"\bmicrobiota\b", r"\bmicrobial\s+communit")
_add("application:phage-therapy", "phage-therapy-term", 1.0, r"\bphage\s+(?:therapy|therapies|treatment|cocktail)", r"\btherapeutic\s+phage", r"\bphage[- ]based\s+(?:therapy|treatment)")
_add("subject:phage-biology", "phage-term", 1.0, r"\bbacteriophages?\b", r"\bphages?\b", r"\bprophages?\b")
_add("subject:bioinformatics", "bioinformatics-term", 1.0, r"\bbioinformatic", r"\bsequence\s+(?:alignment|analysis)", r"\bgenome\s+assembly", r"\bcomputational\s+biology", r"\bphylogenetic", r"\bvariant\s+calling")
_add("subject:analysis", "math-analysis", 0.95, r"\breal\s+analysis", r"\bfunctional\s+analysis", r"\bcomplex\s+analysis", r"\bmathematical\s+analysis", r"\bmeasure\s+theory", r"\blebesgue", r"\bbanach\s+space", r"\bhilbert\s+space", r"\bsobolev", r"\bfourier\s+analysis", r"\bharmonic\s+analysis", r"\bcalculus\s+of\s+variations")
_add("subject:topology", "topology-strong", 0.95, r"\btopolog(?:y|ical)\s+(?:space|group|invariant|manifold)", r"\balgebraic\s+topology", r"\bgeneral\s+topology", r"\bhomotopy", r"\bcohomology", r"\bhomology\s+group", r"\bmanifolds?\b", r"\bpersistent\s+homology")
_add("subject:topology", "topology-weak", 0.62, r"\btopolog(?:y|ical)\b")
_add("subject:probability", "probability-strong", 0.95, r"\bprobability\s+(?:measure|theory|space)", r"\bstochastic\s+process", r"\bmarkov\s+chain", r"\bmartingale", r"\brandom\s+variable", r"\bbrownian")
_add("subject:probability", "probability-weak", 0.6, r"\bstochastic\b(?!\s+optimi)", r"\bprobabilistic\b")
_add("subject:optimization", "optimization-strong", 0.95, r"\bconvex\s+optimi[sz]ation", r"\blinear\s+programming", r"\bgradient\s+descent", r"\blagrangian", r"\bcombinatorial\s+optimi[sz]ation", r"\bstochastic\s+optimi[sz]ation")
_add("subject:optimization", "optimization-weak", 0.6, r"\boptimi[sz]ation\b")
_add("subject:optimal-transport", "ot-term", 1.0, r"\boptimal\s+transport", r"\bwasserstein", r"\bearth\s+mover", r"\bmonge[- ]kantorovich", r"\bsinkhorn")
_add("subject:kl-divergence", "kl-term", 1.0, r"\bkullback[- ]leibler", r"\bkl[- ]divergence", r"\brelative\s+entropy")
_add("subject:information-theory", "info-theory", 0.95, r"\binformation\s+theor", r"\bmutual\s+information", r"\bshannon\b", r"\bchannel\s+capacity")
_add("subject:information-theory", "entropy-weak", 0.6, r"\bentropy\b")
# Broad domains added in taxonomy-v4: papers outside the original mathematics/life-science core (statistics software,
# machine learning, quantum computing, genomics, protein design, drug discovery, clinical work) used to fall to Unclassified.
_add("subject:genome-biology", "genome-strong", 0.95, r"\bgenom(?:e|es|ic|ics)\b", r"\bgenome[- ]wide", r"\btranscriptom", r"\bsingle[- ]cell", r"\brna[- ]seq\b", r"\bchip[- ]seq\b", r"\b(?:dna|rna|genome|third[- ]generation|long[- ]read|next[- ]generation|high[- ]throughput|nanopore|illumina)\s+sequencing", r"\bgene\s+(?:expression|annotation|identification|prediction|regulation|families|family)", r"\btransposable\s+elements?", r"\bchromatin\b", r"\bepigenom", r"\bribosome\s+profiling", r"\bsequence\s+variants?", r"\bgwas\b", r"\btranscription\s+factors?", r"\bde\s+novo\s+assembl", r"\bpan[- ]?genom", r"\bencode\b")
_add("subject:genome-biology", "genome-weak", 0.7, r"\bsequencing\b", r"\bgenes?\b", r"\bdna\b", r"\balleles?\b", r"\bgenetic\b")
_add("subject:protein-science", "protein-strong", 0.95, r"\bprotein\s+(?:design|structures?|folding|engineering|language\s+models?|complex(?:es)?|binders?|nanomaterials?|sequences?|families|function)", r"\bpeptides?\b", r"\bmolecular\s+dynamics", r"\balphafold", r"\brosetta\b", r"\bcryo[- ]?em\b", r"\benzymes?\s+design", r"\bantibod(?:y|ies)\b", r"\bsmall\s+proteins?\b", r"\bproteom")
_add("subject:protein-science", "protein-weak", 0.65, r"\bproteins?\b", r"\benzym")
_add("subject:drug-discovery", "drug-strong", 0.95, r"\bdrug\s+(?:discovery|design|combinations?|repurposing|targets?|synerg\w*|response|development|candidates?)", r"\bdocking\b", r"\bvirtual\s+screening", r"\bsmall[- ]molecules?\b", r"\bligand", r"\bmolecul(?:e|ar)\s+generation", r"\bpharmacolog", r"\badmet\b", r"\bmedicinal\s+chemistry")
_add("subject:drug-discovery", "drug-weak", 0.65, r"\bdrugs?\b", r"\bcompounds?\b")
_add("subject:statistics", "statistics-strong", 0.95, r"\bregressions?\b", r"\bmixed[- ]effects?\b", r"\blinear\s+mixed", r"\bgeneralized\s+linear", r"\bsurvival\s+analysis", r"\bhypothesis\s+test", r"\bstatistical\s+(?:inference|models?|tests?|methods?|analysis|power|significance|learning)", r"\bbayesian\s+(?:inference|statistics|models?|hierarchical|analysis)", r"\bmodel\s+selection", r"\bmaximum\s+likelihood", r"\bbootstrap", r"\b(?:an?|the)\s+r\s+package", r"\bpackage\s+for\s+r\b", r"\bin\s+r\b", r"\bcran\b", r"\bcensored\b", r"\btwo[- ]phase\s+designs?", r"\bcount\s+data", r"\bcategorical\s+data", r"\bcontingency\s+tables?", r"\bsimultaneous\s+equations", r"\beconometric", r"\bsparse\s+matri", r"\bresidual[- ]based", r"\bmultiple\s+testing", r"\bfalse\s+discovery\s+rate", r"\banova\b", r"\bestimators?\b", r"<i>r</i>", r"\bpackages?\b", r"\bimputations?\b", r"\bautocorrelation", r"\bsurveys?\b", r"\bconditional\s+inference", r"\btime\s+series", r"\bkernel\s+methods?", r"\bsupport\s+vector", r"\bboost(?:ed|ing)\b", r"\bstatistical\s+computing", r"\balgebraic\s+statistics", r"\bsubpopulations?", r"\brcpp\b", r"\bmultcomp\b", r"\bbinning\b", r"\bmoment\s+results")
_add("subject:statistics", "statistics-weak", 0.65, r"\bstatistic(?:s|al)\b", r"\bestimation\b", r"\bcorrelation\b", r"\bsampling\b")
_add("subject:mathematics", "math-general", 0.8, r"\btheorems?\b", r"\bproofs?\b", r"\blemmas?\b", r"\bconjectures?\b", r"\b(?:partial\s+)?differential\s+equations?", r"\bequations?\b", r"\bk(?:a|ä)hler\b", r"\bhyperbolicity", r"\bsurfaces?\b", r"\bgeometr(?:y|ic)\b", r"\balgebra(?:ic)?\b", r"\bmatrix\b", r"\belimination\b")
_add("subject:computer-science", "cs-strong", 0.9, r"\bprocessing[- ]in[- ]memory", r"\bhigh\s+bandwidth\s+memory", r"\bcomputer\s+architecture", r"\boperating\s+systems?", r"\bdistributed\s+systems?", r"\bcompilers?\b", r"\bprogramming\s+languages?", r"\bsoftware\s+engineering", r"\bdata\s+structures?", r"\bdatabases?\b", r"\bgpus?\b", r"\bparallel\s+computing", r"\bpython\b", r"\bpycon\b", r"\bcomputational\s+complexity", r"\balgorithms?\b")
_add("subject:computer-science", "cs-weak", 0.65, r"\bcomputing\b", r"\bsoftware\b", r"\bhardware\b", r"\bcomputer\b")
_add("subject:machine-learning", "ml-strong", 1.0, r"\bmachine\s+learning", r"\bdeep\s+learning", r"\bneural\s+networks?", r"\blanguage\s+models?", r"\bllms?\b", r"\btransformers?\s+(?:models?|architectures?|networks?)", r"\bgraph\s+neural", r"\bconvolutional", r"\breinforcement\s+learning", r"\bgenerative\s+(?:models?|adversarial|ai)", r"\bdiffusion\s+models?", r"\bflow[- ]matching", r"\bautoencoders?", r"\bvaes?\b", r"\battention[- ]based", r"\bself[- ]supervised", r"\brepresentation\s+learning", r"\bcontrastive\s+learning", r"\bfoundation\s+models?", r"\bfew[- ]shot", r"\bgraph\s+pooling", r"\bembeddings?\b", r"\bclassifiers?\b")
_add("subject:quantum-computing", "quantum-strong", 1.0, r"\bquantum\s+(?:comput\w*|algorithms?|circuits?|optimi[sz]ation|annealing|machine\s+learning|software|hardware|advantage|supremacy|chemistry|simulation|processors?|devices?)", r"\bqubits?\b", r"\bnisq\b", r"\bvariational\s+quantum", r"\bquantum[- ]classical", r"\bqinterpreter\b")
_add("subject:quantum-computing", "quantum-weak", 0.65, r"\bquantum\b")
_add("subject:medicine", "medicine-strong", 0.9, r"\bclinical\s+(?:trials?|practice|guidelines?|outcomes?|studies|study)", r"\bepidemiolog", r"\bpublic\s+health", r"\bpatients?\b", r"\bhospital", r"\bcohort\b", r"\bmortality\b", r"\bdiagnos(?:is|tic)", r"\binfections?\b", r"\bcancer\b", r"\btumou?rs?\b", r"\binflammat")
_add("subject:medicine", "medicine-weak", 0.65, r"\bdiseases?\b", r"\bclinical\b", r"\bmedical\b", r"\bhealth\b")

_BIOMED = re.compile(r"\b(?:clinical|patients?|disease|hospital|epidemi\w*|cancer|biomedical|medical|cohort|mortality|health\w*|therapy|trial)\b")
_DEV = re.compile(r"\b(?:plugins?|extensible|open[- ]source|software\s+(?:platform|package)|we\s+(?:present|introduce|develop|describe)|toolkit|framework\s+for|pipeline\s+for)\b")
_SEQ_CONTEXT = re.compile(r"\b(?:analysis|pipeline|sequenc\w*|diversity|taxonom\w*|data)\b")
_OT_ACRONYM = re.compile(r"\bot\b")
# Strong mathematical context only: bare "optimal" (as in "optimal dosing") is not evidence.
_OT_CONTEXT = re.compile(r"\b(?:transport\w*|wasserstein|sinkhorn|monge|kantorovich|coupling\w*|earth\s+mover)\b")
_OCCUPATIONAL = re.compile(r"\boccupational\s+therap\w*|\bergotherap\w*")
_KL_ACRONYM = re.compile(r"\bkl\b"); _KL_CONTEXT = re.compile(r"divergence|entropy|variational|kullback")


# Reserved ``auto-classifier:`` / ``auto-primary:`` labels are control metadata written by an older
# heuristic classifier, not scientific evidence.  They are dropped at the classifier INPUT only (a
# derived view); the source paper's tags are never modified.
_RESERVED_TAG = re.compile(r"^\s*auto-(?:classifier|primary):")


def is_control_tag(value: Any) -> bool:
    text = value.get("tag") or value.get("label") or "" if isinstance(value, Mapping) else value
    return bool(_RESERVED_TAG.match(_norm(text)))


def source_keyword_items(paper: Mapping[str, Any]) -> list[Any]:
    """Original source tags (``keywords`` then ``tags``) as a flat list, control tags removed.

    String, mapping (``{"tag": ...}``) and list/tuple/set forms are normalized BEFORE iteration (a
    plain string is one keyword, never a sequence of characters).  The graph pipeline and the local
    evidence resolver both use this, so a stored ``inputHash`` can be reproduced from the raw source
    record.  The source paper itself is never modified.
    """
    items: list[Any] = []
    for key in ("keywords", "tags"):
        value = paper.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (str, Mapping)):
            value = [value]
        if isinstance(value, (list, tuple, set)):
            items.extend(x for x in value if not is_control_tag(x))
    return items


def _parts(paper: Mapping[str, Any]) -> list[tuple[str, str]]:
    result = []
    for key in _PARTS:
        if key == "keywords":
            value = paper["_sourceKeywords"] if "_sourceKeywords" in paper else source_keyword_items(paper)
        else:
            value = paper.get(key)
        if isinstance(value, (list, tuple, set)):
            value = " ".join(str(x.get("tag") or x.get("label") or "") if isinstance(x, Mapping) else str(x) for x in value)
        if key in ("title", "abstract") and value:
            value = normalize_text(value)           # the graph stores title/abstract whitespace-normalized; evidence offsets refer to this form
        if value:
            result.append((key, _norm(str(value)[:MAX_TEXT])))
    return result


def _term_regex(term: str) -> re.Pattern[str] | None:
    text = _norm(term).strip()
    if len(text) < 2: return None
    body = r"\s+".join(re.escape(piece) for piece in text.split())
    return re.compile((r"\b" if text[0].isalnum() else "") + body + (r"\b" if text[-1].isalnum() else ""))


class Candidate:
    __slots__ = ("key", "score", "field", "span", "rule", "hits", "origin", "families", "input_hash")
    def __init__(self, key: str, score: float, field: str, span: tuple[int, int] | None, rule: str, hits: int = 1, origin: str = "rule"):
        self.key, self.score, self.field, self.span, self.rule, self.hits, self.origin = key, score, field, span, rule, hits, origin
        self.families: tuple[str, ...] = ()
        self.input_hash: str | None = None


# --------------------------------------------------------------------------
# Broad-domain Life Science (reviewed policy ontology-label-policy-v2)
# --------------------------------------------------------------------------
# The existing ``subject:life-science`` is assigned ONLY when (a) a high-specificity biological
# compound appears in the title/tags (or, with lower confidence, the abstract), or (b) at least two
# INDEPENDENT biological evidence families appear in the title/tags/abstract/indexed tiers.  Phrases
# that merely look biological (genetic algorithm, cellular automata, neural network, viral marketing,
# computer virus, cell phone, ...) are blanked before matching, generic clinical/statistical words are
# not biology, full text alone is never enough, and a confident narrower subject (Bioinformatics,
# Phage Biology, Mathematics children, ...) or a manual choice always wins over this broad fallback.
_BIO_GUARD = re.compile(r"\b(?:genetic (?:algorithms?|programming|operators?)|gene expression programming|evolutionary (?:algorithms?|computation)|cellular automat\w+|cell ?phones?|mobile cell\w*|(?:artificial |deep |convolutional |recurrent )?neural (?:networks?|nets?)|artificial neurons?|viral (?:marketing|videos?|content|campaigns?)|computer viruses?|virus (?:propagation|spreading)|malware|software ecosystems?|power plants?)\b")
_BIO_COMPOUND = re.compile(r"\b(?:gene expression|gene regulation|protein (?:folding|structure|synthesis)|genome editing|crispr(?:-cas\d*)?|transcriptom\w*|proteom\w*|metabolom\w*|rna[- ]seq(?:uencing)?|rna sequencing|single[- ]cell (?:rna|transcript\w*|sequenc\w*)|mitochondri\w*|synaptic plasticity|photosynthes\w*|neurodegenerat\w*|antibiotic resistance|enzyme kinetics|cell (?:cycle|signaling|signalling|division|differentiation)|stem cells?|population genetics|biodiversity|coral reefs?|microbiome\w*|microbiota|microbial communit\w+|gut flora|cytochrome|embryonic development|embryogenesis)\b")
_BIO_FAMILIES = {
    "molecular": re.compile(r"\b(?:genes?|genetic|genom\w*|dna|rna|mrna|proteins?|enzymes?|peptides?|nucleotides?|chromatin|epigen\w*|transcription(?:al)? factors?|phosphorylat\w*|metabolites?)\b"),
    "cellular": re.compile(r"\b(?:cells?|cellular|organelles?|membranes?|receptors?|neurons?|synap\w*|embryos?|embryonic|tissues?)\b"),
    "organism": re.compile(r"\b(?:bacteri\w+|yeast|zebrafish|arabidopsis|drosophila|mouse|mice|murine|rats?|fungi|fungal|algae|archaea|microbes?|microorganisms?|pathogens?|plankton)\b"),
    "ecology": re.compile(r"\b(?:ecolog\w*|biodiversity|habitats?|coral|speciation|natural selection|phenotyp\w*|genotyp\w*)\b"),
    "physiology": re.compile(r"\b(?:metabol\w*|immun\w*|physiolog\w*|hormon\w*|respirat\w*|pathogenesis)\b"),
}
_BIO_NEEDLES = ("gene", "genet", "genom", "protein", "enzym", "dna", "rna", "cell", "microb", "bacteri", "mitochond", "synap", "photosynth", "crispr", "proteom",
                "transcript", "metabol", "immun", "ecolog", "biodivers", "coral", "neuron", "embryo", "tissue", "membrane", "receptor", "yeast", "zebrafish",
                "arabidopsis", "mouse", "mice", "murine", "fung", "alga", "archaea", "pathogen", "stem", "antibiotic", "cytochrome", "physiolog", "hormon",
                "habitat", "phenotyp", "genotyp", "epigen", "chromatin", "peptide", "nucleotide", "respirat", "neurodegen", "speciation", "selection",
                "drosophila", "rats", "rat ", "plankton", "flora", "peptides")
_BIO_TIERS = ("title", "keywords", "abstract", "indexedText")      # fullText alone is never enough


def _broad_bio_hits(text: str) -> tuple[tuple[int, int] | None, dict[str, tuple[int, int]]]:
    """``(first compound span, {family: first span})`` of one text part after blanking look-alike phrases."""
    if not any(needle in text for needle in _BIO_NEEDLES):
        return None, {}
    if _BIO_GUARD.search(text):
        text = _BIO_GUARD.sub(lambda m: " " * len(m.group(0)), text)     # same length: offsets stay valid
    compound = _BIO_COMPOUND.search(text)
    families = {name: m.span() for name, rx in _BIO_FAMILIES.items() if (m := rx.search(text))}
    return (compound.span() if compound else None), families


def _catalog_fingerprint(entities: Mapping[str, Mapping[str, Any]]) -> str:
    """Semantic fingerprint of everything that influences evidence: facet keys and custom match terms."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(RULE_VERSION.encode())
    for key in sorted(entities):
        row = entities[key]
        label = row.get("label")
        terms = [label.get("en") if isinstance(label, Mapping) else label, *row.get("aliases", []), *row.get("keywords", [])] if row.get("origin") == "custom" else []
        digest.update(repr((key, row.get("origin"), [str(t) for t in terms])).encode())
    return digest.hexdigest()


def collect_evidence(papers: Iterable[Mapping[str, Any]], catalog: Mapping[str, Any], cache: dict | None = None) -> dict[str, dict[str, Candidate]]:
    """Return ``{paperId: {ontologyKey: Candidate}}`` from bounded rule evidence.

    ``cache`` is an optional per-build dict keyed by (catalog fingerprint, text part, text hash), so a
    second pass over an unchanged part (e.g. 30k-character indexed text) with a semantically identical
    catalog is a lookup, not a rescan, even when another part (keywords) changed.  The key covers exactly
    the inputs the rules read, hence results are identical.
    """
    entities = {x["ontologyKey"]: x for x in catalog.get("entities", []) if isinstance(x, Mapping)}
    custom_rules: list[tuple[str, str, float, re.Pattern[str], str | None]] = []
    for key, row in entities.items():
        if row.get("origin") != "custom": continue
        terms = [(row["label"].get("en") if isinstance(row.get("label"), Mapping) else row.get("label"), 1.0, "custom-label")]
        terms += [(a, 0.95, "custom-alias") for a in row.get("aliases", [])] + [(k, 0.95, "custom-keyword") for k in row.get("keywords", [])]
        for term, strength, rule in terms:
            rx = _term_regex(term)
            if rx:
                first_token = _norm(term).split()[0] if _norm(term).split() else None
                custom_rules.append((key, rule, strength, rx, first_token if first_token and len(first_token) >= 2 else None))
    output: dict[str, dict[str, Candidate]] = {}
    fingerprint = _catalog_fingerprint(entities) if cache is not None else ""
    all_rules = _RULES + custom_rules
    def lookup(store: tuple[dict[int, tuple[int, int]], set[int]], text: str, index: int) -> tuple[int, int] | None:
        """First match span of rule ``index`` in one text part, scanned lazily and remembered."""
        hits, scanned = store
        if index not in scanned:
            scanned.add(index)
            _, _, _, rx, needle = all_rules[index]
            if not (_PREFILTER and needle is not None and needle not in text):
                m = rx.search(text)
                if m:
                    hits[index] = m.span()
        return hits.get(index)
    for paper in papers:
        pid = str(paper.get("id", ""))
        parts = _parts(paper)
        stores: dict[str, tuple[dict[int, tuple[int, int]], set[int]]] = {}
        part_hash = {field: hashlib.sha256(text.encode("utf-8")).hexdigest() for field, text in parts}
        for field, text in parts:
            if cache is not None:
                key = (fingerprint, field, part_hash[field])
                stores[field] = cache.setdefault(key, ({}, set()))
            else:
                stores[field] = ({}, set())
        blob = " ".join(text for _, text in parts)
        found: dict[str, Candidate] = {}
        def record(key: str, rule: str, strength: float, field: str, span: tuple[int, int] | None, origin: str = "rule") -> None:
            if key not in entities: return
            score = round(_TIER_WEIGHT.get(field, 0.5) * strength, 6)
            cur = found.get(key)
            if cur is None:
                found[key] = Candidate(key, score, field, span, rule, 1, origin)
            else:
                cur.hits += 1
                if score > cur.score: cur.score, cur.field, cur.span, cur.rule = score, field, span, rule
        rules_hit: set[tuple[str, str]] = set()
        for index, (key, rule, strength, _rx, _needle) in enumerate(all_rules):
            for field, text in parts:
                span = lookup(stores[field], text, index)
                if span is not None:
                    record(key, rule, strength, field, span, "custom" if key in entities and entities[key].get("origin") == "custom" else "rule")
                    rules_hit.add((key, rule)); break
        # multi-hit corroboration: distinct rules for one facet add a bonus
        for key, cand in found.items():
            distinct = len({r for k, r in rules_hit if k == key})
            if distinct > 1: cand.score = round(min(1.0, cand.score + 0.08 * (distinct - 1)), 6)
        # --- contextual rules -------------------------------------------------
        def first(key: str) -> Candidate | None: return found.get(key)
        phage_subject = first("subject:phage-biology") is not None and first("subject:phage-biology").field in {"title", "keywords", "abstract"}
        dev = _DEV.search(blob) is not None
        if "tool:qiime2" not in found and _PLUGIN_WORD.search(blob):
            for field, text in parts:
                match = _Q2_GENERIC.search(text)
                if match:
                    record("tool:qiime2", "qiime2-plugin-context", 1.0, field, match.span()); break
        qiime = first("tool:qiime2")
        if not qiime and _QIIME_FAMILY.search(blob) and dev:
            # QIIME 1/unversioned development evidence still supports the
            # Bioinformatics subject, but never creates the QIIME 2 tool.
            record("subject:bioinformatics", "qiime-family-development", 1.0, next((f for f, t in parts if _DEV.search(t)), "abstract"), None)
        if qiime:
            m = _DEV.search(blob)
            if m and dev:
                qiime.rule = "qiime2-development"
                field = next((f for f, t in parts if _DEV.search(t)), qiime.field)
                record("subject:bioinformatics", "qiime2-development", 1.0, field, None)
            else:
                qiime.rule = "qiime2-use"
        seq_context = _SEQ_CONTEXT.search(blob) is not None
        if not phage_subject:
            for key, strength in (("method:amplicon", 0.85), ("method:metagenomics", 0.85)):
                cand = first(key)
                if cand and cand.score >= 0.4:
                    record("subject:bioinformatics", "sequence-method-evidence", strength, cand.field, cand.span)
            micro = first("application:microbiome")
            if micro and seq_context:
                record("subject:bioinformatics", "microbiome-sequence-analysis", 0.78, micro.field, micro.span)
        # OT / KL acronyms count only with a matching mathematical context.
        if "subject:optimal-transport" not in found and _OT_ACRONYM.search(blob) and not _OCCUPATIONAL.search(blob):
            strong = set(_OT_CONTEXT.findall(blob))
            # In a biomedical document the acronym needs two distinct strong mathematical terms.
            if len(strong) >= (2 if _BIOMED.search(blob) else 1):
                field = next((f for f, t in parts if _OT_ACRONYM.search(t)), "title"); record("subject:optimal-transport", "ot-acronym-context", 0.9, field, None)
        if "subject:kl-divergence" not in found and _KL_ACRONYM.search(blob) and _KL_CONTEXT.search(blob):
            field = next((f for f, t in parts if _KL_ACRONYM.search(t)), "title"); record("subject:kl-divergence", "kl-acronym-context", 0.9, field, None)
        # Broad-domain Life Science (policy ontology-label-policy-v2): compound or >=2 independent families.
        if "subject:life-science" in entities:
            compound_hit: tuple[str, tuple[int, int]] | None = None
            family_spans: dict[str, tuple[str, tuple[int, int]]] = {}
            for field, text in parts:
                if field not in _BIO_TIERS:
                    continue
                if cache is not None:
                    bkey = ("broad", field, part_hash[field])
                    if bkey not in cache:
                        cache[bkey] = _broad_bio_hits(text)
                    compound, families = cache[bkey]
                else:
                    compound, families = _broad_bio_hits(text)
                if compound is not None and compound_hit is None:
                    compound_hit = (field, compound)
                for name, span in families.items():
                    family_spans.setdefault(name, (field, span))
            scored: list[tuple[float, str, tuple[int, int], str, tuple[str, ...]]] = []
            if compound_hit is not None:
                scored.append((_TIER_WEIGHT[compound_hit[0]] * 0.9, compound_hit[0], compound_hit[1], "broad-domain-compound", ()))
            if len(family_spans) >= 2:
                best_field = max((f for f, _ in family_spans.values()), key=lambda f: _TIER_WEIGHT[f])
                span = next(sp for f, sp in family_spans.values() if f == best_field)
                scored.append((min(1.0, _TIER_WEIGHT[best_field] * 0.85 + 0.08 * (len(family_spans) - 1)), best_field, span, "broad-domain-families", tuple(sorted(family_spans))))
            if scored:
                top = max(scored, key=lambda row: row[0])
                score = round(min(1.0, top[0] + (0.08 if len(scored) == 2 else 0.0)), 6)
                # No broad fallback when a confident narrower subject (descendant of Life Science) or any
                # other high-confidence subject exists: the broad root must not absorb them.
                descendants = {key for key, row in entities.items() if row.get("kind") == "subject" and key != "subject:life-science"
                               and "subject:life-science" in _ontology_ancestors(entities, key)}
                # Gap-filling subjects (FALLBACK_SUBJECTS) never block it: they only act where the core abstained.
                blocked = any(c.score >= DIRECT_THRESHOLD for k, c in found.items() if k in descendants and k not in FALLBACK_SUBJECTS) or \
                    any(c.score >= PRIMARY_THRESHOLD for k, c in found.items() if k.startswith("subject:") and k != "subject:life-science" and k not in FALLBACK_SUBJECTS)
                if not blocked and score >= DIRECT_THRESHOLD:
                    cand = Candidate("subject:life-science", score, top[1], top[2], top[3], 1, "rule")
                    cand.families = top[4] or tuple(sorted(family_spans))
                    found["subject:life-science"] = cand
        # Domain-conflict damping: weak/lone mathematical markers in a
        # biomedical document do not make a mathematics subject.
        if _BIOMED.search(blob):
            math_keys = ("subject:analysis", "subject:topology", "subject:probability", "subject:optimization", "subject:information-theory")
            for key in math_keys:
                cand = found.get(key)
                if cand and cand.hits < 2 and entities[key].get("origin") == "builtin":
                    cand.score = round(cand.score * 0.5, 6)
        for cand in found.values():
            # Bind the stored span to the exact normalized input it refers to (hash only; no raw text).
            digest = part_hash.get(cand.field)
            cand.input_hash = f"sha256:{digest}" if digest else None
        output[pid] = found
    return output


def _ontology_ancestors(entities: Mapping[str, Mapping[str, Any]], key: str) -> set[str]:
    """Ancestor ontology keys of ``key`` (catalog ``broaderIds`` are entity ids; map back to keys)."""
    by_id = {row.get("id"): k for k, row in entities.items()}
    seen: set[str] = set(); stack = [key]
    while stack:
        for parent_id in entities.get(stack.pop(), {}).get("broaderIds", []):
            parent = by_id.get(parent_id)
            if parent and parent not in seen:
                seen.add(parent); stack.append(parent)
    return seen


def resolve_evidence_span(paper: Mapping[str, Any], ref: Mapping[str, Any]) -> str | None:
    """LOCAL resolver for a stored evidence span (never used for logging or publication).

    Re-normalizes the named field of the *source* paper with the recorded normalization version and
    returns ``text[start:end]`` only if the SHA-256 of that normalized input equals the stored
    ``inputHash``; an edited field, a different normalization or a malformed ref yields ``None``.
    """
    if not isinstance(ref, Mapping) or ref.get("normalization") != NORMALIZATION_VERSION or not isinstance(ref.get("inputHash"), str):
        return None
    span = ref.get("spanRange")
    if not (isinstance(span, (list, tuple)) and len(span) == 2 and all(isinstance(x, int) for x in span)):
        return None
    text = dict(_parts(paper)).get(str(ref.get("field")))
    if text is None or f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}" != ref["inputHash"]:
        return None
    start, end = span
    return text[start:end] if 0 <= start <= end <= len(text) else None


def _subject_projection(catalog: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
    by_id = {x["id"]: x for x in catalog.get("entities", [])}
    by_key = {x["ontologyKey"]: x["id"] for x in catalog.get("entities", [])}
    legacy = {x["legacyFieldId"]: x["id"] for x in catalog.get("entities", []) if x.get("legacyFieldId")}
    return by_id, by_key, legacy


def _ref(server: str, epoch: str, pid: str, eid: str, cand: Candidate | None, *, origin: str, tier: str | None = None, field: str = "user") -> dict[str, Any]:
    ref = {"sourceId": server, "sourceEpoch": epoch, "paperId": pid, "facetId": eid, "origin": origin, "ruleVersion": RULE_VERSION}
    if cand is not None:
        ref.update({"field": cand.field, "evidenceTier": _TIER_NAME.get(cand.field, "unknown"), "ruleId": cand.rule, "score": min(1.0, max(0.0, cand.score)),
                    "evidenceSpan": None})
        if cand.span: ref["spanRange"] = [int(cand.span[0]), int(cand.span[1])]
        if cand.input_hash: ref["inputHash"] = cand.input_hash; ref["normalization"] = NORMALIZATION_VERSION
        if cand.families: ref["families"] = list(cand.families)
    else:
        ref.update({"field": field, "evidenceTier": tier or "user", "score": 1.0, "evidenceSpan": None})
    return ref


def _subject_primary(pool: list[dict[str, Any]], depth: Mapping[str, int], anc: Mapping[str, set[str]], related: Any,
                     *, pick_top: bool = False) -> tuple[str | None, bool]:
    """Primary subject of one pool: the clear winner; on a near tie between unrelated subjects, their deepest common
    ancestor (both claims are true of it); with no common ancestor, the top-ranked one only when ``pick_top``."""
    if not pool:
        return None, False
    rank = lambda r: r["score"] + 0.03 * depth[r["facetId"]]
    ranked = sorted(pool, key=lambda r: (-rank(r), r["facetId"]))
    top = ranked[0]
    if top["score"] < PRIMARY_THRESHOLD:
        return None, False
    rivals = [r for r in ranked[1:] if not related(top["facetId"], r["facetId"]) and rank(top) - rank(r) < AMBIGUITY_MARGIN]
    if not rivals:
        return top["facetId"], False
    common = set(anc[top["facetId"]]) | {top["facetId"]}
    for rival in rivals:
        common &= set(anc[rival["facetId"]]) | {rival["facetId"]}
    if common:
        return sorted(common, key=lambda e: (-depth[e], e))[0], True
    return (top["facetId"], True) if pick_top else (None, True)


def resolve_facets(papers: Iterable[Mapping[str, Any]], catalog: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Candidate]], *,
                   user_subjects: Mapping[str, Iterable[str]] | None = None, display_fields: Mapping[str, str] | None = None,
                   overlay_entities: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Turn evidence + user overrides into direct memberships and per-kind primaries."""
    by_id, by_key, legacy = _subject_projection(catalog)
    server, epoch = str(catalog.get("sourceId", "")), str(catalog.get("sourceEpoch", ""))
    depth = {eid: len(_ancestors(by_id, eid)) for eid in by_id}
    anc = {eid: _ancestors(by_id, eid) for eid in by_id}
    def related(a: str, b: str) -> bool: return a == b or a in anc[b] or b in anc[a]
    user_subjects = user_subjects or {}; display_fields = display_fields or {}; overlay_entities = overlay_entities or {}
    state = catalog.get("inputState")
    rows: list[dict[str, Any]] = []
    for paper in papers:
        pid = str(paper.get("id", ""))
        accepted: dict[str, dict[str, Any]] = {}
        # 1. generated evidence
        cands = evidence.get(pid, {})
        for key, cand in cands.items():
            eid = by_key.get(key)
            if not eid or cand.score < DIRECT_THRESHOLD or key in FALLBACK_SUBJECTS: continue   # gap-filling evidence: see gap_fill_subjects
            origin = "custom-rule" if cand.origin == "custom" else "rule"
            accepted[eid] = {"facetId": eid, "ontologyKey": key, "kind": by_id[eid]["kind"], "scoreKind": "evidence", "score": round(min(1.0, cand.score), 6),
                             "origin": origin, "evidenceRef": _ref(server, epoch, pid, eid, cand, origin=origin)}
        # 2. user-confirmed subjects (legacy memberships) and facet assignments
        user_directs: list[str] = []
        for fid in user_subjects.get(pid, []):
            eid = legacy.get(str(fid))
            if eid:
                accepted[eid] = {"facetId": eid, "ontologyKey": by_id[eid]["ontologyKey"], "kind": "subject", "scoreKind": "user", "score": 1.0,
                                 "origin": "user", "evidenceRef": _ref(server, epoch, pid, eid, None, origin="user", tier="user", field="legacy-field")}
                user_directs.append(eid)
        entity = overlay_entities.get(pid, {}) if isinstance(overlay_entities, Mapping) else {}
        def resolve_ref(ref: Any) -> str | None:
            ref = str(ref.get("facetId") if isinstance(ref, Mapping) else ref)
            return ref if ref in by_id else by_key.get(ref)
        for raw in (entity.get("facetAssignments") or []) if isinstance(entity, Mapping) else []:
            eid = resolve_ref(raw)
            if eid:
                accepted[eid] = {"facetId": eid, "ontologyKey": by_id[eid]["ontologyKey"], "kind": by_id[eid]["kind"], "scoreKind": "user", "score": 1.0,
                                 "origin": "user", "evidenceRef": _ref(server, epoch, pid, eid, None, origin="user", tier="user", field="overlay")}
                if by_id[eid]["kind"] == "subject": user_directs.append(eid)
        primary_override: dict[str, str] = {}
        for kind, ref in ((entity.get("primaryFacetOverrides") or {}) if isinstance(entity, Mapping) else {}).items():
            eid = resolve_ref(ref)
            if eid and by_id[eid]["kind"] == kind:
                primary_override[kind] = eid
                # A user's primary choice is itself an accepted direct membership.
                accepted.setdefault(eid, {"facetId": eid, "ontologyKey": by_id[eid]["ontologyKey"], "kind": kind, "scoreKind": "user", "score": 1.0,
                                          "origin": "user", "evidenceRef": _ref(server, epoch, pid, eid, None, origin="user", tier="user", field="overlay")})
        # 3. primary per kind (always within accepted)
        primary: dict[str, str | None] = {}
        status = "abstain"
        for kind in FACET_KINDS:
            pool = [r for r in accepted.values() if r["kind"] == kind]
            chosen: str | None = None
            if kind in primary_override and primary_override[kind] in accepted:
                chosen = primary_override[kind]
            elif kind == "subject" and user_directs:
                want = legacy.get(str(display_fields.get(pid, "")))
                uniq = [u for u in dict.fromkeys(user_directs) if u in accepted]
                chosen = want if want in uniq else sorted(uniq, key=lambda e: (-depth[e], e))[0]
            elif pool:
                ranked = sorted(pool, key=lambda r: (-(r["score"] + 0.03 * depth[r["facetId"]]), r["facetId"]))
                top = ranked[0]
                if kind == "subject":
                    rivals = [r for r in ranked[1:] if not related(top["facetId"], r["facetId"])
                              and (top["score"] + 0.03 * depth[top["facetId"]]) - (r["score"] + 0.03 * depth[r["facetId"]]) < AMBIGUITY_MARGIN]
                    if top["score"] >= PRIMARY_THRESHOLD and not rivals: chosen = top["facetId"]
                    elif top["score"] >= PRIMARY_THRESHOLD: status = "ambiguous"
                else:
                    chosen = top["facetId"]
            primary[kind] = chosen
        if primary.get("subject"):
            status = "user" if (user_directs or "subject" in primary_override) else "classified"
        elif status != "ambiguous" and accepted:
            status = "abstain"
        directs = sorted(accepted.values(), key=lambda r: (r["kind"], r["facetId"]))
        rows.append({"paperId": pid, "directMemberships": directs, "primaryByFacet": {k: v for k, v in primary.items()}, "status": status})
    return rows


def gap_fill_subjects(papers: Iterable[Mapping[str, Any]], catalog: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Candidate]],
                      resolved: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, float, str | None]]:
    """Display subject for papers the core classifier left without a primary (taxonomy-v4).

    Only FALLBACK_SUBJECTS evidence at or above PRIMARY_THRESHOLD counts.  A near tie between unrelated gap subjects
    resolves to their deepest common ancestor, else to the top-ranked one (deterministic).  The facet record itself is
    untouched (its subject primary stays None); the caller turns the result into a ``gap-fill`` display membership.
    Returns {paperId: (entityId, score, ruleId)}."""
    by_id, by_key, _ = _subject_projection(catalog)
    depth = {eid: len(_ancestors(by_id, eid)) for eid in by_id}
    anc = {eid: _ancestors(by_id, eid) for eid in by_id}
    def related(a: str, b: str) -> bool: return a == b or a in anc[b] or b in anc[a]
    out: dict[str, tuple[str, float, str | None]] = {}
    for paper, row in zip(papers, resolved):
        pid = str(paper.get("id", ""))
        if (row.get("primaryByFacet") or {}).get("subject"):
            continue
        pool = [{"facetId": by_key[key], "score": min(1.0, cand.score), "rule": cand.rule}
                for key, cand in (evidence.get(pid) or {}).items() if key in FALLBACK_SUBJECTS and key in by_key and cand.score >= PRIMARY_THRESHOLD]
        chosen, _ = _subject_primary(pool, depth, anc, related, pick_top=True)
        if chosen:
            best = max((r for r in pool if r["facetId"] == chosen or chosen in anc[r["facetId"]]), key=lambda r: (r["score"], r["facetId"]))
            out[pid] = (chosen, round(best["score"], 6), best["rule"])
    return out


def classify_papers(papers: Iterable[Mapping[str, Any]], catalog: Mapping[str, Any], **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    papers = list(papers)
    rows = resolve_facets(papers, catalog, collect_evidence(papers, catalog), **kwargs)
    return rows, {"version": RULE_VERSION, "ruleVersion": RULE_VERSION, "classifier": "bounded-evidence-rules",
                  "thresholds": {"direct": DIRECT_THRESHOLD, "primary": PRIMARY_THRESHOLD, "ambiguityMargin": AMBIGUITY_MARGIN}}


def check_facet_invariants(paper_facets: list[Mapping[str, Any]], catalog: Mapping[str, Any]) -> list[str]:
    """Return violations of the primary-in-direct and same-kind invariants."""
    by_id = {x["id"]: x for x in catalog.get("entities", [])}
    errors = []
    for row in paper_facets:
        direct = {m["facetId"] for m in row.get("directMemberships", [])}
        for kind, eid in (row.get("primaryByFacet") or {}).items():
            if eid is None: continue
            if eid not in by_id or by_id[eid]["kind"] != kind: errors.append(f"{row['paperId']}: primary {kind} is not a same-kind entity")
            elif eid not in direct: errors.append(f"{row['paperId']}: primary {kind} not in accepted direct memberships")
    return errors


_MSC_LINE = re.compile(r"^\s*(?:msc(?:\s*20\d{2})?|ams(?:\s*msc)?|mathematics\s+subject\s+classification)\s*[:=]\s*(.+)$", re.IGNORECASE)
_MSC_CODE = re.compile(r"\b(\d{2}[A-Za-z]\d{2})\b")


def msc_codes_from_extra(extra: Any, limit: int = 12) -> list[str]:
    """Parse bounded MSC codes from structured ``Extra`` lines only.

    Only lines whose key is a known MSC key contribute; free text in other
    Extra lines is never mined, so Extra cannot leak into concepts.
    """
    codes: list[str] = []
    for line in str(extra or "")[:20000].splitlines()[:200]:
        match = _MSC_LINE.match(line)
        if match:
            for code in _MSC_CODE.findall(match.group(1)):
                code = code.upper()
                if code not in codes:
                    codes.append(code)
    return codes[:limit]


# --------------------------------------------------------------------------
# Field statistics
# --------------------------------------------------------------------------
_UNKNOWN_LABELS = {"unknown", "unclassified", "미분류"}


def field_stats(papers: Iterable[Mapping[str, Any]], fields: Mapping[str, Mapping[str, Any]], memberships: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Unique-paper accepted-direct / descendant-inclusive / primary / unknown sets."""
    papers = [p for p in papers if p.get("id")]
    active = {str(p["id"]) for p in papers}
    def is_unknown(fid: str) -> bool:
        lbl = fields[fid].get("label", {}); text = lbl.get("en") if isinstance(lbl, Mapping) else lbl
        return _norm(text) in _UNKNOWN_LABELS or fid.rsplit(":", 1)[-1] == "unknown"
    direct: dict[str, set[str]] = defaultdict(set)
    for row in memberships:
        pid, fid = str(row.get("paperId", "")), str(row.get("fieldId", ""))
        if pid in active and fid in fields: direct[fid].add(pid)
    parents = {fid: [str(x) for x in raw.get("parentIds", []) if str(x) in fields] for fid, raw in fields.items()}
    def descendants_of() -> dict[str, set[str]]:
        children: dict[str, set[str]] = defaultdict(set)
        for child, ps in parents.items():
            for p in ps: children[p].add(child)
        out: dict[str, set[str]] = {}
        for fid in fields:
            seen: set[str] = {fid}; stack = [fid]
            while stack:
                for c in children.get(stack.pop(), ()):
                    if c not in seen: seen.add(c); stack.append(c)
            out[fid] = seen
        return out
    desc = descendants_of()
    primary_of = {str(p["id"]): str(p.get("displayFieldId")) for p in papers if p.get("displayFieldId") in fields and not is_unknown(str(p.get("displayFieldId")))}
    rows = []
    for fid in sorted(fields):
        inclusive: set[str] = set()
        for d in desc[fid]: inclusive |= direct.get(d, set())
        known_primary = {pid for pid, f in primary_of.items() if f == fid}
        rows.append({"fieldId": fid, "direct": len(direct.get(fid, set())), "inclusive": len(inclusive), "primary": len(known_primary)})
    unknown = sorted(active - set(primary_of))
    return {"version": STATS_VERSION, "paperCount": len(active), "rows": rows, "unknown": len(unknown), "unknownPaperIds": unknown,
            "knownPrimary": len(primary_of),
            "invariants": {"knownPrimaryPlusUnknownEqualsPaperCount": len(primary_of) + len(unknown) == len(active),
                           "primaryLeDirectLeInclusive": all(r["primary"] <= r["direct"] <= r["inclusive"] for r in rows)}}


# --------------------------------------------------------------------------
# Concept quality
# --------------------------------------------------------------------------
PROTECTED_PHRASES = frozenset({
    "disease burden", "fiber bundles", "fibre bundles", "vector bundles", "optimal transport", "kl divergence",
    "kullback-leibler divergence", "relative entropy", "phage therapy", "shotgun metagenomics", "16s rrna", "marker gene",
    "microbial community", "machine learning", "graph neural network",
})
GENERIC_DENY = frozenset({"been", "has", "have", "had", "were", "was", "being", "also", "however", "thus", "burden", "bundles", "bundle",
                          "can", "may", "one", "two", "using", "used", "based", "show", "shown", "et", "al"})
_CODE = re.compile(r"^\d{2}[a-z]\d{2}$")
_ALNUM_TECH = re.compile(r"^(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]{3,}$")
_GENERATED_META = re.compile(r"^auto-(?:classifier|primary):")
_CANONICAL = {  # label -> (canonical label, context regex over paper text)
    "kl": ("kl divergence", re.compile(r"divergence|relative entropy|kullback")),
    "kullback-leibler": ("kl divergence", None), "kullback-leibler divergence": ("kl divergence", None), "kullback leibler divergence": ("kl divergence", None),
    "ot": ("optimal transport", re.compile(r"optimal transport|earth mover|wasserstein|sinkhorn|monge-kantorovich")),
    "optimal-transport": ("optimal transport", None),
}


# --------------------------------------------------------------------------
# Library-derived lexical model (automatic function-word / weak-phrase filter)
# --------------------------------------------------------------------------
# No word list: every decision comes from the statistics of the library's own
# titles and abstracts (duplicate titles counted once).
#   * residual IDF (Church & Gale): a content word is bursty, so it sits in
#     fewer documents than a Poisson spread of its total count predicts;
#     function words follow the Poisson prediction (residual near 0).
#   * title affinity: content words head titles, function words rarely do.
#   * phrase cohesion: normalised PMI of an adjacent pair.
LEXICAL_MIN_DOCS = 50            # below this the statistics are too thin; the model is not built
LEXICAL_UBIQUITY = 0.4           # a token in >= 40% of documents discriminates nothing
LEXICAL_FLAT_RIDF = 0.3          # residual IDF below this = evenly spread
LEXICAL_BURSTY_RIDF = 0.5        # residual IDF from this = bursty (term evidence)
LEXICAL_TITLE_ALPHA = 0.05       # one-sided binomial level for "appears in titles less than chance"
LEXICAL_COMMON_SHARE = 0.025     # enough documents for "no term evidence" to count against a token
LEXICAL_COHESION = 0.5           # NPMI from which an adjacent pair is a cohesive phrase
LEXICAL_PHRASE_SCORE = 2
_LEX_TOKEN = re.compile(r"[\w\u0080-\uffff][\w\u0080-\uffff'’-]{1,}", re.UNICODE)
_HAS_LETTER = re.compile(r"[^\W\d_]")


def _lex_tokens(value: Any) -> list[str]:
    return [_norm(t) for t in _LEX_TOKEN.findall(normalize_text(value))[:400]]


def lexical_model(papers: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Token / adjacent-pair statistics of the library, or ``None`` when it has fewer than ``LEXICAL_MIN_DOCS`` distinct texts."""
    cf: dict[Any, int] = defaultdict(int); df: dict[Any, int] = defaultdict(int); tdf: dict[Any, int] = defaultdict(int)
    seen: set[str] = set(); docs = 0; total = 0
    for paper in papers:
        title = _lex_tokens(paper.get("title")); body = _lex_tokens(paper.get("abstract"))
        key = " ".join(title)
        if not (title or body) or (key and key in seen): continue
        seen.add(key); docs += 1; total += len(title) + len(body)
        title_units = title + list(zip(title, title[1:])); units = title_units + body + list(zip(body, body[1:]))
        for unit in units: cf[unit] += 1
        for unit in set(units): df[unit] += 1
        for unit in set(title_units): tdf[unit] += 1
    if docs < LEXICAL_MIN_DOCS: return None
    token_df = sum(n for unit, n in df.items() if isinstance(unit, str))
    baseline = sum(n for unit, n in tdf.items() if isinstance(unit, str)) / token_df if token_df else 0.0
    return {"cf": cf, "df": df, "tdf": tdf, "docs": docs, "tokens": total, "titleBaseline": baseline, "memo": {}}


def _lex_token_class(model: Mapping[str, Any], token: str) -> str:
    """``function`` | ``term`` | ``common`` (well observed, no term evidence) | ``unknown``."""
    memo = model["memo"]
    if token in memo: return memo[token]
    d = model["df"].get(token, 0); n = model["docs"]
    if not d: result = "unknown"
    else:
        share = d / n; tdf = model["tdf"].get(token, 0); base = model["titleBaseline"]
        ridf = math.log2(n * (1 - math.exp(-model["cf"][token] / n)) / d)
        title_tail = sum(math.comb(d, i) * base ** i * (1 - base) ** (d - i) for i in range(tdf + 1))   # P(X <= tdf)
        if share >= LEXICAL_UBIQUITY or (ridf < LEXICAL_FLAT_RIDF and title_tail < LEXICAL_TITLE_ALPHA): result = "function"
        elif ridf >= LEXICAL_BURSTY_RIDF or tdf / d >= base: result = "term"
        else: result = "common" if share >= LEXICAL_COMMON_SHARE else "unknown"
    memo[token] = result
    return result


def lexical_verdict(model: Mapping[str, Any] | None, label: str) -> str | None:
    """Verdict for a text-derived concept label of one or two tokens: ``function-word``, ``low-termhood`` or ``None`` (keep)."""
    if model is None: return None
    tokens = label.split()
    if not tokens or len(tokens) > 2: return None
    if not all(_HAS_LETTER.search(t) for t in tokens): return "low-termhood"        # number fragments ("000 bacterial")
    classes = [_lex_token_class(model, t) for t in tokens]
    if len(tokens) == 1:
        return "function-word" if classes[0] == "function" else None if classes[0] == "term" else "low-termhood"
    if "function" in classes: return "function-word"
    pair = (tokens[0], tokens[1]); count = model["cf"].get(pair, 0); total = model["tokens"]
    cohesive = False
    if 0 < count < total:
        pmi = math.log2(count * total / (model["cf"][tokens[0]] * model["cf"][tokens[1]]))
        cohesive = pmi / -math.log2(count / total) >= LEXICAL_COHESION
    score = classes.count("term") - classes.count("common") + (model["tdf"].get(pair, 0) >= 1) + cohesive
    return None if score >= LEXICAL_PHRASE_SCORE else "low-termhood"


def _concept_text(paper: Mapping[str, Any]) -> str:
    return _norm(" ".join(str(paper.get(k) or "") for k in ("title", "abstract")) + " " + " ".join(str(x) for x in paper.get("keywords", []) or [] if not is_control_tag(x)))


def concept_quality(concepts: Mapping[str, Mapping[str, Any]], papers: list[dict[str, Any]], overlays: Mapping[str, Any] | None = None,
                    *, server: str = "", epoch: str = "") -> dict[str, Any]:
    """Phrase/context/DF-termhood quality pass with a resolvable ledger.

    Precedence: user pin/alias/exclusion/redirect protection -> curated phrase
    and technical-code protection -> generated-metadata/stopword deny -> DF and
    termhood score -> library-derived lexical verdict (text-derived labels only;
    explicit keywords and tags are never judged).  Suppressed IDs remain resolvable through the ledger but
    are not active graph nodes.
    """
    overlays = overlays if isinstance(overlays, Mapping) else {}
    pins = overlays.get("pins", {}) if isinstance(overlays.get("pins"), Mapping) else {}
    aliases = overlays.get("conceptAliases", {}) if isinstance(overlays.get("conceptAliases"), Mapping) else {}
    entities = overlays.get("entities", {}) if isinstance(overlays.get("entities"), Mapping) else {}
    redirect_targets = {str(v) for v in (overlays.get("redirectTargets") or [])}
    user_protected = {str(k) for k, v in pins.items() if v} | {str(k) for k in aliases} | redirect_targets
    user_excluded: set[str] = set()
    for cid, raw in entities.items():
        if not isinstance(raw, Mapping) or raw.get("entityType") != "concept": continue
        quality = raw.get("quality") if isinstance(raw.get("quality"), Mapping) else {}
        if raw.get("excluded") is True or quality.get("status") == "suppressed": user_excluded.add(str(cid))
        if raw.get("protected") is True or quality.get("protected") is True or quality.get("status") == "protected": user_protected.add(str(cid))
    n_papers = len(papers)
    mutable = {cid: dict(row) for cid, row in concepts.items()}
    label_of = {cid: _norm((row.get("label") or {}).get("en", "") if isinstance(row.get("label"), Mapping) else row.get("label", "")).strip() for cid, row in mutable.items()}
    by_label = {label: cid for cid, label in label_of.items() if label}
    df: dict[str, int] = defaultdict(int)
    for p in papers:
        for cid in set(p.get("conceptIds", [])): df[cid] += 1
    origins: dict[str, set[str]] = defaultdict(set)
    for p in papers:
        for meta in p.get("keywordMetadata", []) or []:
            cid = by_label.get(_norm(meta.get("label")).strip())
            if cid: origins[cid].add(str(meta.get("origin")))
    texts = {p["id"]: _concept_text(p) for p in papers}
    lexicon = lexical_model(papers)
    # canonical targets (per paper, context-dependent)
    def canonical_for(cid: str, text: str) -> str | None:
        label = label_of.get(cid, "")
        spec = _CANONICAL.get(label)
        if not spec: return None
        canon, context = spec
        if context is not None and (not context.search(text) or _OCCUPATIONAL.search(text)): return None
        target = by_label.get(canon) or by_label.get(canon.replace(" ", "-"))
        if target is None and server:
            target = f"concept:{server}:{epoch}:{_slug(canon)}"
            mutable.setdefault(target, {"id": target, "label": {"en": canon.title() if canon != "kl divergence" else "KL Divergence", "ko": canon}, "aliases": [], "source": "extracted"})
            by_label[canon] = target; label_of[target] = canon
        return target if target and target != cid else None
    merged_into: dict[str, set[str]] = defaultdict(set)
    holders: dict[str, int] = defaultdict(int)          # papers originally holding each concept
    remapped: dict[str, int] = defaultdict(int)         # papers whose context remapped it
    for p in papers:
        for cid in set(p.get("conceptIds", [])): holders[cid] += 1
    for p in papers:
        out = []
        for cid in p.get("conceptIds", []):
            if cid in user_protected or cid in user_excluded or str(mutable.get(cid, {}).get("source")) == "user":
                out.append(cid); continue
            target = canonical_for(cid, texts.get(p["id"], ""))
            if target: merged_into[cid].add(target); remapped[cid] += 1; out.append(target)
            else: out.append(cid)
        p["conceptIds"] = list(dict.fromkeys(out))
    df = defaultdict(int)
    for p in papers:
        for cid in set(p["conceptIds"]): df[cid] += 1
    ledger: dict[str, Any] = {"version": QUALITY_VERSION, "ruleVersion": QUALITY_VERSION, "entries": [], "raw": [], "active": [], "suppressed": [], "protected": [], "user": []}
    for cid in sorted(mutable):
        row = mutable[cid]; label = label_of[cid]; source = str(row.get("source", "extracted"))
        tokens = label.split()
        origin_set = origins.get(cid, set())
        explicit = bool(origin_set & {"keyword", "zotero-tag"})
        status, reason, protected_by = "active", "retained", None
        technical = bool(_CODE.match(label) or _ALNUM_TECH.match(label.replace(" ", "")) and len(tokens) == 1)
        score = None
        if cid in user_excluded:
            status, reason, protected_by = "suppressed", "user-excluded", "user"
        elif cid in user_protected or source == "user":
            status, reason, protected_by = "protected", ("user-pin-or-alias" if cid in user_protected else "user-owned"), "user"
        elif label in PROTECTED_PHRASES:
            status, reason, protected_by = "protected", "protected-phrase", "rule"
        elif technical or "extra-msc" in origin_set:
            status, reason, protected_by = "protected", ("msc-code" if "extra-msc" in origin_set else "technical-code"), "rule"
        elif _GENERATED_META.match(label):
            status, reason = "suppressed", "generated-metadata-tag"
        elif len(tokens) == 1 and label in GENERIC_DENY:
            status, reason = "suppressed", "context-free-boilerplate"
        else:
            d = df.get(cid, 0)
            generic = all(t in GENERIC_DENY for t in tokens)
            score = 0.2 * min(d, 4) + (0.25 if len(tokens) >= 2 else 0) + (0.15 if any(len(t) >= 6 for t in tokens) else 0) - (0.4 if generic else 0)
            if n_papers >= 25 and d / n_papers > 0.4: score -= 0.3          # boilerplate dispersion
            score = round(score, 4)
            text_only = bool(origin_set) and not explicit
            needs_df = n_papers >= MIN_LIBRARY_FOR_DF and text_only
            if d == 0:
                # Not an active graph node: merged away by a canonical concept or no longer attached to any paper.
                status, reason = "suppressed", ("merged-into-canonical" if merged_into.get(cid) and remapped[cid] == holders[cid] else "no-current-membership")
            elif needs_df and (d < 2 or score < TERMHOOD_THRESHOLD):
                status, reason = "suppressed", "low-termhood"
            elif text_only:
                # Automatic filter: the library's own statistics decide (no curated word list).
                verdict = lexical_verdict(lexicon, label)
                if verdict: status, reason = "suppressed", verdict
        entry = {"id": cid, "conceptId": cid, "label": row.get("label"), "status": status, "reason": reason, "ruleVersion": QUALITY_VERSION,
                 "protectedBy": protected_by or "rule", "evidenceRefs": []}
        if score is not None: entry["termhood"] = score
        if merged_into.get(cid):
            # Only a merge that applied to EVERY paper holding the concept (single target) is a
            # global-safe redirect.  A shared acronym merged only where its context matched
            # (OT, KL) is reported as a per-paper context merge and must never be followed globally.
            if remapped[cid] == holders[cid] and len(merged_into[cid]) == 1:
                entry["mergedInto"] = sorted(merged_into[cid]); entry["mergeScope"] = "global"
            else:
                entry["contextMergedInto"] = sorted(merged_into[cid]); entry["mergeScope"] = "per-paper"; entry["contextMergedPaperCount"] = remapped[cid]
        ledger["entries"].append(entry); ledger["raw"].append(cid)
        if status == "suppressed": ledger["suppressed"].append(cid)
        else: ledger["active"].append(cid)
        if status == "protected": ledger["protected"].append(cid)
        if source == "user" or protected_by == "user": ledger["user"].append(cid)
    suppressed = set(ledger["suppressed"])
    suppressed_labels = {label_of[c] for c in suppressed}
    for p in papers:
        p["conceptIds"] = [cid for cid in p["conceptIds"] if cid not in suppressed]
        if isinstance(p.get("keywords"), list):
            p["keywords"] = [k for k in p["keywords"] if _norm(k).strip() not in suppressed_labels]
    for entry in ledger["entries"]:
        mutable[entry["id"]]["qualityStatus"] = entry["status"]
    live = {cid for p in papers for cid in p["conceptIds"]}
    ledger["counts"] = {"raw": len(ledger["raw"]), "active": len(ledger["active"]), "suppressed": len(ledger["suppressed"]),
                        "protected": len(ledger["protected"]), "user": len(ledger["user"]), "activeInGraph": len(live)}
    return {"concepts": mutable, "ledger": ledger}

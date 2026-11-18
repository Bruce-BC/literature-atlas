"""Construction of the source-scoped Literature Atlas graph snapshot."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .embeddings import EmbeddingProvider, normalize_text


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "into",
    "is", "of", "on", "or", "the", "to", "with", "this", "that", "using",
    "study", "analysis", "method", "results", "paper", "연구", "분석", "및", "의",
}
_SLUG = re.compile(r"[^a-z0-9가-힣]+", re.IGNORECASE)
_ID_RE = re.compile(r"^[A-Za-z0-9:_./@+%-]+$")
_HYPHENS = re.compile(r"[‐‑‒–—−-]")
KEYWORD_TOPK_PER_PAPER = 12
KEYWORD_MIN_COOCCURRENCE = 2
OPENALEX_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_PROVIDER_POOL: dict[tuple[str, str, bool], EmbeddingProvider] = {}


def _norm(value: Any) -> str:
    value = normalize_text(value).casefold()
    return _HYPHENS.sub("-", value)


def _slug(value: Any) -> str:
    text = _norm(value).replace("&", " and ")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    base = _SLUG.sub("-", ascii_text).strip("-")
    # Keep simple IDs readable, but make punctuation/Unicode IDs collision
    # resistant and schema-safe. Labels retain their original Unicode text.
    if base and re.fullmatch(r"[a-z0-9]+", text):
        return base[:180]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return (base[:150] + "-" if base else "x-") + digest


def _source_scope(source: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    out = copy.deepcopy(dict(source))
    server = str(out.get("serverId") or "").strip()
    source_id = str(out.get("id") or "").strip()
    epoch = str(out.get("epoch") or "").strip()
    if not server or source_id != server or not epoch:
        raise ValueError("source must provide matching id/serverId and persistent epoch")
    out["serverId"] = server
    out["id"] = server
    out["epoch"] = epoch
    out.setdefault("alias", "zotero-local")
    out.setdefault("zoteroApi", "3")
    out.setdefault("schema", 1)
    return out, server, epoch


def _valid_id(value: Any) -> bool:
    value = str(value or "")
    return bool(value) and len(value) <= 512 and bool(_ID_RE.fullmatch(value))


def _legacy_id_map(previous: Mapping[str, Any] | None, server: str, epoch: str,
                   *contexts: Any) -> dict[str, str]:
    """Map legacy active-epoch Unicode IDs from every active graph input."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    labels: dict[str, Any] = {}

    def register(kind: str, old_id: Any, label: Any = None) -> None:
        old = str(old_id or "")
        prefix = f"{kind}:{server}:{epoch}:"
        if not old.startswith(prefix):
            return
        if label is not None and old not in labels:
            labels[old] = label
        if _valid_id(old):
            used.add(old)

    def scan(value: Any) -> None:
        if isinstance(value, Mapping):
            for kind, key in (("field", "fields"), ("concept", "concepts")):
                for row in value.get(key, []) or []:
                    if isinstance(row, Mapping):
                        label = row.get("label", {})
                        label = label.get("en") or label.get("ko") if isinstance(label, Mapping) else label
                        register(kind, row.get("id"), label)
                        if kind == "field":
                            for parent in row.get("parentIds", []) or []:
                                register("field", parent)
            for key in ("fieldIds", "conceptIds", "parentIds"):
                for item in value.get(key, []) or []:
                    register("field" if key != "conceptIds" else "concept", item)
            for key in ("paperFields", "paperConcepts", "conceptAliases", "fieldAliases", "pins"):
                nested = value.get(key)
                if isinstance(nested, Mapping):
                    for entity, payload in nested.items():
                        register("concept" if key in {"paperConcepts", "conceptAliases"} else "field", entity)
                        if isinstance(payload, list):
                            for item in payload:
                                register("concept" if key == "paperConcepts" else "field", item)
            entities = value.get("entities")
            if isinstance(entities, Mapping):
                for entity, row in entities.items():
                    kind = str(row.get("entityType") or "") if isinstance(row, Mapping) else ""
                    register(kind, entity)
                    scan(row)
            for row in value.get("memberships", []) or []:
                if isinstance(row, Mapping):
                    register("field", row.get("fieldId"))
            for row in value.values():
                if isinstance(row, (Mapping, list)):
                    scan(row)
        elif isinstance(value, list):
            for row in value:
                scan(row)

    scan(previous or {})
    for context in contexts:
        scan(context)
    for old, label in list(labels.items()):
        if _valid_id(old):
            continue
        kind = "field" if old.startswith("field:") else "concept"
        candidate = _id(kind, server, epoch, label or old)
        if candidate in used:
            candidate = f"{candidate}-{hashlib.sha256(old.encode()).hexdigest()[:12]}"
        mapping[old] = candidate
        used.add(candidate)
    return mapping


def _replace_id(value: Any, migrations: Mapping[str, str]) -> Any:
    return migrations.get(str(value), value)


def _migrate_rows(rows: Any, migrations: Mapping[str, str]) -> Any:
    if not isinstance(rows, list):
        return rows
    result = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            result.append(raw)
            continue
        row = copy.deepcopy(dict(raw))
        if row.get("id") in migrations:
            old = str(row["id"])
            row["id"] = migrations[old]
            row["legacyId"] = old
        for key in ("parentIds", "fieldIds", "conceptIds"):
            if isinstance(row.get(key), list):
                row[key] = [_replace_id(item, migrations) for item in row[key]]
        result.append(row)
    return result


def _migrate_taxonomy(taxonomy: Any, migrations: Mapping[str, str]) -> Any:
    if not isinstance(taxonomy, Mapping) or not migrations:
        return taxonomy
    out = copy.deepcopy(dict(taxonomy))
    out["fields"] = _migrate_rows(out.get("fields", []), migrations)
    out["concepts"] = _migrate_rows(out.get("concepts", []), migrations)
    return out


def _migrate_overlay(overlays: Any, migrations: Mapping[str, str]) -> Any:
    if not isinstance(overlays, Mapping) or not migrations:
        return overlays
    out = copy.deepcopy(dict(overlays))
    for key in ("paperFields", "paperConcepts", "conceptAliases", "fieldAliases"):
        value = out.get(key)
        if isinstance(value, Mapping):
            converted = {}
            for entity, payload in value.items():
                new_entity = _replace_id(entity, migrations)
                if key in {"conceptAliases", "fieldAliases"}:
                    converted[new_entity] = payload
                elif isinstance(payload, list):
                    converted[new_entity] = [_replace_id(item, migrations) for item in payload]
                else:
                    converted[new_entity] = payload
            out[key] = converted
    if isinstance(out.get("memberships"), list):
        out["memberships"] = [dict(row, fieldId=_replace_id(row.get("fieldId"), migrations)) if isinstance(row, Mapping) else row for row in out["memberships"]]
    if isinstance(out.get("entities"), Mapping):
        entities = {}
        for entity, raw in out["entities"].items():
            new_entity = _replace_id(entity, migrations)
            row = copy.deepcopy(raw)
            if isinstance(row, Mapping):
                row = dict(row)
                for key in ("fieldIds", "conceptIds"):
                    if isinstance(row.get(key), list):
                        row[key] = [_replace_id(item, migrations) for item in row[key]]
                if isinstance(row.get("memberships"), list):
                    row["memberships"] = [dict(item, fieldId=_replace_id(item.get("fieldId"), migrations)) if isinstance(item, Mapping) else item for item in row["memberships"]]
            entities[new_entity] = row
        out["entities"] = entities
    if isinstance(out.get("pins"), Mapping):
        out["pins"] = {_replace_id(key, migrations): value for key, value in out["pins"].items()}
    return out


def _id(prefix: str, server: str, epoch: str, value: Any) -> str:
    return f"{prefix}:{server}:{epoch}:{_slug(value)}"


def _paper_id(paper: Mapping[str, Any], server: str) -> str:
    current = str(paper.get("id") or "")
    if current.startswith(f"zotero:{server}:"):
        return current
    library = paper.get("libraryID", paper.get("libraryId", 0))
    key = str(paper.get("key") or paper.get("itemKey") or paper.get("id") or "unknown")
    return f"zotero:{server}:{library}:{key}"


def _key_for_paper(paper: Mapping[str, Any], paper_id: str) -> str:
    return str(paper.get("key") or paper.get("itemKey") or paper_id.rsplit(":", 1)[-1])


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _edge_id(server: str, epoch: str, source: str, target: str, kind: str, directed: bool) -> str:
    if not directed:
        source, target = _canonical_pair(source, target)
    # Keep canonical endpoints visible in the ID.  This makes source/epoch
    # fencing and duplicate inspection possible without consulting a table.
    return f"edge:{server}:{epoch}:{source}:{target}:{kind}"


def _label(value: Any, fallback: str = "") -> dict[str, str]:
    if isinstance(value, Mapping):
        return {"en": str(value.get("en", fallback)), "ko": str(value.get("ko", value.get("en", fallback)))}
    return {"en": str(value or fallback), "ko": str(value or fallback)}


def _iter_tags(paper: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Return ``(label, origin, evidenceTier)`` without flattening provenance."""
    values: list[tuple[str, str, str]] = []
    for key, origin, tier in (("keywords", "keyword", "metadata"), ("tags", "zotero-tag", "metadata")):
        raw = paper.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        for item in raw:
            if isinstance(item, Mapping):
                label = item.get("tag") or item.get("label") or item.get("name")
                item_origin = str(item.get("origin") or origin)
                item_tier = str(item.get("evidenceTier") or tier)
            else:
                label, item_origin, item_tier = item, origin, tier
            label = normalize_text(label)
            if label:
                values.append((label, item_origin, item_tier if item_tier in {"metadata", "indexed", "fulltext", "ocr", "unknown"} else tier))
    has_explicit = bool(paper.get("keywords") or paper.get("tags"))
    # Zotero records often have no explicit keyword array.  Add a small,
    # bounded lexical signal from title/abstract while keeping its origin and
    # evidence tier visible.  This is intentionally conservative rather than a
    # generic stopword-heavy bag of every abstract token.
    if has_explicit:
        # Keep indexed text provenance even when Zotero tags already exist.
        indexed_text = normalize_text(paper.get("indexedText") or paper.get("fullText"))
        if indexed_text:
            for token in re.findall(r"[\w\u0080-\uffff][\w\u0080-\uffff'’-]{2,}", indexed_text, flags=re.UNICODE)[:12]:
                if _norm(token) not in _STOPWORDS:
                    values.append((token, "indexed-text", "indexed"))
        return values
    for field, origin, tier in (("title", "title", "metadata"), ("abstract", "abstract", "metadata"), ("indexedText", "indexed-text", "indexed")):
        text = normalize_text(paper.get(field))
        seen = 0
        for token in re.findall(r"[\w\u0080-\uffff][\w\u0080-\uffff'’-]{2,}", text, flags=re.UNICODE):
            if _norm(token) in _STOPWORDS or len(_norm(token)) < 3:
                continue
            values.append((token, origin, tier))
            seen += 1
            if seen >= 12:
                break
    return values


def _manual_values(overlays: Any, key: str) -> Any:
    if not isinstance(overlays, Mapping):
        return None
    value = overlays.get(key)
    if value is not None:
        return value
    manual = overlays.get("manual")
    return manual.get(key) if isinstance(manual, Mapping) else None


def _verified_reference(ref: Any) -> tuple[str | None, bool, str, str]:
    """Extract a citation only from explicitly verified, evidenced input."""
    if not isinstance(ref, Mapping):
        return (str(ref) if ref is not None else None, False, "unknown", "unverified-input")
    target = ref.get("paperId") or ref.get("id") or ref.get("key") or ref.get("target")
    verified = ref.get("verified") is True
    tier = str(ref.get("evidenceTier") or "unknown")
    raw_provider = str(ref.get("provider") or "").strip().lower()
    raw_source = str(ref.get("source") or "").strip().lower()
    provider = raw_provider or raw_source
    if provider == "pdf":
        provider = "pdf-reference"
    allowed_tiers = {"metadata", "indexed", "fulltext", "ocr"}
    pdf_evidence = raw_source in {"pdf", "pdf-reference"} and tier in {"indexed", "fulltext", "ocr"}
    openalex_evidence = (raw_provider == "openalex" and isinstance(ref.get("retrievalProvenance"), Mapping)
                         and str(ref["retrievalProvenance"].get("method") or "") == "referenced_works")
    metadata_evidence = (raw_provider in {"metadata-reference", "zotero-reference", "indexed-reference"}
                         and bool(ref.get("evidenceSpan") or ref.get("referenceText")))
    has_evidence = tier in allowed_tiers and (pdf_evidence or openalex_evidence or metadata_evidence)
    return (str(target) if target is not None else None, verified and has_evidence, tier, provider or "verified-reference")


def _builtin_taxonomy(server: str, epoch: str) -> list[dict[str, Any]]:
    """Small explainable bilingual field hierarchy for an empty user taxonomy."""
    return [
        {"id": "mathematics", "label": {"en": "Mathematics", "ko": "수학"}, "parentIds": [], "aliases": []},
        {"id": "analysis", "label": {"en": "Analysis", "ko": "해석학"}, "parentIds": ["mathematics"], "aliases": ["mathematical analysis"]},
        {"id": "topology", "label": {"en": "Topology", "ko": "위상수학"}, "parentIds": ["mathematics"], "aliases": ["graph topology"]},
        {"id": "probability", "label": {"en": "Probability", "ko": "확률론"}, "parentIds": ["mathematics"], "aliases": ["stochastic"]},
        {"id": "optimization", "label": {"en": "Optimization", "ko": "최적화"}, "parentIds": ["mathematics"], "aliases": ["optimal transport"]},
        {"id": "optimal-transport", "label": {"en": "Optimal Transport", "ko": "최적수송"}, "parentIds": ["optimization"], "aliases": ["OT"]},
        {"id": "information", "label": {"en": "Information Theory", "ko": "정보이론"}, "parentIds": ["mathematics"], "aliases": ["entropy", "information theory"]},
        {"id": "kl-divergence", "label": {"en": "KL Divergence", "ko": "KL 발산"}, "parentIds": ["information"], "aliases": ["Kullback–Leibler divergence", "relative entropy"]},
        {"id": "life-science", "label": {"en": "Life Science", "ko": "생명과학"}, "parentIds": [], "aliases": ["biology", "biological"]},
        {"id": "bioinformatics", "label": {"en": "Bioinformatics", "ko": "생물정보학"}, "parentIds": ["life-science"], "aliases": ["genomics", "genome"]},
        {"id": "phage", "label": {"en": "Phage Biology", "ko": "파지 생물학"}, "parentIds": ["life-science"], "aliases": ["bacteriophage"]},
        {"id": "unknown", "label": {"en": "Unclassified", "ko": "미분류"}, "parentIds": [], "aliases": []},
    ]


_BUILTIN_RULES = {
    "analysis": ("analysis", "calculus", "theorem", "measure theory", "real analysis"),
    "topology": ("topology", "topological", "graph topology"),
    "probability": ("probability", "stochastic", "bayesian", "markov", "random variable"),
    "optimization": ("optimization", "optimisation", "optimal transport", "convex", "optimal"),
    "optimal-transport": ("optimal transport", "optimal-transport"),
    "information": ("information theory", "entropy", "mutual information", "kl divergence", "relative entropy"),
    "kl-divergence": ("kl divergence", "kullback leibler divergence", "relative entropy"),
    "life-science": ("biology", "biological", "microbiology", "cell", "protein", "ecology", "life science"),
    "bioinformatics": ("bioinformatics", "genome", "genomics", "metagenom", "sequence alignment"),
    "phage": ("phage", "bacteriophage", "virus"),
}


class GraphEngine:
    """Build a complete, source/epoch-fenced graph without mutating callers."""

    def __init__(self, cache_dir: Path, provider: str = "e5", allow_download: bool = True):
        self.cache_dir = Path(cache_dir)
        self.provider_name = provider
        self.allow_download = allow_download
        self._provider_instance: EmbeddingProvider | None = None

    def _taxonomy(self, taxonomy: Any, server: str, epoch: str) -> tuple[dict[str, dict], dict[str, dict]]:
        fields: dict[str, dict] = {}
        concepts: dict[str, dict] = {}
        if isinstance(taxonomy, Mapping):
            raw_fields = taxonomy.get("fields", [])
            raw_concepts = taxonomy.get("concepts", [])
        else:
            raw_fields, raw_concepts = taxonomy or [], []
        for raw in raw_fields or []:
            if not isinstance(raw, Mapping):
                continue
            old_id = str(raw.get("id") or raw.get("label", {}).get("en") or "unknown")
            if old_id.startswith("field:") and not old_id.startswith(f"field:{server}:{epoch}:"):
                raise ValueError("taxonomy field is outside active source epoch")
            fid = old_id if old_id.startswith(f"field:{server}:{epoch}:") else _id("field", server, epoch, old_id)
            fields[fid] = {
                "id": fid, "label": _label(raw.get("label"), old_id),
                "parentIds": [], "aliases": [str(x) for x in raw.get("aliases", [])],
                "source": str(raw.get("source") or "user"),
            }
        # Add parent links only after all IDs are known, then remove cycles and
        # unknown parents.  A malformed user taxonomy cannot poison the graph.
        old_to_new = {}
        for raw in raw_fields or []:
            if not isinstance(raw, Mapping):
                continue
            old_id = str(raw.get("id") or raw.get("label", {}).get("en") or "unknown")
            old_to_new[old_id] = old_id if old_id.startswith(f"field:{server}:{epoch}:") else _id("field", server, epoch, old_id)
        for raw in raw_fields or []:
            if not isinstance(raw, Mapping):
                continue
            old_id = str(raw.get("id") or raw.get("label", {}).get("en") or "unknown")
            fid = old_id if old_id.startswith(f"field:{server}:{epoch}:") else old_to_new.get(old_id)
            if fid not in fields:
                continue
            parents = raw.get("parentIds") or raw.get("parents") or []
            for parent in parents:
                parent_text = str(parent)
                if parent_text.startswith("field:") and not parent_text.startswith(f"field:{server}:{epoch}:"):
                    raise ValueError("taxonomy parent is outside active source epoch")
            fields[fid]["parentIds"] = [p if str(p) in fields else old_to_new.get(str(p)) for p in parents]
            fields[fid]["parentIds"] = [p for p in fields[fid]["parentIds"] if p]
        def detect_cycle(fid: str, active: set[str], done: set[str]) -> None:
            if fid in active:
                raise ValueError("taxonomy parent cycle")
            if fid in done:
                return
            active.add(fid)
            for parent in fields.get(fid, {}).get("parentIds", []):
                detect_cycle(parent, active, done)
            active.remove(fid)
            done.add(fid)
        done_fields: set[str] = set()
        for fid in fields:
            detect_cycle(fid, set(), done_fields)
        def reaches(start: str, target: str, seen: set[str]) -> bool:
            if start == target:
                return True
            if start in seen:
                return False
            seen.add(start)
            return any(reaches(p, target, seen) for p in fields.get(start, {}).get("parentIds", []))
        for fid, field in fields.items():
            field["parentIds"] = [p for p in field["parentIds"] if p != fid and not reaches(p, fid, set())]
        for raw in raw_concepts or []:
            if not isinstance(raw, Mapping):
                continue
            old_id = str(raw.get("id") or raw.get("label", {}).get("en") or "unknown")
            if old_id.startswith("concept:") and not old_id.startswith(f"concept:{server}:{epoch}:"):
                raise ValueError("taxonomy concept is outside active source epoch")
            cid = old_id if old_id.startswith(f"concept:{server}:{epoch}:") else _id("concept", server, epoch, old_id)
            concepts[cid] = {
                "id": cid, "label": _label(raw.get("label"), old_id),
                "aliases": [str(x) for x in raw.get("aliases", [])],
                "source": str(raw.get("source") or "user"),
            }
        return fields, concepts

    @staticmethod
    def _doi(value: Any) -> str:
        return re.sub(r"^https?://(doi\.org/)?", "", normalize_text(value)).strip().lower()

    def _openalex_enrichment(self, papers: list[dict[str, Any]], key_to_id: Mapping[str, str],
                             settings: Mapping[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], str, bool]:
        """Resolve opt-in DOI references to currently known library records.

        OpenAlex returns work IDs for ``referenced_works``.  We fetch bounded
        referenced-work metadata, match only returned DOIs already present in
        the current library, and persist redacted-by-key cache files locally.
        """
        enabled = bool(settings.get("openAlexEnabled", settings.get("openalexEnabled", settings.get("openAlex", False))))
        if not enabled:
            return {}, "disabled", False
        timeout = max(1.0, min(float(settings.get("openAlexTimeout", 4.0)), 20.0))
        max_refs = max(1, min(int(settings.get("openAlexMaxReferences", 50)), 200))
        doi_to_paper = {self._doi(p.get("doi")): p["id"] for p in papers if self._doi(p.get("doi"))}
        if not doi_to_paper:
            return {}, "unknown", False
        cache_root = self.cache_dir / "openalex"
        cache_root.mkdir(parents=True, exist_ok=True)
        failures = 0
        attempted = 0
        refs_by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def fetch(identifier: str) -> tuple[dict[str, Any] | None, bool]:
            cache_path = cache_root / (hashlib.sha256(identifier.encode()).hexdigest() + ".json")
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and isinstance(cached.get("body"), dict):
                    return cached["body"], True
            except (OSError, ValueError, TypeError):
                pass
            url = "https://api.openalex.org/works/" + urllib.parse.quote(identifier, safe="")
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "LiteratureAtlas/0.1"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    if int(getattr(response, "status", 200)) != 200:
                        return None, False
                    payload = response.read(OPENALEX_MAX_RESPONSE_BYTES + 1)
                    if len(payload) > OPENALEX_MAX_RESPONSE_BYTES:
                        return None, False
                    body = json.loads(payload.decode("utf-8"))
                    if not isinstance(body, dict):
                        return None, False
                try:
                    cache_path.write_text(json.dumps({"body": body}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                except OSError:
                    pass
                return body, True
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError):
                return None, False

        for paper in papers:
            doi = self._doi(paper.get("doi"))
            if not doi:
                continue
            attempted += 1
            work, ok = fetch("https://doi.org/" + doi)
            if not ok or not work:
                failures += 1
                continue
            for work_id in (work.get("referenced_works") or [])[:max_refs]:
                if not isinstance(work_id, str):
                    continue
                ref, ref_ok = fetch(work_id.rsplit("/", 1)[-1])
                if not ref_ok or not ref:
                    failures += 1
                    continue
                target_doi = self._doi(ref.get("doi"))
                target = doi_to_paper.get(target_doi)
                if target and target != paper["id"]:
                    refs_by_paper[paper["id"]].append({
                        "paperId": target, "verified": True, "provider": "openalex",
                        "evidenceTier": "metadata", "retrievalProvenance": {
                            "provider": "openalex", "method": "referenced_works",
                            "source": "doi", "workId": work_id,
                        },
                    })
        if failures:
            return refs_by_paper, "failed", True
        return refs_by_paper, ("ready" if attempted else "unknown"), False

    def build(self, papers: Iterable[Mapping[str, Any]], *, source: Mapping[str, Any], taxonomy: Any = None,
              overlays: Any = None, settings: Mapping[str, Any] | None = None,
              attachments: Iterable[Mapping[str, Any]] | None = None,
              previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_out, server, epoch = _source_scope(source)
        raw_taxonomy = copy.deepcopy(taxonomy)
        raw_overlay = copy.deepcopy(overlays or {})
        source_papers = [copy.deepcopy(dict(p)) for p in papers]
        migrations = _legacy_id_map(previous, server, epoch, raw_taxonomy, raw_overlay,
                                     source, source_papers)
        settings = copy.deepcopy(dict(settings or {}))
        taxonomy = _migrate_taxonomy(raw_taxonomy, migrations)
        overlay_copy = _migrate_overlay(raw_overlay, migrations)
        # Canonicalize backend entity patches into the graph's source-scoped
        # working shape.  Invalid/cross-source entity IDs are ignored.
        entities = overlay_copy.get("entities", {}) if isinstance(overlay_copy, Mapping) else {}
        if isinstance(entities, Mapping):
            paper_fields = dict(overlay_copy.get("paperFields", {})) if isinstance(overlay_copy.get("paperFields", {}), Mapping) else {}
            memberships = list(overlay_copy.get("memberships", [])) if isinstance(overlay_copy.get("memberships", []), list) else []
            concept_aliases = dict(overlay_copy.get("conceptAliases", {})) if isinstance(overlay_copy.get("conceptAliases", {}), Mapping) else {}
            pins = dict(overlay_copy.get("pins", {})) if isinstance(overlay_copy.get("pins", {}), Mapping) else {}
            for entity_id, raw_entity in entities.items():
                if not isinstance(raw_entity, Mapping):
                    continue
                entity_id = str(entity_id)
                active_entity = (entity_id.startswith(f"zotero:{server}:") or entity_id.startswith(f"field:{server}:{epoch}:") or entity_id.startswith(f"concept:{server}:{epoch}:"))
                if entity_id.startswith(("zotero:", "field:", "concept:")) and not active_entity:
                    raise ValueError("overlay entity is outside active source epoch")
                if not active_entity:
                    continue
                kind = str(raw_entity.get("entityType") or "")
                if kind == "paper":
                    if "fieldIds" in raw_entity:
                        paper_fields[entity_id] = copy.deepcopy(raw_entity.get("fieldIds") or [])
                    for membership in raw_entity.get("memberships", []) or []:
                        if isinstance(membership, Mapping):
                            row = copy.deepcopy(dict(membership)); row["paperId"] = entity_id; memberships.append(row)
                    if "pinned" in raw_entity:
                        pins[entity_id] = bool(raw_entity["pinned"])
                elif kind == "concept":
                    if "aliases" in raw_entity:
                        concept_aliases[entity_id] = copy.deepcopy(raw_entity.get("aliases") or [])
                    if "pinned" in raw_entity:
                        pins[entity_id] = bool(raw_entity["pinned"])
                elif kind == "field":
                    if "aliases" in raw_entity:
                        overlay_copy.setdefault("fieldAliases", {})[entity_id] = copy.deepcopy(raw_entity.get("aliases") or [])
                    if "pinned" in raw_entity:
                        pins[entity_id] = bool(raw_entity["pinned"])
            overlay_copy["paperFields"] = paper_fields
            overlay_copy["memberships"] = memberships
            overlay_copy["conceptAliases"] = concept_aliases
            overlay_copy["pins"] = pins
        for paper in source_papers:
            for key in ("fieldIds", "conceptIds"):
                if isinstance(paper.get(key), list):
                    paper[key] = [_replace_id(item, migrations) for item in paper[key]]
            if isinstance(paper.get("memberships"), list):
                paper["memberships"] = [dict(item, fieldId=_replace_id(item.get("fieldId"), migrations)) if isinstance(item, Mapping) else item for item in paper["memberships"]]
        fields, concepts = self._taxonomy(taxonomy, server, epoch)
        builtin_fields = not fields
        if builtin_fields:
            fields, _ = self._taxonomy({"fields": _builtin_taxonomy(server, epoch)}, server, epoch)
            for field in fields.values():
                field["source"] = "model"
        paper_rows: list[dict[str, Any]] = []
        by_old_field: dict[str, str] = {}
        for fid in fields:
            by_old_field[fid.rsplit(":", 1)[-1]] = fid
        raw_top_concepts = []
        if isinstance(taxonomy, Mapping):
            raw_top_concepts = taxonomy.get("concepts", []) or []
        for raw in raw_top_concepts:
            if isinstance(raw, Mapping) and raw.get("id"):
                by_old_field[str(raw["id"])] = str(raw["id"])

        # Preserve source-supplied fields/concepts and memberships from a prior
        # snapshot when callers pass a snapshot-shaped paper collection.
        supplied_fields = (source.get("fields") if isinstance(source, Mapping) else None) or []
        for raw in supplied_fields:
            if isinstance(raw, Mapping) and raw.get("id"):
                fid = str(raw["id"])
                if fid.startswith("field:") and not fid.startswith(f"field:{server}:{epoch}:"):
                    raise ValueError("supplied field is outside active source epoch")
                if fid.startswith(f"field:{server}:{epoch}:"):
                    fields.setdefault(fid, copy.deepcopy(dict(raw)))
        supplied_concepts = (source.get("concepts") if isinstance(source, Mapping) else None) or []
        for raw in supplied_concepts:
            if isinstance(raw, Mapping) and raw.get("id"):
                cid = str(raw["id"])
                if cid.startswith("concept:") and not cid.startswith(f"concept:{server}:{epoch}:"):
                    raise ValueError("supplied concept is outside active source epoch")
                if cid.startswith(f"concept:{server}:{epoch}:"):
                    concepts.setdefault(cid, copy.deepcopy(dict(raw)))
        if isinstance(previous, Mapping):
            for raw in _migrate_rows(previous.get("fields", []) or [], migrations) or []:
                if isinstance(raw, Mapping) and str(raw.get("id", "")).startswith(f"field:{server}:{epoch}:"):
                    fields.setdefault(str(raw["id"]), copy.deepcopy(dict(raw)))
            for raw in _migrate_rows(previous.get("concepts", []) or [], migrations) or []:
                if isinstance(raw, Mapping) and str(raw.get("id", "")).startswith(f"concept:{server}:{epoch}:"):
                    concepts.setdefault(str(raw["id"]), copy.deepcopy(dict(raw)))
        for field_id, aliases in (overlay_copy.get("fieldAliases", {}) or {}).items() if isinstance(overlay_copy.get("fieldAliases", {}), Mapping) else []:
            field_id = str(field_id)
            if field_id.startswith("field:") and field_id not in fields:
                raise ValueError("field alias is outside active source epoch")
            if field_id in fields:
                fields[field_id]["aliases"] = [str(alias) for alias in aliases or []]

        explicit_memberships = []
        for p in source_papers:
            explicit_memberships.extend(p.pop("memberships", []) if isinstance(p.get("memberships"), list) else [])
        # Prior derived memberships are recomputed.  User memberships must be
        # supplied by the source record or source-scoped overlays so removing
        # an overlay/field cannot be undone by a stale previous snapshot.
        existing_memberships = []
        manual_memberships = _manual_values(overlay_copy, "memberships") or _manual_values(overlay_copy, "fieldMemberships") or []
        all_membership_inputs = explicit_memberships + existing_memberships + [copy.deepcopy(x) for x in manual_memberships if isinstance(x, Mapping)]
        paper_ids: set[str] = set()
        key_to_id: dict[str, str] = {}
        for p in source_papers:
            pid = _paper_id(p, server)
            paper_ids.add(pid)
            key_to_id[_key_for_paper(p, pid)] = pid
            p["id"] = pid
            p["key"] = _key_for_paper(p, pid)
            p["libraryID"] = int(p.get("libraryID", p.get("libraryId", 0)) or 0)
            p["title"] = normalize_text(p.get("title", ""))
            p["abstract"] = normalize_text(p.get("abstract")) or None
            p["authors"] = [normalize_text(x) for x in (p.get("authors") or [])]
            p.setdefault("year", None)
            p.setdefault("doi", None)
            p.setdefault("attachmentIds", [])
            p.setdefault("textStatus", "missing" if not p["abstract"] else "readable")
            p["_inputFieldIds"] = list(p.get("fieldIds") or [])
            p["_inputConceptIds"] = list(p.get("conceptIds") or [])
            p["fieldIds"] = []
            p["conceptIds"] = []
            paper_rows.append(p)

        # Empty field declarations get explainable rule-based memberships so a
        # real library is useful immediately.  Existing/user memberships stay
        # authoritative and are never replaced by these candidates.
        if builtin_fields:
            for p in paper_rows:
                haystack = _norm(" ".join([p.get("title", ""), p.get("abstract") or ""] + [str(x) for x in p.get("keywords", [])]))
                candidates = []
                for field_key, terms in _BUILTIN_RULES.items():
                    matched = next((term for term in terms if _norm(term) in haystack), None)
                    if matched:
                        candidates.append((field_key, matched))
                p["_ruleFields"] = candidates

        memberships: list[dict[str, Any]] = []
        membership_by_key: dict[tuple[str, str], dict] = {}
        manual_fields = _manual_values(overlay_copy, "paperFields") or {}
        def ensure_field(field_id: str, label: str = "") -> str:
            if field_id.startswith("field:") and not field_id.startswith(f"field:{server}:{epoch}:"):
                raise ValueError("field membership is outside active source epoch")
            if field_id.startswith(f"field:{server}:{epoch}:"):
                fid = field_id
            else:
                fid = by_old_field.get(field_id) or _id("field", server, epoch, field_id)
            fields.setdefault(fid, {"id": fid, "label": _label(label, field_id), "parentIds": [], "aliases": [], "source": "user"})
            return fid
        def add_membership(pid: str, fid: str, raw: Mapping[str, Any] | None = None, *, default_method: str = "user") -> None:
            raw = raw or {}
            method = str(raw.get("method") or default_method)
            if method not in {"user", "model", "openalex"}:
                method = default_method
            key = (pid, fid)
            record = {
                "id": str(raw.get("id") or f"membership:{server}:{epoch}:{hashlib.sha256((pid + fid).encode()).hexdigest()[:24]}"),
                "paperId": pid, "fieldId": fid, "method": method,
                "score": raw.get("score", 1 if method == "user" else None),
                "weight": float(raw.get("weight", 1) or 0),
                "evidenceSpan": raw.get("evidenceSpan"),
                "modelVersion": raw.get("modelVersion"),
            }
            if raw.get("scoreKind"):
                record["scoreKind"] = str(raw["scoreKind"])
            if isinstance(raw.get("provenance"), Mapping):
                record["provenance"] = copy.deepcopy(dict(raw["provenance"]))
            old = membership_by_key.get(key)
            if old is None or (method == "user" and old.get("method") != "user"):
                membership_by_key[key] = record
        # User paper field declarations are the primary source. Preserve every
        # membership; the display field is chosen separately below.
        for p in paper_rows:
            for field_id in p.get("_inputFieldIds", []) if isinstance(p.get("_inputFieldIds"), list) else []:
                add_membership(p["id"], ensure_field(str(field_id)), default_method="user")
            manual_override = isinstance(manual_fields, Mapping) and (p["id"] in manual_fields or p.get("key") in manual_fields)
            if builtin_fields and not p.get("_inputFieldIds") and not manual_override:
                candidates = p.get("_ruleFields") or [("unknown", None)]
                for field_key, evidence in candidates:
                    add_membership(p["id"], ensure_field(field_key), {
                        "method": "model", "score": 0.75 if evidence else 0.1,
                        "weight": 1, "evidenceSpan": evidence, "modelVersion": "term-rules-v1",
                        "scoreKind": "heuristic",
                        "provenance": {"provider": "term-rules", "method": "normalized-term-match", "label": "rule-based"},
                    }, default_method="model")
        for raw in all_membership_inputs:
            pid = str(raw.get("paperId") or "")
            if pid.startswith("zotero:") and pid not in paper_ids and not pid.startswith(f"zotero:{server}:"):
                raise ValueError("membership paper is outside active source")
            if pid not in paper_ids:
                pid = key_to_id.get(pid, pid)
            if pid not in paper_ids:
                continue
            fid = ensure_field(str(raw.get("fieldId") or raw.get("field") or "unknown"))
            add_membership(pid, fid, raw, default_method="model")
        if isinstance(manual_fields, Mapping):
            for key, values in manual_fields.items():
                if str(key).startswith("zotero:") and str(key) not in paper_ids and not str(key).startswith(f"zotero:{server}:"):
                    raise ValueError("paper field overlay is outside active source")
                pid = key if key in paper_ids else key_to_id.get(str(key))
                if not pid:
                    continue
                if isinstance(values, str):
                    values = [values]
                if not values:
                    add_membership(pid, ensure_field("unknown", "Unknown"), {"method": "user", "score": 1}, default_method="user")
                for field_id in values or []:
                    add_membership(pid, ensure_field(str(field_id)), {"method": "user", "score": 1}, default_method="user")
        for p in paper_rows:
            pid = p["id"]
            related = [m for m in membership_by_key.values() if m["paperId"] == pid]
            related.sort(key=lambda m: (m["method"] != "user", -float(m["weight"]), m["fieldId"]))
            p["fieldIds"] = [m["fieldId"] for m in related]
            requested = p.get("displayFieldId")
            requested = ensure_field(str(requested)) if requested else None
            if requested and requested not in p["fieldIds"]:
                requested = None
            p["displayFieldId"] = requested or (related[0]["fieldId"] if related else _id("field", server, epoch, "unknown"))
            if not related:
                ensure_field(p["displayFieldId"], "Unknown")
            memberships.extend(related)

        # Keyword and concept extraction is intentionally exact/alias based.
        # It avoids broad fuzzy merges (e.g. KL divergence with Klebsiella).
        alias_to_concept: dict[str, str] = {}
        for cid, concept in concepts.items():
            for value in [concept.get("label", {}).get("en"), concept.get("label", {}).get("ko"), *concept.get("aliases", [])]:
                if value:
                    alias_to_concept[_norm(value)] = cid
        manual_aliases = _manual_values(overlay_copy, "conceptAliases") or {}
        if isinstance(manual_aliases, Mapping):
            for cid, aliases in manual_aliases.items():
                canonical = str(cid)
                if canonical.startswith("concept:") and not canonical.startswith(f"concept:{server}:{epoch}:"):
                    raise ValueError("concept alias is outside active source epoch")
                if not canonical.startswith(f"concept:{server}:{epoch}:"):
                    canonical = _id("concept", server, epoch, canonical)
                concepts.setdefault(canonical, {"id": canonical, "label": _label(cid), "aliases": [], "source": "user"})
                for alias in aliases if isinstance(aliases, (list, tuple, set)) else [aliases]:
                    alias_to_concept[_norm(alias)] = canonical
                    concepts[canonical]["aliases"].append(str(alias))
        elif isinstance(manual_aliases, list):
            for row in manual_aliases:
                if not isinstance(row, Mapping) or not row.get("conceptId") or not row.get("alias"):
                    continue
                canonical = str(row["conceptId"])
                if canonical.startswith("concept:") and not canonical.startswith(f"concept:{server}:{epoch}:"):
                    raise ValueError("concept alias is outside active source epoch")
                if not canonical.startswith(f"concept:{server}:{epoch}:"):
                    canonical = _id("concept", server, epoch, canonical)
                concepts.setdefault(canonical, {"id": canonical, "label": _label(canonical), "aliases": [], "source": "user"})
                alias_to_concept[_norm(row["alias"])] = canonical
                concepts[canonical]["aliases"].append(str(row["alias"]))
        keyword_meta: dict[str, list[dict[str, str]]] = {}
        manual_paper_concepts = _manual_values(overlay_copy, "paperConcepts") or _manual_values(overlay_copy, "conceptMemberships") or {}
        for p in paper_rows:
            tags = _iter_tags(p)
            seen_kw: set[str] = set()
            extracted_keywords: list[str] = []
            for label, origin, tier in tags:
                key = _norm(label)
                if not key or key in _STOPWORDS or len(key) < 2 or key in seen_kw:
                    continue
                seen_kw.add(key)
                extracted_keywords.append(label)
                keyword_meta.setdefault(p["id"], []).append({"label": label, "origin": origin, "evidenceTier": tier})
                cid = alias_to_concept.get(key)
                if cid is None:
                    cid = _id("concept", server, epoch, key)
                    concepts[cid] = {"id": cid, "label": _label(label), "aliases": [], "source": "extracted"}
                    alias_to_concept[key] = cid
                if cid not in p["conceptIds"]:
                    p["conceptIds"].append(cid)
            if isinstance(manual_paper_concepts, Mapping):
                for overlay_paper_id in manual_paper_concepts:
                    if str(overlay_paper_id).startswith("zotero:") and not str(overlay_paper_id).startswith(f"zotero:{server}:"):
                        raise ValueError("paper concept overlay is outside active source")
                raw_manual = manual_paper_concepts.get(p["id"], manual_paper_concepts.get(p.get("key"), []))
                if isinstance(raw_manual, str):
                    raw_manual = [raw_manual]
                for raw_cid in raw_manual or []:
                    cid = str(raw_cid)
                    if cid.startswith("concept:") and not cid.startswith(f"concept:{server}:{epoch}:"):
                        raise ValueError("paper concept is outside active source epoch")
                    if not cid.startswith(f"concept:{server}:{epoch}:"):
                        cid = _id("concept", server, epoch, cid)
                    concepts.setdefault(cid, {"id": cid, "label": _label(raw_cid), "aliases": [], "source": "user"})
                    if cid not in p["conceptIds"]:
                        p["conceptIds"].append(cid)
            # Preserve explicit concept IDs and user concepts supplied by source.
            for raw_cid in p.get("_inputConceptIds", []) if isinstance(p.get("_inputConceptIds", []), list) else []:
                cid = str(raw_cid)
                if cid.startswith("concept:") and not cid.startswith(f"concept:{server}:{epoch}:"):
                    raise ValueError("paper concept is outside active source epoch")
                if cid not in concepts:
                    cid = _id("concept", server, epoch, cid)
                    concepts[cid] = {"id": cid, "label": _label(raw_cid), "aliases": [], "source": "user"}
                if cid not in p["conceptIds"]:
                    p["conceptIds"].append(cid)
            p["keywordMetadata"] = keyword_meta.get(p["id"], [])
            p["keywords"] = extracted_keywords
            p.pop("_ruleFields", None)
            p.pop("indexedText", None)
            p.pop("fullText", None)
            pins = overlay_copy.get("pins", {})
            if isinstance(pins, Mapping) and p["id"] in pins:
                p["pinned"] = bool(pins[p["id"]])
            p.pop("_inputFieldIds", None)
            p.pop("_inputConceptIds", None)

        configured_vectors = next((settings.get(key) for key in ("fakeVectors", "embeddingVectors", "vectors")
                                   if isinstance(settings.get(key), Mapping)), None)
        pool_key = (str(self.cache_dir.resolve()), str(self.provider_name).lower(), bool(self.allow_download))
        provider = _PROVIDER_POOL.get(pool_key)
        if provider is None:
            provider = EmbeddingProvider(self.cache_dir, self.provider_name, self.allow_download,
                                         vectors=configured_vectors)
            _PROVIDER_POOL[pool_key] = provider
        elif provider.provider in {"fake", "test"}:
            provider._vectors = ({str(k): list(map(float, v)) for k, v in configured_vectors.items()}
                                 if configured_vectors is not None else {})
        self._provider_instance = provider
        embedding_records = provider.embed_many(paper_rows)
        failed_embeddings = any(x.failed for x in embedding_records)
        vectors = {r.document_id: r.vector for r in embedding_records if r.vector is not None}
        embedding_by_id = {r.document_id: r for r in embedding_records}
        for paper in paper_rows:
            record = embedding_by_id.get(paper["id"])
            if record is not None:
                paper["embedding"] = record.metadata()
        threshold = float(settings.get("semanticThreshold", settings.get("threshold", 0.80)))
        top_k = max(0, int(settings.get("semanticTopK", settings.get("topK", 5))))
        edges: list[dict[str, Any]] = []
        edge_keys: set[tuple[str, str, str]] = set()
        def add_edge(a: str, b: str, kind: str, directed: bool, score: float | None,
                     provider_name: str, method: str, tier: str = "unknown", verified: bool = False,
                     extra: Mapping[str, Any] | None = None) -> None:
            if a == b:
                return
            key_a, key_b = (a, b) if directed else _canonical_pair(a, b)
            key = (key_a, key_b, kind)
            if key in edge_keys:
                return
            edge_keys.add(key)
            if score is not None:
                score = max(0.0, min(1.0, float(score)))
            row = {"id": _edge_id(server, epoch, a, b, kind, directed), "source": a, "target": b,
                   "type": kind, "directed": directed, "score": score,
                   "provenance": {"provider": provider_name, "method": method,
                                  "modelRevision": provider.revision if provider_name in {"e5", "fake", "tfidf", "embedding"} else None},
                   "evidenceTier": tier, "verified": bool(verified)}
            if extra:
                row.update(copy.deepcopy(dict(extra)))
            edges.append(row)

        # Keep actual paper nodes even when there is no abstract or no model.
        paper_order = [p["id"] for p in paper_rows]
        candidate_scores: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for i, left in enumerate(paper_order):
            for right in paper_order[i + 1:]:
                va, vb = vectors.get(left), vectors.get(right)
                if va is None or vb is None or len(va) != len(vb):
                    continue
                score = max(-1.0, min(1.0, sum(x * y for x, y in zip(va, vb))))
                if score < threshold:
                    continue
                candidate_scores[left].append((score, right))
                candidate_scores[right].append((score, left))
        selected_pairs: set[tuple[str, str]] = set()
        for left, candidates in candidate_scores.items():
            for score, right in sorted(candidates, key=lambda pair: (-pair[0], pair[1]))[:top_k]:
                selected_pairs.add(_canonical_pair(left, right))
        for left, right in sorted(selected_pairs):
            va, vb = vectors.get(left), vectors.get(right)
            if va is None or vb is None:
                continue
            score = sum(x * y for x, y in zip(va, vb))
            add_edge(left, right, "semantic", False, round(float(score), 6), provider.provider, "cosine", "metadata", False)
        openalex_enabled = bool(settings.get("openAlexEnabled", settings.get("openalexEnabled", settings.get("openAlex", False))))
        openalex_refs, openalex_status, openalex_failed = self._openalex_enrichment(paper_rows, key_to_id, settings)
        # Explicit references are the only local citation input.  Related items
        # stay their own edge type and are never used as citations.
        for p in paper_rows:
            pid = p["id"]
            refs = p.get("references") or p.get("citationTargets") or p.get("citationEdges") or p.get("citations") or []
            if isinstance(refs, Mapping):
                refs = [refs]
            for ref in refs:
                target, verified, tier, prov = _verified_reference(ref)
                if target is None:
                    continue
                target = key_to_id.get(str(target), str(target))
                if target in paper_ids and verified:
                    add_edge(pid, target, "citation", True, None, prov, "verified-reference", tier, True)
            # OpenAlex enrichment is opt-in and consumes only an explicit
            # referenced_works payload with retrieval provenance supplied by
            # the caller.  It never runs for a DOI alone and never uses
            # semantic/related-item suggestions as citations.
            if openalex_enabled:
                supplied_openalex = p.get("openalexReferences", []) or []
                for ref in [*openalex_refs.get(pid, []), *supplied_openalex]:
                    if not isinstance(ref, Mapping):
                        continue
                    target = key_to_id.get(str(ref.get("key") or ref.get("paperId") or ref.get("id")), str(ref.get("paperId") or ref.get("id") or ""))
                    provenance = ref.get("retrievalProvenance") or ref.get("provenance")
                    if target in paper_ids and provenance:
                        add_edge(pid, target, "citation", True, None, "openalex", "referenced_works", "metadata", True,
                                 {"retrievalProvenance": copy.deepcopy(provenance)})
            related = p.get("relatedItems") or p.get("related") or []
            for target in related if isinstance(related, list) else [related]:
                target = key_to_id.get(str(target), str(target))
                if target in paper_ids:
                    add_edge(pid, target, "related", False, 1.0, "zotero-related", "related-item", "metadata", True)
        # A failed optional enrichment must not erase a previously verified
        # citation whose two endpoints are still in the active source/epoch.
        # Endpoint validation prevents deleted or cross-epoch records from
        # being resurrected by a stale cache.
        for old in (previous.get("edges", []) if isinstance(previous, Mapping) else []):
            if not isinstance(old, Mapping) or old.get("type") != "citation":
                continue
            if old.get("source") not in paper_ids or old.get("target") not in paper_ids:
                continue
            if not str(old.get("id", "")).startswith(f"edge:{server}:{epoch}:"):
                continue
            if not old.get("verified", False) or not openalex_failed:
                continue
            provenance = old.get("provenance") if isinstance(old.get("provenance"), Mapping) else {}
            add_edge(str(old["source"]), str(old["target"]), "citation", True, old.get("score"),
                     str(provenance.get("provider", "cached")), str(provenance.get("method", "cached")),
                     str(old.get("evidenceTier", "unknown")), bool(old.get("verified", False)),
                     {"cached": True})
        # Concept-concept cooccurrence uses unique papers, separate from paper
        # similarity and citations.  Scores are counts, while edge scores stay
        # in the schema's [0,1] range and the exact count is explicit.
        counts: Counter[tuple[str, str]] = Counter()
        all_pair_counts: Counter[tuple[str, str]] = Counter()
        cooccurrence_tiers: dict[tuple[str, str], set[str]] = defaultdict(set)
        all_concept_pairs: set[tuple[str, str]] = set()
        keyword_candidate_pairs: set[tuple[str, str]] = set()
        for p in paper_rows:
            # Keep all concepts and paper memberships in the snapshot, while
            # bounding the pair construction to a deterministic focus set.
            # This is a display-edge policy, not a claim that the omitted
            # pairs do not exist; aggregate candidate counts are disclosed in
            # overview/modelStatus for focused-query implementations.
            all_cids = sorted(set(p["conceptIds"]))
            for all_i, all_a in enumerate(all_cids):
                for all_b in all_cids[all_i + 1:]:
                    all_concept_pairs.add((all_a, all_b))
                    all_pair_counts[(all_a, all_b)] += 1
            cids = sorted(set(list(dict.fromkeys(p["conceptIds"]))[:KEYWORD_TOPK_PER_PAPER]))
            tier_by_concept: dict[str, set[str]] = defaultdict(set)
            for metadata in p.get("keywordMetadata", []):
                label = _norm(metadata.get("label"))
                cid = alias_to_concept.get(label)
                if cid:
                    tier_by_concept[cid].add(str(metadata.get("evidenceTier") or "metadata"))
            for i, a in enumerate(cids):
                for b in cids[i + 1:]:
                    counts[(a, b)] += 1
                    keyword_candidate_pairs.add((a, b))
                    cooccurrence_tiers[(a, b)].update(tier_by_concept.get(a, {"metadata"}))
                    cooccurrence_tiers[(a, b)].update(tier_by_concept.get(b, {"metadata"}))
        for (a, b), count in counts.items():
            if count < KEYWORD_MIN_COOCCURRENCE:
                continue
            evidence_tiers = sorted(cooccurrence_tiers.get((a, b), {"metadata"}))
            evidence_tier = evidence_tiers[0] if len(evidence_tiers) == 1 else "unknown"
            add_edge(a, b, "keyword", False, 1.0, "cooccurrence", "unique-paper-count", "metadata", True,
                     {"cooccurrenceCount": count, "displayPolicy": "shared-paper-threshold",
                      "evidenceTiers": evidence_tiers})
            edges[-1]["evidenceTier"] = evidence_tier
        # Manual/source edge rows can be retained when they are valid and same
        # epoch; this supports reindexing without losing user-confirmed data.
        prior_edges = previous.get("edges", []) if isinstance(previous, Mapping) else []
        if failed_embeddings:
            for old in prior_edges:
                if isinstance(old, Mapping) and old.get("type") == "semantic" and old.get("source") in paper_ids and old.get("target") in paper_ids:
                    add_edge(str(old["source"]), str(old["target"]), "semantic", False, old.get("score"),
                             str(old.get("provenance", {}).get("provider", "embedding")),
                             str(old.get("provenance", {}).get("method", "cached")),
                             str(old.get("evidenceTier", "unknown")), bool(old.get("verified", False)), {"stale": True})

        # The overview allocates each paper to its primary/display field.  All
        # field rows remain visible, including zero-degree fields.
        field_weights = Counter(p.get("displayFieldId") for p in paper_rows)
        overview_fields = [{"fieldId": fid, "paperWeight": float(field_weights.get(fid, 0))} for fid in sorted(fields)]
        ribbons: dict[tuple[str, str], dict[str, Any]] = {}
        field_by_paper = {p["id"]: p.get("displayFieldId") for p in paper_rows}
        for edge in edges:
            if edge["type"] in {"keyword", "field"} or edge["source"] not in field_by_paper or edge["target"] not in field_by_paper:
                continue
            left, right = field_by_paper.get(edge["source"]), field_by_paper.get(edge["target"])
            if not left or not right or left == right:
                continue
            pair = _canonical_pair(left, right)
            ribbon = ribbons.setdefault(pair, {"sourceFieldId": pair[0], "targetFieldId": pair[1], "weight": 0.0, "count": 0, "edgeIds": [], "types": {}, "type": edge["type"]})
            if ribbon["type"] != edge["type"]:
                ribbon["type"] = "mixed"
            ribbon["weight"] += 1.0
            ribbon["count"] += 1
            ribbon["edgeIds"].append(edge["id"])
            ribbon["types"][edge["type"]] = ribbon["types"].get(edge["type"], 0) + 1
        for field in fields.values():
            pins = overlay_copy.get("pins", {})
            if isinstance(pins, Mapping) and field["id"] in pins:
                field["pinned"] = bool(pins[field["id"]])
        for concept in concepts.values():
            pins = overlay_copy.get("pins", {})
            if isinstance(pins, Mapping) and concept["id"] in pins:
                concept["pinned"] = bool(pins[concept["id"]])
        full_edge_counts = Counter(edge["type"] for edge in edges)
        full_edge_counts["keyword"] = len(all_pair_counts)
        full_total_edges = sum(full_edge_counts.values())
        aggregate_concepts = sorted({concept_id for pair in all_pair_counts for concept_id in pair})
        aggregate_index = {concept_id: index for index, concept_id in enumerate(aggregate_concepts)}
        keyword_aggregate = {
            "conceptIds": aggregate_concepts,
            "pairs": [[aggregate_index[a], aggregate_index[b], count] for (a, b), count in sorted(all_pair_counts.items())],
            "encoding": "concept-index-pairs",
            "totalPairs": len(all_pair_counts),
            "totalWeight": sum(all_pair_counts.values()),
        }
        overview = {"fields": overview_fields, "ribbons": list(ribbons.values()),
                    "totalPapers": len(paper_rows), "totalEdges": full_total_edges,
                    "displayedEdges": len(edges), "fullGraphEdgeCounts": dict(full_edge_counts),
                    "allocationMode": "primary-display-field", "unassignedPapers": sum(1 for p in paper_rows if not p.get("fieldIds")),
                    "sectors": [{"fieldId": x["fieldId"], "paperWeight": x["paperWeight"], "type": "field"} for x in overview_fields]}
        overview["keywordCooccurrence"] = {
            "policy": "bounded-topk-shared-paper",
            "topKPerPaper": KEYWORD_TOPK_PER_PAPER,
            "minCooccurrence": KEYWORD_MIN_COOCCURRENCE,
            "allConceptPairCount": len(all_concept_pairs),
            "selectedConceptCandidatePairs": len(keyword_candidate_pairs),
            "displayedPairs": sum(1 for edge in edges if edge["type"] == "keyword"),
        }
        overview["keywordAggregate"] = keyword_aggregate
        for ribbon in overview["ribbons"]:
            ribbon["topFieldIDs"] = [ribbon["sourceFieldId"], ribbon["targetFieldId"]]

        model_status = {"provider": provider.provider, "label": "synthetic-test-only" if provider.provider == "fake" else provider.provider,
                        "revision": provider.revision, "modelCardHash": embedding_records[0].model_card_hash if embedding_records else None,
                        "artifactHash": embedding_records[0].artifact_hash if embedding_records else None,
                        "dimensions": max((r.dimensions for r in embedding_records), default=0), "tokenLimit": 512,
                        "prefix": "query: ", "inputHashes": {r.document_id: r.input_hash for r in embedding_records},
                        "truncated": {r.document_id: r.truncated for r in embedding_records},
                        "failures": {r.document_id: r.error for r in embedding_records if r.failed},
                        "failed": failed_embeddings, "status": "failed" if failed_embeddings else "ready",
                        "loadError": getattr(provider, "_model_error", None),
                        "tokenizerError": getattr(provider, "_tokenizer_error", None),
                        "cacheError": getattr(provider, "_cache_error", None)}
        model_status["keywordPolicy"] = {
            "name": "bounded-topk-shared-paper", "topKPerPaper": KEYWORD_TOPK_PER_PAPER,
            "minCooccurrence": KEYWORD_MIN_COOCCURRENCE,
            "allConceptPairCount": len(all_concept_pairs),
            "fullAggregatePairCount": len(all_pair_counts),
            "selectedConceptCandidatePairs": len(keyword_candidate_pairs),
            "displayedPairs": sum(1 for edge in edges if edge["type"] == "keyword"),
            "allConceptsAndMembershipsRetained": True,
        }
        model_status["openAlexStatus"] = openalex_status
        model_status["citationStatus"] = "unknown" if openalex_status in {"unknown", "failed"} else (
            "cached-or-verified" if any(e["type"] == "citation" for e in edges) else "none")
        out_attachments = []
        for raw in list(attachments or []):
            if not isinstance(raw, Mapping):
                continue
            row = copy.deepcopy(dict(raw))
            aid = str(row.get("id") or _id("attachment", server, epoch, row.get("key", "unknown")))
            if not aid.startswith("attachment:"):
                aid = _id("attachment", server, epoch, aid)
            parent = str(row.get("parentId") or row.get("parentItemId") or "")
            row.update({"id": aid, "parentId": key_to_id.get(parent, parent), "name": str(row.get("name") or ""),
                        "status": str(row.get("status") or "missing")})
            out_attachments.append(row)
        collections = []
        if isinstance(source.get("collections"), list):
            collections = copy.deepcopy(source["collections"])
        return {"schemaVersion": 1, "source": source_out,
                "status": {"phase": "idle", "stale": failed_embeddings, "lastSuccess": None,
                           "message": "embedding provider failed; cached semantic edges retained" if failed_embeddings else None,
                           "observedLag": None, "watcherInterval": None},
                "papers": paper_rows, "fields": list(fields.values()), "concepts": list(concepts.values()),
                "memberships": memberships, "edges": edges, "attachments": out_attachments, "collections": collections,
                "overview": overview, "modelStatus": model_status, "allocationMode": "primary-display-field"}


"""Validation and merge helpers for the taxonomy v2 persistence contract.

The bridge stores user authored taxonomy/overlay state as JSON singleton
payloads.  This module deliberately contains no graph-building code: it
validates references at the write boundary and performs additive merges so a
legacy editor cannot accidentally erase newer facet metadata.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping


FACET_KINDS = frozenset({"subject", "method", "tool", "application"})
QUALITY_STATUSES = frozenset({"raw", "active", "suppressed", "protected", "user"})


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


CONFIG_KEYS = frozenset({"version", "entities"})
# Editable keys plus the known generated catalog fields a UI may round-trip.
ENTITY_KEYS = frozenset({"id", "facetId", "key", "ontologyKey", "kind", "label", "broaderIds", "aliases", "keywords", "legacyFieldId", "origin", "version"})
PERSISTED_TAXONOMY_KEYS = ("fields", "concepts", "facetCatalogConfig", "inputState")
MAX_ENTITIES = 2000


def builtin_reference_index(source_id: str | None, source_epoch: str | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(entity id -> kind, semantic key -> entity id)`` for the builtin catalog."""
    if not source_id or not source_epoch:
        return {}, {}
    from .taxonomy import build_facet_catalog
    catalog = build_facet_catalog(str(source_id), str(source_epoch), {}, None)
    return ({row["id"]: row["kind"] for row in catalog["entities"]}, {row["ontologyKey"]: row["id"] for row in catalog["entities"]})


def facet_entities(config: Mapping[str, Any] | None, *, source_id: str | None = None,
                   source_epoch: str | None = None, require_source_scope: bool = False) -> dict[str, dict[str, Any]]:
    """Validate an editable facet catalog config and return canonical entity rows.

    References (``broaderIds``) are normalized to canonical ``entity.id`` before
    the same-kind and cycle checks; builtin entities may be referenced as
    parents.  Unknown keys are rejected (strict editable config) while the
    known generated catalog fields are accepted for round-tripping.
    """
    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise ValueError("facetCatalogConfig must be an object")
    if set(config) - CONFIG_KEYS:
        raise ValueError("unknown facetCatalogConfig key")
    rows = _list(config.get("entities", []) if config.get("entities") is not None else [], "facetCatalogConfig.entities")
    if len(rows) > MAX_ENTITIES:
        raise ValueError("too many facet entities")
    builtin_kinds, builtin_keys = builtin_reference_index(source_id, source_epoch) if require_source_scope else ({}, {})
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("facet entity must be an object")
        if set(raw) - ENTITY_KEYS:
            raise ValueError("unknown facet entity key")
        entity_id = _string(raw.get("id"), "facet entity id")
        if require_source_scope and not entity_id.startswith(f"facet:{source_id}:{source_epoch}:"):
            raise ValueError("facet entity id is not source scoped")
        kind = _string(raw.get("kind"), "facet entity kind")
        if kind not in FACET_KINDS:
            raise ValueError("invalid facet entity kind")
        for text_key in ("facetId", "key", "ontologyKey", "origin"):
            if text_key in raw:
                _string(raw.get(text_key), f"facet entity {text_key}")
        if "ontologyKey" in raw and not str(raw["ontologyKey"]).startswith(kind + ":"):
            raise ValueError("facet ontologyKey kind mismatch")
        label = raw.get("label", {})
        if not isinstance(label, (str, Mapping)) or (isinstance(label, Mapping) and any(not isinstance(v, str) for v in label.values())):
            raise ValueError("facet entity label must be text or object")
        for list_key in ("broaderIds", "aliases", "keywords"):
            value = raw.get(list_key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() or len(item) > 512 for item in value) or len(value) > 200:
                raise ValueError(f"facet {list_key} must be string array")
        if "legacyFieldId" in raw and raw["legacyFieldId"] is not None:
            _string(raw["legacyFieldId"], "facet legacyFieldId")
        if entity_id in result or entity_id in builtin_kinds:
            raise ValueError("duplicate facet entity id")
        row = copy.deepcopy(dict(raw))
        row["id"], row["kind"] = entity_id, kind
        for list_key in ("broaderIds", "aliases"):
            row[list_key] = list(raw.get(list_key, []))
        result[entity_id] = row
    semantic_to_id = dict(builtin_keys)
    for entity_id, row in result.items():
        for key in ("facetId", "ontologyKey"):
            if row.get(key):
                if semantic_to_id.get(str(row[key]), entity_id) != entity_id:
                    raise ValueError("duplicate facet ontologyKey")
                semantic_to_id[str(row[key])] = entity_id
    kind_of = {**builtin_kinds, **{k: v["kind"] for k, v in result.items()}}
    # Normalize parent aliases (semantic key or entity id) BEFORE validation.
    for entity_id, row in result.items():
        normalized = []
        for parent in row["broaderIds"]:
            parent_id = semantic_to_id.get(parent, parent)
            if parent_id == entity_id:
                raise ValueError("facet cannot parent itself")
            if parent_id not in kind_of:
                raise ValueError("unknown facet parent")
            if kind_of[parent_id] != row["kind"]:
                raise ValueError("facet broaderIds must reference the same kind")
            if parent_id not in normalized:
                normalized.append(parent_id)
        row["broaderIds"] = normalized
    _check_acyclic({key: row["broaderIds"] for key, row in result.items()})
    return result


def _check_acyclic(parents: Mapping[str, list[str]]) -> None:
    """Reject parent cycles with an iterative O(V+E) walk (no recursion, no path copies)."""
    from .taxonomy import find_back_edges
    if find_back_edges(parents):
        raise ValueError("facet taxonomy cycle")


def validate_input_state(value: Any) -> dict[str, Any]:
    """Validate the editable ``inputState``: only ``status`` and a short ``reason`` are persisted."""
    if value is None:
        return {"status": "absent"}
    if not isinstance(value, Mapping):
        raise ValueError("taxonomy inputState must be an object")
    if set(value) - {"status", "reason"}:
        raise ValueError("unknown taxonomy inputState key")
    status = value.get("status", "absent")
    if status not in {"absent", "uninitialized", "intentional-empty-valid", "valid-custom", "invalid", "disabled"}:
        raise ValueError("invalid taxonomy inputState status")
    result: dict[str, Any] = {"status": status}
    if "reason" in value:
        if not isinstance(value["reason"], str) or len(value["reason"]) > 256:
            raise ValueError("taxonomy inputState reason must be a short string")
        result["reason"] = value["reason"]
    return result


def validate_facet_assignments(value: Any, *, known_ids: set[str], semantic: Mapping[str, str] | None = None,
                               paper_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Validate user facet assignments and persist them as canonical ``{facetId}`` rows."""
    rows = _list(value, "facetAssignments")
    if len(rows) > 200:
        raise ValueError("too many facet assignments")
    result: list[dict[str, Any]] = []
    for raw in rows:
        if isinstance(raw, Mapping):
            if set(raw) - {"facetId", "paperId"}:
                raise ValueError("unknown facet assignment key")
            if "paperId" in raw and paper_ids is not None and raw["paperId"] not in paper_ids:
                raise ValueError("unknown facet assignment paper")
        entity_id = canonical_facet_ref(raw, known_ids, semantic or {})
        if {"facetId": entity_id} not in result:
            result.append({"facetId": entity_id})
    return result


def validate_primary_overrides(value: Any, *, known_ids: set[str], kinds: Mapping[str, str],
                               semantic: Mapping[str, str] | None = None) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("primaryFacetOverrides must be an object")
    result: dict[str, str] = {}
    for kind, ref in value.items():
        if kind not in FACET_KINDS:
            raise ValueError("invalid primary facet kind")
        entity_id = canonical_facet_ref(ref, known_ids, semantic or {})
        if kinds.get(entity_id) != kind:
            raise ValueError("primary facet override kind mismatch")
        result[str(kind)] = entity_id
    return result


def validate_quality_override(value: Any, *, known_concepts: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("concept quality override must be an object")
    if set(value) - {"status", "protected", "conceptId", "reason"}:
        raise ValueError("unknown concept quality override key")
    result = copy.deepcopy(dict(value))
    if "status" in result and result["status"] not in QUALITY_STATUSES:
        raise ValueError("invalid concept quality status")
    if "conceptId" in result and result["conceptId"] not in known_concepts:
        raise ValueError("unknown concept quality concept")
    if "protected" in result and not isinstance(result["protected"], bool):
        raise ValueError("concept quality protected must be boolean")
    if "reason" in result and (not isinstance(result["reason"], str) or len(result["reason"]) > 256):
        raise ValueError("concept quality reason must be a short string")
    return result


def merge_payload(current: Mapping[str, Any] | None, incoming: Mapping[str, Any], *, replace_keys: set[str] | None = None) -> dict[str, Any]:
    """Merge a taxonomy write, persisting only whitelisted user-owned keys.

    Request control keys (entityType/entityId/patch/label/...) and generated
    snapshot blocks are never persisted; extensions already stored (facet
    config, inputState) are retained through legacy fields/concepts edits.
    """
    result = {key: copy.deepcopy(current[key]) for key in PERSISTED_TAXONOMY_KEYS if current and key in current}
    for key in PERSISTED_TAXONOMY_KEYS:
        if key in incoming:
            result[key] = copy.deepcopy(incoming[key])
    return result


def known_facet_index(taxonomy: Mapping[str, Any], snapshot: Mapping[str, Any] | None = None, *,
                      source_id: str | None = None, source_epoch: str | None = None) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Return ``(canonical entity ids, id -> kind, semantic key -> id)`` for write validation."""
    builtin_kinds, builtin_keys = builtin_reference_index(source_id, source_epoch)
    kinds, semantic = dict(builtin_kinds), dict(builtin_keys)
    config = taxonomy.get("facetCatalogConfig") if isinstance(taxonomy, Mapping) else None
    if isinstance(config, Mapping):
        for entity_id, row in facet_entities(config).items():
            kinds[entity_id] = row["kind"]
            for key in ("facetId", "ontologyKey"):
                if row.get(key): semantic[str(row[key])] = entity_id
    catalog = (snapshot or {}).get("facetCatalog") if isinstance(snapshot, Mapping) else None
    for row in (catalog.get("entities", []) if isinstance(catalog, Mapping) else []):
        if isinstance(row, Mapping) and row.get("id") and row.get("kind") in FACET_KINDS:
            kinds[str(row["id"])] = str(row["kind"])
            if row.get("ontologyKey"): semantic[str(row["ontologyKey"])] = str(row["id"])
    return set(kinds), kinds, semantic


def canonical_facet_ref(value: Any, known_ids: set[str], semantic: Mapping[str, str]) -> str:
    """Normalise an ID or semantic key to the canonical entity id or raise."""
    ref = value.get("facetId") if isinstance(value, Mapping) else value
    ref = _string(ref, "facet reference")
    entity_id = ref if ref in known_ids else semantic.get(ref)
    if entity_id is None or entity_id not in known_ids:
        raise ValueError("unknown facet reference")
    return entity_id

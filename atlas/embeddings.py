"""Local embedding providers used by the Literature Atlas graph.

The provider deliberately has a small, dependency-free surface.  Importing
``sentence_transformers`` is lazy so the bridge can still start when the
optional model package is unavailable; that state is reported to callers and
never silently represented as an AI result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_CARD_URL = "https://huggingface.co/intfloat/multilingual-e5-small"
MAX_TOKENS = 512
PREFIX = "query: "
_WS = re.compile(r"\s+")


def normalize_text(value: Any) -> str:
    """Return stable whitespace-normalized text without exposing raw paths."""

    if value is None:
        return ""
    return _WS.sub(" ", str(value).replace("\x00", " ")).strip()


def document_text(document: Mapping[str, Any], *, token_limit: int = MAX_TOKENS,
                  tokenizer: Any = None, diagnostics: list[str] | None = None) -> tuple[str, bool]:
    """Build the E5 comparison text and apply a real bounded truncation.

    E5 uses a single query prefix for each member of a symmetric paper-paper
    comparison.  The title and abstract are kept in a deterministic order.
    A tokenizer, when supplied by the loaded model, defines the actual limit;
    the whitespace fallback is conservative and still records truncation.
    """

    title = normalize_text(document.get("title", ""))
    abstract = normalize_text(document.get("abstract", ""))
    raw = f"{PREFIX}{title}"
    if abstract:
        raw += f" {abstract}"
    if tokenizer is not None:
        try:
            ids = tokenizer.encode(raw, add_special_tokens=True, truncation=False)
            if len(ids) > token_limit:
                content = raw[len(PREFIX):]
                content_ids = tokenizer.encode(content, add_special_tokens=True, truncation=False)
                clipped = content_ids[: max(1, token_limit - 2)]
                decoded = tokenizer.decode(clipped, skip_special_tokens=True).strip()
                return f"{PREFIX}{decoded} [TRUNCATED]", True
            return raw, False
        except Exception as exc:
            if diagnostics is not None:
                diagnostics.append(f"{type(exc).__name__}: {str(exc)[:160]}")
    # Keep prefix and title intact where possible.  This is an approximate
    # tokenizer only until a real model tokenizer is available.
    words = raw.split()
    if len(words) > token_limit:
        return " ".join(words[: max(1, token_limit - 1)]) + " [TRUNCATED]", True
    return raw, False


def input_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def revision_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unit(values: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in values))
    if norm == 0:
        return [0.0 for _ in values]
    return [float(x) / norm for x in values]


@dataclass(frozen=True)
class EmbeddingResult:
    document_id: str
    vector: list[float] | None
    input_hash: str
    provider: str
    revision: str
    model_card_hash: str
    dimensions: int
    token_limit: int
    truncated: bool
    failed: bool = False
    error: str | None = None
    prepared_input: str = ""
    artifact_hash: str = ""

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("vector", None)
        data.pop("prepared_input", None)
        data["failure"] = self.error if self.failed else None
        return data


class EmbeddingProvider:
    """A real local E5 provider, plus explicitly labelled test providers."""

    def __init__(self, cache_dir: Path, provider: str = "e5",
                 allow_download: bool = True, *, model_name: str = MODEL_NAME,
                 vectors: Mapping[str, Sequence[float]] | None = None):
        self.cache_dir = Path(cache_dir)
        self.provider = str(provider).lower()
        self.allow_download = bool(allow_download)
        self.model_name = model_name
        self._model: Any = None
        self._tokenizer: Any = None
        self._model_error: str | None = None
        self._tokenizer_error: str | None = None
        self._cache_error: str | None = None
        self._artifact_hash = revision_hash("unloaded")
        self._vectors = {str(k): list(map(float, v)) for k, v in (vectors or {}).items()}
        self._revision = (
            "fixture-e5-revision" if self.provider in {"fake", "test"}
            else ("tfidf-1" if self.provider == "tfidf" else MODEL_NAME)
        )
        self._model_card_hash = revision_hash(MODEL_CARD_URL)

    @property
    def revision(self) -> str:
        return self._revision

    def _cache_path(self, digest: str) -> Path:
        # The filename is an input digest and model revision only.  No paper
        # title, DOI, item key, source path, or other source value is logged.
        safe_revision = hashlib.sha256((self._revision + self._artifact_hash + self._model_card_hash).encode()).hexdigest()[:20]
        return self.cache_dir / "embeddings" / f"{digest[7:]}-{safe_revision}.json"

    @staticmethod
    def _artifact_fingerprint(model: Any) -> str:
        """Fingerprint the loaded model snapshot without copying corpus data.

        Hugging Face snapshots use content-addressed symlinks.  Recording the
        resolved blob names gives a cheap, stable artifact fingerprint; for a
        non-snapshot model directory we hash the file bytes instead.
        """
        try:
            tokenizer = getattr(model, "tokenizer", None)
            vocab = getattr(tokenizer, "vocab_file", None)
            root = Path(os.path.realpath(vocab)).parent if vocab else None
            if root is None or not root.exists():
                return revision_hash(str(getattr(model, "name_or_path", "unknown")))
            entries = []
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                real = Path(os.path.realpath(path))
                try:
                    target = real.name if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
                    entries.append(f"{path.relative_to(root)}:{path.stat().st_size}:{target}")
                except OSError:
                    continue
            return revision_hash("\n".join(entries))
        except Exception:
            return revision_hash("artifact-fingerprint-error")

    def _load_model(self) -> None:
        if self._model is not None or self._model_error is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            # Recent sentence-transformers forwards ``local_files_only`` via
            # model_kwargs; older releases accept it directly.  Never retry a
            # failed local-only load in a way that could start a download.
            try:
                self._model = SentenceTransformer(
                    self.model_name, device="cpu",
                    model_kwargs={"local_files_only": not self.allow_download},
                )
            except TypeError:
                if not self.allow_download:
                    raise
                self._model = SentenceTransformer(self.model_name, device="cpu")
            self._tokenizer = getattr(self._model, "tokenizer", None)
            cfg = getattr(self._model, "_first_module", lambda: None)()
            revision = getattr(cfg, "auto_model", None)
            config = getattr(revision, "config", None)
            commit = getattr(config, "_commit_hash", None) or getattr(config, "commit_hash", None)
            self._revision = f"{self.model_name}@{commit or 'unresolved'}"
            card = getattr(self._model, "_model_card_text", None)
            if isinstance(card, str) and card:
                self._model_card_hash = revision_hash(card)
            self._artifact_hash = self._artifact_fingerprint(cfg)
        except Exception as exc:  # keep diagnostics bounded and source-free
            self._model_error = f"{type(exc).__name__}: {str(exc)[:240]}"

    def _prepare_text(self, document: Mapping[str, Any]) -> tuple[str, bool, str | None]:
        diagnostics: list[str] = []
        text, truncated = document_text(document, token_limit=MAX_TOKENS,
                                         tokenizer=self._tokenizer, diagnostics=diagnostics)
        error = diagnostics[0] if diagnostics else None
        if error:
            self._tokenizer_error = error
        return text, truncated, error

    def _fake(self, text: str, dimensions: int = 384) -> list[float]:
        # Deterministic unit vectors are intentionally labelled synthetic.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        for i in range(dimensions):
            block = hashlib.sha256(digest + i.to_bytes(4, "big")).digest()
            values.append((int.from_bytes(block[:8], "big") / 2**63) - 1.0)
        return _unit(values)

    def _tfidf(self, text: str) -> list[float]:
        # A small deterministic fallback for offline development.  It is a
        # distinct provider and is never reported as E5 or AI inference.
        terms = re.findall(r"[\w\u0080-\uffff]+", text.casefold())
        vocab = sorted(set(terms))[:384]
        return _unit([float(terms.count(term)) for term in vocab]) or [1.0]

    def _read_cached(self, doc_id: str, text: str, digest: str, truncated: bool) -> EmbeddingResult | None:
        path = self._cache_path(digest)
        if not path.exists():
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (not cached.get("failed") and cached.get("inputHash") == digest
                    and cached.get("revision") == self._revision
                    and cached.get("artifactHash", "") == self._artifact_hash
                    and cached.get("modelCardHash") == self._model_card_hash):
                return EmbeddingResult(
                    doc_id, cached.get("vector"), digest, cached["provider"], cached["revision"],
                    cached["modelCardHash"], int(cached["dimensions"]), int(cached["tokenLimit"]),
                    bool(cached.get("truncated", truncated)), False, None, text, cached.get("artifactHash", ""),
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._cache_error = f"cache-read-{type(exc).__name__}"
            pass
        return None

    def _write_cached(self, result: EmbeddingResult) -> None:
        try:
            path = self._cache_path(result.input_hash)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**result.metadata(), "artifactHash": self._artifact_hash, "vector": result.vector}, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            self._cache_error = f"cache-write-{type(exc).__name__}"

    @staticmethod
    def _shared_tfidf(texts: Sequence[str]) -> list[list[float]]:
        """Encode a batch against one deterministic, shared corpus basis."""
        token_rows = [re.findall(r"[\w\u0080-\uffff]+", text.removeprefix(PREFIX).casefold()) for text in texts]
        document_frequency: Counter[str] = Counter()
        for terms in token_rows:
            document_frequency.update(set(terms))
        # ``query`` is part of E5's transport prefix, never a semantic term.
        document_frequency.pop("query", None)
        vocabulary = sorted(document_frequency, key=lambda term: (-document_frequency[term], term))[:384]
        n_documents = max(1, len(token_rows))
        idf = {term: math.log((1 + n_documents) / (1 + document_frequency[term])) + 1.0 for term in vocabulary}
        return [_unit([terms.count(term) * idf[term] for term in vocabulary]) or [1.0] for terms in token_rows]

    def embed(self, document: Mapping[str, Any], *, document_id: str | None = None) -> EmbeddingResult:
        self._load_model() if self.provider == "e5" else None
        text, truncated, tokenizer_error = self._prepare_text(document)
        digest = input_hash(text)
        doc_id = str(document_id or document.get("id") or document.get("key") or digest)
        cached = self._read_cached(doc_id, text, digest, truncated)
        if cached is not None and tokenizer_error is None:
            return cached

        vector: list[float] | None = None
        failed = False
        error: str | None = None
        if tokenizer_error is not None and self.provider == "e5":
            failed = True
            error = f"tokenizer-failure: {tokenizer_error}"
            revision = self._revision
            provider_name = "e5"
            dimensions = 0
        elif self.provider in {"fake", "test"}:
            vector = _unit(self._vectors.get(doc_id, self._fake(text)))
            revision = "fixture-e5-revision"
            dimensions = len(vector)
            provider_name = "fake"
        elif self.provider == "tfidf":
            vector = self._tfidf(text)
            revision = self._revision
            dimensions = len(vector)
            provider_name = "tfidf"
        elif self.provider == "e5":
            revision = self._revision
            provider_name = "e5"
            if self._model is None:
                failed = True
                error = self._model_error or "model unavailable"
                dimensions = 0
            else:
                try:
                    encoded = self._model.encode(
                        [text], normalize_embeddings=True, convert_to_numpy=True,
                        show_progress_bar=False, device="cpu",
                    )
                    row = encoded[0].tolist() if hasattr(encoded[0], "tolist") else list(encoded[0])
                    vector = _unit([float(x) for x in row])
                    dimensions = len(vector)
                except Exception as exc:
                    failed = True
                    error = f"{type(exc).__name__}: {str(exc)[:240]}"
                    dimensions = 0
        else:
            provider_name = self.provider
            revision = self._revision
            failed = True
            error = f"unknown provider: {self.provider}"
            dimensions = 0

        result = EmbeddingResult(
            doc_id, vector, digest, provider_name, revision, self._model_card_hash,
            dimensions, MAX_TOKENS, truncated, failed, error, text, self._artifact_hash,
        )
        self._write_cached(result)
        return result

    def embed_many(self, documents: Iterable[Mapping[str, Any]]) -> list[EmbeddingResult]:
        rows = list(documents)
        if self.provider == "e5":
            self._load_model()
            prepared = [self._prepare_text(doc) for doc in rows]
            results: list[EmbeddingResult | None] = [None] * len(rows)
            missing: list[int] = []
            for i, (doc, (text, truncated, tokenizer_error)) in enumerate(zip(rows, prepared)):
                doc_id = str(doc.get("id") or doc.get("key") or i)
                digest = input_hash(text)
                results[i] = None if tokenizer_error else self._read_cached(doc_id, text, digest, truncated)
                if results[i] is None:
                    missing.append(i)
                if tokenizer_error:
                    results[i] = EmbeddingResult(doc_id, None, digest, "e5", self._revision,
                                                 self._model_card_hash, 0, MAX_TOKENS, truncated, True,
                                                 f"tokenizer-failure: {tokenizer_error}", text, self._artifact_hash)
                    missing.remove(i)
            if missing and self._model is not None:
                try:
                    encoded = self._model.encode(
                        [prepared[i][0] for i in missing], normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False, device="cpu",
                    )
                    for row_index, i in enumerate(missing):
                        vector = encoded[row_index].tolist() if hasattr(encoded[row_index], "tolist") else list(encoded[row_index])
                        text, truncated, _ = prepared[i]
                        result = EmbeddingResult(
                            str(rows[i].get("id") or rows[i].get("key") or i), _unit([float(x) for x in vector]),
                            input_hash(text), "e5", self._revision, self._model_card_hash, len(vector),
                            MAX_TOKENS, truncated, False, None, text, self._artifact_hash,
                        )
                        results[i] = result
                        self._write_cached(result)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {str(exc)[:240]}"
                    for i in missing:
                        text, truncated, _ = prepared[i]
                        results[i] = EmbeddingResult(
                            str(rows[i].get("id") or rows[i].get("key") or i), None, input_hash(text),
                            "e5", self._revision, self._model_card_hash, 0, MAX_TOKENS,
                            truncated, True, error, text, self._artifact_hash,
                        )
            elif missing:
                error = self._model_error or "model unavailable"
                for i in missing:
                    text, truncated, _ = prepared[i]
                    results[i] = EmbeddingResult(
                        str(rows[i].get("id") or rows[i].get("key") or i), None, input_hash(text),
                        "e5", self._revision, self._model_card_hash, 0, MAX_TOKENS,
                        truncated, True, error, text, self._artifact_hash,
                    )
            return [result for result in results if result is not None]
        if self.provider != "tfidf":
            return [self.embed(doc, document_id=str(doc.get("id") or doc.get("key") or i))
                    for i, doc in enumerate(rows)]
        # Per-document TF-IDF bases make unrelated one-token documents appear
        # identical.  Build one shared basis for every graph batch and keep its
        # vocabulary fingerprint in the provider revision/cache identity.
        prepared = [document_text(doc, token_limit=MAX_TOKENS, tokenizer=None) for doc in rows]
        texts = [item[0] for item in prepared]
        vectors = self._shared_tfidf(texts)
        corpus_revision = revision_hash(json.dumps(sorted(set(re.findall(r"[\w\u0080-\uffff]+", " ".join(texts).removeprefix(PREFIX).casefold()))), ensure_ascii=False))[:24]
        revision = f"tfidf-1@{corpus_revision[7:]}"
        results = []
        for i, (doc, (text, truncated), vector) in enumerate(zip(rows, prepared, vectors)):
            result = EmbeddingResult(
                str(doc.get("id") or doc.get("key") or i), vector, input_hash(text), "tfidf",
                revision, self._model_card_hash, len(vector), MAX_TOKENS, truncated, False, None, text,
                self._artifact_hash,
            )
            results.append(result)
        return results



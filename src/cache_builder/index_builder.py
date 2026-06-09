"""
FAISS index builder for embedding-based similarity search.

Builds two distinct indices:

Index 1 — AZ Spec Labels (~24K entries with labels):
    Embedded text = field label + SDTM variable hint
    Purpose = Tier 0 fuzzy matching (searched within same module at runtime)
    Key insight: Module filtering happens at SEARCH time, not at INDEX time.
    This means all labels are in one flat index, and the searcher filters
    results by module using the metadata.

Index 2 — CDISC Variables (~4,535 entries):
    Embedded text = domain + variable label + definition snippet
    Purpose = Tier 2 fallback (searched with domain filtering at runtime)

Both use IndexFlatIP (inner product) with L2-normalized vectors,
which is mathematically equivalent to cosine similarity.
"""

from __future__ import annotations
from pathlib import Path
import json

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

from config.settings import (
    MODELS,
    AZ_SPEC_FAISS_INDEX,
    AZ_SPEC_FAISS_METADATA,
    CDISC_FAISS_INDEX,
    CDISC_FAISS_METADATA,
)
from src.cache_builder.models import (
    SpecMappingEntry,
    SpecFAISSEntry,
    CorporateVariable,
    CDISCFAISSEntry,
)
from src.utils.text_normalizer import (
    build_embedding_text_for_spec_label,
    build_embedding_text_for_cdisc_variable,
)
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def load_embedding_model() -> SentenceTransformer:
    """
    Load the PubMedBERT sentence embedding model.

    First call downloads (~420 MB); subsequent calls use HuggingFace cache.
    Runs on CPU by default for deployment portability.
    """
    logger.info("Loading embedding model", model=MODELS.embedding_model, device=MODELS.device)
    model = SentenceTransformer(MODELS.embedding_model, device=MODELS.device)
    actual_dim = model.get_sentence_embedding_dimension()
    logger.info("Embedding model ready", dimension=actual_dim)

    if actual_dim != MODELS.embedding_dimension:
        logger.warning(
            "Embedding dimension mismatch — update config/settings.py",
            expected=MODELS.embedding_dimension,
            actual=actual_dim,
        )

    return model


def _encode_texts(
    model: SentenceTransformer,
    texts: list[str],
    desc: str,
) -> np.ndarray:
    """
    Encode texts into L2-normalized embeddings.

    Returns numpy array of shape (n, dimension) with unit-length vectors.
    With normalized vectors, inner product = cosine similarity.
    """
    logger.info(f"Encoding {len(texts):,} texts: {desc}")

    embeddings = model.encode(
        texts,
        batch_size=MODELS.embedding_batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )

    logger.info(f"Encoding complete: shape={embeddings.shape}, dtype={embeddings.dtype}")
    return embeddings.astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Index 1: AZ Spec Labels
# ─────────────────────────────────────────────────────────────────────────────

def build_az_spec_index(
    entries: list[SpecMappingEntry],
    model: SentenceTransformer,
) -> int:
    """
    Build FAISS Index 1 from AZ Spec source labels.

    Only indexes entries with non-empty source labels — these are the ones
    that can be matched against CRF field text.

    The embedding text for each entry is:
        "{normalized_label} maps to {sdtm_variable}"

    This gives the embedding model semantic context about what the label means
    in SDTM terms, improving similarity matches for synonymous labels.

    Args:
        entries: All parsed spec entries.
        model: Loaded SentenceTransformer.

    Returns:
        Number of vectors indexed.
    """
    # Filter to entries with non-empty labels
    indexable = [e for e in entries if e.source_label.strip()]

    if not indexable:
        logger.error("No entries with source labels found — cannot build AZ Spec index")
        return 0

    logger.info(
        "Building AZ Spec FAISS Index (Index 1)",
        total_entries=len(entries),
        indexable=len(indexable),
    )

    # Deduplicate by (module, label_normalized, sdtm_variable) to avoid
    # redundant vectors for the same field appearing in multiple contexts
    seen: set[tuple[str, str, str]] = set()
    unique_entries: list[SpecMappingEntry] = []

    for entry in indexable:
        dedup_key = (entry.source_module, entry.source_label_normalized, entry.sdtm_variable)
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique_entries.append(entry)

    logger.info(
        "After deduplication",
        before=len(indexable),
        after=len(unique_entries),
        removed=len(indexable) - len(unique_entries),
    )

    # Build embedding texts
    embedding_texts: list[str] = []
    metadata_records: list[dict] = []

    for idx, entry in enumerate(unique_entries):
        embed_text = build_embedding_text_for_spec_label(
            label=entry.source_label,
            module=entry.source_module,
            sdtm_variable=entry.sdtm_variable,
        )

        # Skip entries that produce empty embedding text
        if not embed_text.strip():
            continue

        embedding_texts.append(embed_text)
        metadata_records.append(
            SpecFAISSEntry(
                index_id=len(metadata_records),  # Sequential index into FAISS
                source_module=entry.source_module,
                source_label=entry.source_label,
                source_label_normalized=entry.source_label_normalized,
                sdtm_domain=entry.sdtm_domain,
                sdtm_variable=entry.sdtm_variable,
                sdtm_label=entry.sdtm_label,
                core=entry.core,
                is_supplemental=entry.is_supplemental,
                embedding_text=embed_text,
            ).model_dump()
        )

    # Generate embeddings
    embeddings = _encode_texts(model, embedding_texts, desc="AZ Spec Labels")

    # Build FAISS index (flat inner product = cosine with normalized vectors)
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    # Save to disk
    faiss.write_index(index, str(AZ_SPEC_FAISS_INDEX))
    logger.info("FAISS index written", path=str(AZ_SPEC_FAISS_INDEX))

    with open(AZ_SPEC_FAISS_METADATA, "w", encoding="utf-8") as f:
        json.dump(metadata_records, f, ensure_ascii=False)
    logger.info("FAISS metadata written", path=str(AZ_SPEC_FAISS_METADATA))

    logger.info(
        "AZ Spec Index 1 complete",
        vectors=index.ntotal,
        dimension=dimension,
        index_size_mb=AZ_SPEC_FAISS_INDEX.stat().st_size / (1024 * 1024),
    )

    return index.ntotal


# ─────────────────────────────────────────────────────────────────────────────
# Index 2: CDISC Variables
# ─────────────────────────────────────────────────────────────────────────────

def build_cdisc_index(
    variables: list[CorporateVariable],
    model: SentenceTransformer,
) -> int:
    """
    Build FAISS Index 2 from Corporate SDTM variable definitions.

    Embeds variable labels with domain context and CDISC notes for
    rich semantic representation. Used by Tier 2 for fallback matching
    when a field isn't covered by the AZ spec.

    The embedding text for each variable is:
        "{domain} domain ; {variable label} ; variable {name} ; {notes snippet}"

    Args:
        variables: All parsed CorporateVariable objects.
        model: Loaded SentenceTransformer.

    Returns:
        Number of vectors indexed.
    """
    # Filter to variables with labels
    indexable = [v for v in variables if v.label.strip()]

    if not indexable:
        logger.error("No variables with labels found — cannot build CDISC index")
        return 0

    logger.info(
        "Building CDISC FAISS Index (Index 2)",
        total_variables=len(variables),
        indexable=len(indexable),
    )

    # Build embedding texts
    embedding_texts: list[str] = []
    metadata_records: list[dict] = []

    for idx, var in enumerate(indexable):
        embed_text = build_embedding_text_for_cdisc_variable(
            dataset=var.dataset,
            variable=var.variable,
            label=var.label,
            cdisc_notes=var.cdisc_notes,
        )

        if not embed_text.strip():
            continue

        embedding_texts.append(embed_text)
        metadata_records.append(
            CDISCFAISSEntry(
                index_id=len(metadata_records),
                dataset=var.dataset,
                variable=var.variable,
                label=var.label,
                core=var.core,
                variable_type=var.variable_type,
                role=var.role,
                dataset_level=var.dataset_level,
                embedding_text=embed_text,
            ).model_dump()
        )

    # Generate embeddings
    embeddings = _encode_texts(model, embedding_texts, desc="CDISC Variables")

    # Build FAISS index
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    # Save to disk
    faiss.write_index(index, str(CDISC_FAISS_INDEX))
    logger.info("FAISS index written", path=str(CDISC_FAISS_INDEX))

    with open(CDISC_FAISS_METADATA, "w", encoding="utf-8") as f:
        json.dump(metadata_records, f, ensure_ascii=False)
    logger.info("FAISS metadata written", path=str(CDISC_FAISS_METADATA))

    logger.info(
        "CDISC Index 2 complete",
        vectors=index.ntotal,
        dimension=dimension,
        index_size_mb=CDISC_FAISS_INDEX.stat().st_size / (1024 * 1024),
    )

    return index.ntotal
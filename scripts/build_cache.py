"""
Cache Builder — One-time setup script for the aCRF Automation System.

Converts Excel specification files into runtime-optimised lookup caches
and FAISS embedding indices.

Usage:
    python scripts/build_cache.py

Expected runtime:
    First run: ~3-7 minutes (includes model download)
    Subsequent: ~2-4 minutes (model cached locally)

Outputs (in cache/ directory):
    az_spec_lookup.json         — {module: {normalized_label: [entries]}}
    az_spec_module_index.json   — Module → domain relationships
    az_spec_index.faiss         — FAISS Index 1 (AZ Spec labels, ~24K vectors)
    az_spec_metadata.json       — Metadata for each Index 1 vector
    cdisc_index.faiss           — FAISS Index 2 (CDISC variables, ~4.5K vectors)
    cdisc_metadata.json         — Metadata for each Index 2 vector
    ct_lookup.json              — Variable → codelist associations
    corporate_datasets.json     — Domain definitions
    corporate_variables.json    — All SDTM variables (validation ref)
    map_rules.json              — Mapping function definitions
"""
import truststore
truststore.inject_into_ssl()
import sys
import json
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    CACHE_DIR,
    AZ_SPEC_LOOKUP_CACHE,
    AZ_SPEC_MODULE_INDEX_CACHE,
    CT_LOOKUP_CACHE,
    CORPORATE_VARIABLES_CACHE,
    CORPORATE_DATASETS_CACHE,
    MAP_RULES_CACHE,
)
from src.utils.logging_config import setup_logging, get_logger
from src.cache_builder.spec_parser import parse_az_spec
from src.cache_builder.ct_parser import parse_controlled_terminology
from src.cache_builder.corporate_parser import parse_corporate_datasets, parse_corporate_variables
from src.cache_builder.map_rule_parser import parse_map_rules
from src.cache_builder.index_builder import (
    build_az_spec_index,
    build_cdisc_index,
    load_embedding_model,
)


def _build_spec_lookup_json(entries) -> dict[str, dict[str, list[dict]]]:
    """
    Build the exact-match lookup table for Tier 0.

    Structure:
        {
            "VS1": {
                "were vital signs collected": [
                    {<SpecMappingEntry as dict>}, ...
                ],
                "vital signs collection date": [...]
            },
            "AE": { ... }
        }

    This allows O(1) lookup: given (form_code, normalized_label) → mapping entries.
    Multiple entries per key handles one-to-many mappings (same label → multiple targets).
    """
    lookup: dict[str, dict[str, list[dict]]] = {}

    for entry in entries:
        # Only index entries with actual source labels
        if not entry.source_label.strip():
            continue

        module = entry.source_module
        norm_label = entry.source_label_normalized

        if not module or not norm_label:
            continue

        if module not in lookup:
            lookup[module] = {}

        if norm_label not in lookup[module]:
            lookup[module][norm_label] = []

        lookup[module][norm_label].append(entry.model_dump())

    return lookup


def main() -> None:
    """Execute the complete cache build pipeline."""
    setup_logging(level="INFO")
    logger = get_logger("build_cache")

    total_start = time.time()

    logger.info("╔" + "═" * 58 + "╗")
    logger.info("║   aCRF Automation System — Cache Builder                  ║")
    logger.info("╚" + "═" * 58 + "╝")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 1: Parse All Excel Sources
    # ═══════════════════════════════════════════════════════════════════════

    # ─── 1A: AZ RAW-to-SDTM Specification ───
    t0 = time.time()
    spec_entries, module_index = parse_az_spec()
    logger.info(f"  ⏱  Spec parsing: {time.time() - t0:.1f}s")
    print()

    # ─── 1B: Controlled Terminology ───
    t0 = time.time()
    ct_lookup = parse_controlled_terminology()
    logger.info(f"  ⏱  CT parsing: {time.time() - t0:.1f}s")
    print()

    # ─── 1C: Corporate Standards ───
    t0 = time.time()
    corp_datasets = parse_corporate_datasets()
    corp_variables = parse_corporate_variables()
    logger.info(f"  ⏱  Corporate parsing: {time.time() - t0:.1f}s")
    print()

    # ─── 1D: Map Rules ───
    t0 = time.time()
    map_rules = parse_map_rules()
    logger.info(f"  ⏱  Map rules parsing: {time.time() - t0:.1f}s")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2: Write JSON Caches
    # ═══════════════════════════════════════════════════════════════════════

    logger.info("Writing JSON caches to disk...")

    # Spec lookup (primary runtime cache)
    spec_lookup = _build_spec_lookup_json(spec_entries)
    with open(AZ_SPEC_LOOKUP_CACHE, "w", encoding="utf-8") as f:
        json.dump(spec_lookup, f, ensure_ascii=False)
    logger.info(
        "  ✓ Spec lookup",
        path=AZ_SPEC_LOOKUP_CACHE.name,
        modules=len(spec_lookup),
        total_labels=sum(len(labels) for labels in spec_lookup.values()),
    )

    # Module index
    with open(AZ_SPEC_MODULE_INDEX_CACHE, "w", encoding="utf-8") as f:
        json.dump(module_index.model_dump(), f, ensure_ascii=False, indent=2)
    logger.info("  ✓ Module index", path=AZ_SPEC_MODULE_INDEX_CACHE.name)

    # Controlled terminology
    ct_serialized = {k: v.model_dump() for k, v in ct_lookup.items()}
    with open(CT_LOOKUP_CACHE, "w", encoding="utf-8") as f:
        json.dump(ct_serialized, f, ensure_ascii=False)
    logger.info("  ✓ CT lookup", path=CT_LOOKUP_CACHE.name, entries=len(ct_serialized))

    # Corporate datasets
    with open(CORPORATE_DATASETS_CACHE, "w", encoding="utf-8") as f:
        json.dump([d.model_dump() for d in corp_datasets], f, ensure_ascii=False)
    logger.info("  ✓ Corporate datasets", path=CORPORATE_DATASETS_CACHE.name)

    # Corporate variables
    with open(CORPORATE_VARIABLES_CACHE, "w", encoding="utf-8") as f:
        json.dump([v.model_dump() for v in corp_variables], f, ensure_ascii=False)
    logger.info("  ✓ Corporate variables", path=CORPORATE_VARIABLES_CACHE.name)

    # Map rules
    with open(MAP_RULES_CACHE, "w", encoding="utf-8") as f:
        json.dump([r.model_dump() for r in map_rules], f, ensure_ascii=False)
    logger.info("  ✓ Map rules", path=MAP_RULES_CACHE.name)

    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 3: Build FAISS Embedding Indices
    # ═══════════════════════════════════════════════════════════════════════

    logger.info("Building FAISS embedding indices...")
    logger.info("  (First run downloads model — ~420 MB — subsequent runs use cache)")
    print()

    # Load model once, share between both index builds
    t0 = time.time()
    embedding_model = load_embedding_model()
    logger.info(f"  ⏱  Model load: {time.time() - t0:.1f}s")
    print()

    # Index 1: AZ Spec Labels
    t0 = time.time()
    spec_vectors = build_az_spec_index(spec_entries, model=embedding_model)
    logger.info(f"  ⏱  Index 1 build: {time.time() - t0:.1f}s")
    print()

    # Index 2: CDISC Variables
    t0 = time.time()
    cdisc_vectors = build_cdisc_index(corp_variables, model=embedding_model)
    logger.info(f"  ⏱  Index 2 build: {time.time() - t0:.1f}s")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 4: Validation & Summary
    # ═══════════════════════════════════════════════════════════════════════

    total_elapsed = time.time() - total_start

    logger.info("╔" + "═" * 58 + "╗")
    logger.info("║   BUILD COMPLETE                                          ║")
    logger.info("╚" + "═" * 58 + "╝")
    print()

    # Summary statistics
    logger.info("─── Summary ───")
    logger.info(f"  Total build time:           {total_elapsed:.1f}s")
    logger.info(f"  AZ Spec entries:            {len(spec_entries):,}")
    logger.info(f"  ├─ With source labels:      {sum(1 for e in spec_entries if e.source_label.strip()):,}")
    logger.info(f"  ├─ Supplemental (SUPP):     {sum(1 for e in spec_entries if e.is_supplemental):,}")
    logger.info(f"  └─ Unique modules:          {len(module_index.module_to_domains)}")
    logger.info(f"  CT variable→codelist:       {len(ct_lookup):,}")
    logger.info(f"  Corporate datasets:         {len(corp_datasets)}")
    logger.info(f"  Corporate variables:        {len(corp_variables):,}")
    logger.info(f"  FAISS Index 1 vectors:      {spec_vectors:,}")
    logger.info(f"  FAISS Index 2 vectors:      {cdisc_vectors:,}")
    print()

    # Library breakdown
    lib_counts: dict[str, int] = {}
    for e in spec_entries:
        lib = e.library if e.library else "(empty)"
        lib_counts[lib] = lib_counts.get(lib, 0) + 1

    logger.info("─── Library Breakdown ───")
    for lib, count in sorted(lib_counts.items(), key=lambda x: -x[1]):
        logger.info(f"  {lib}: {count:,}")
    print()

    # Top modules by entry count
    logger.info("─── Top 15 Modules (by entry count) ───")
    sorted_modules = sorted(
        module_index.module_to_entry_count.items(),
        key=lambda x: -x[1],
    )[:15]
    for mod, count in sorted_modules:
        domains = module_index.module_to_domains.get(mod, [])
        primary = module_index.module_to_primary_domain.get(mod, "?")
        logger.info(f"  {mod:12s} → {count:4d} entries │ primary domain: {primary:6s} │ all: {domains}")
    print()

    # Cache file sizes
    logger.info("─── Cache Files ───")
    total_size = 0
    for f in sorted(CACHE_DIR.iterdir()):
        size_bytes = f.stat().st_size
        total_size += size_bytes
        size_str = f"{size_bytes / (1024*1024):.2f} MB" if size_bytes > 1024*1024 else f"{size_bytes / 1024:.1f} KB"
        logger.info(f"  {f.name:30s} {size_str:>10s}")
    logger.info(f"  {'TOTAL':30s} {total_size / (1024*1024):.2f} MB")
    print()

    # Quick validation checks
    logger.info("─── Validation Checks ───")
    _run_validation_checks(spec_lookup, module_index, ct_lookup, corp_variables)


def _run_validation_checks(spec_lookup, module_index, ct_lookup, corp_variables):
    """Run basic sanity checks on the built caches."""
    logger = get_logger("validation")
    issues = []

    # Check 1: Common modules should exist
    expected_modules = ["AE", "CM", "DM", "VS", "MH", "EX"]
    for mod in expected_modules:
        # Check both exact and with numeric suffix (VS vs VS1)
        found = mod in spec_lookup or any(
            k.startswith(mod) for k in spec_lookup.keys()
        )
        if not found:
            issues.append(f"Expected module '{mod}' (or variant) not found in spec lookup")
        else:
            logger.info(f"  ✓ Module '{mod}' present")

    # Check 2: Spec lookup should have substantial content
    total_labels = sum(len(labels) for labels in spec_lookup.values())
    if total_labels < 1000:
        issues.append(f"Spec lookup has only {total_labels} label entries — expected 5000+")
    else:
        logger.info(f"  ✓ Spec lookup has {total_labels:,} label entries")

    # Check 3: CT lookup should cover common variables
    expected_ct = ["AE.AESEV", "VS.VSTESTCD", "DM.SEX"]
    for key in expected_ct:
        if key in ct_lookup:
            logger.info(f"  ✓ CT covers {key}")
        else:
            # Not critical — just informational
            logger.info(f"  ⚠ CT does not cover {key} (may be expected)")

    # Check 4: FAISS indices should be loadable
    import faiss
    try:
        idx1 = faiss.read_index(str(CACHE_DIR / "az_spec_index.faiss"))
        idx2 = faiss.read_index(str(CACHE_DIR / "cdisc_index.faiss"))
        logger.info(f"  ✓ FAISS Index 1 loads OK ({idx1.ntotal} vectors)")
        logger.info(f"  ✓ FAISS Index 2 loads OK ({idx2.ntotal} vectors)")
    except Exception as e:
        issues.append(f"FAISS index load failed: {e}")

    if issues:
        logger.warning("─── Issues Found ───")
        for issue in issues:
            logger.warning(f"  ⚠ {issue}")
    else:
        logger.info("  ✓ All validation checks passed")


if __name__ == "__main__":
    main()
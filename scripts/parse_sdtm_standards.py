"""
Parse AZ Corporate SDTM Standards into structured JSON caches.

Generates:
1. cache/sdtm_spec_by_dataset.json          — Full spec by dataset
2. cache/sdtm_dictionary_field_rules.json    — Dictionary-derived field rules by domain
3. cache/form_to_domain_map.json             — Form code → primary domain
4. cache/sdtm_label_to_variable.json         — Normalized label → variable by domain
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("cache")


# ─────────────────────────────────────────────────────────────────────────────
# FORM → DOMAIN MAP (study-specific knowledge)
# ─────────────────────────────────────────────────────────────────────────────

_FORM_TO_DOMAIN: dict[str, str] = {
    "AE": "AE", "SERAE": "AE", "AZAWSAE": "AE",
    "CM": "CM", "CM1": "CM",
    "MH": "MH",
    "VS": "VS", "VS1": "VS",
    "EG": "EG",
    "DM": "DM",
    "DS": "DS", "DS1": "DS",
    "LB": "LB", "LB1": "LB", "LB2": "LB", "LB3": "LB",
    "EX": "EX", "EX1": "EX",
    "PE": "PR",
    "HISS": "PR",
    "PREG": "LB",
    "SU_NIC": "SU",
    "ALLERH": "IS",
    "CONSENT": "DS", "CONSENT2": "DS",
    "CONSWD": "DS", "CONSWD1": "DS",
    "DOSDISC": "DS",
    "PARTOPT": "DS",
    "IE": "TI", "IE1": "TI",
    "VISIT": "SV", "VISIT2": "SV", "VISIT3": "SV",
    "VISITP": "SV",
    "CONTACT": "SV",
    "RESHISTE": "MH",
    "HEVENT": "FAHO", "HEVENT1": "FAHO",
    "CHCSS": "HO",
    "PULMTE": "FA",
    "EXACD": "CE", "EXACD1": "CE",
    "EXACATE": "CE",
    "HELMINTH": "FA",
    "INFDI": "FA",
    "INFRF": "FA",
    "INFSS": "CE",
    "LIVERDI": "FA",
    "LIVERSS": "CE",
    "LIVERRF": "CO",
    "OVERDOSE": "EC",
    "EXP": "EC",
    "PREGREP": "RP",
    "CGIC": "QS",
    "ASMPERF": "PR", "ASMPERF1": "PR", "ASMPERF2": "PR",
}


# ─────────────────────────────────────────────────────────────────────────────
# LABEL PATTERNS → VARIABLE KEYWORDS
# These help match CRF labels to SDTM variable labels
# ─────────────────────────────────────────────────────────────────────────────

_DICTIONARY_LABEL_PATTERNS: list[tuple[str, str]] = [
    # (CRF label pattern, SDTM label keyword to search for)
    (r"meddra\s*lowest\s*level\s*term\s*name", "lowest level term"),
    (r"meddra\s*lowest\s*level\s*term\s*code", "lowest level term code"),
    (r"meddra\s*preferred\s*term\s*name", "preferred term"),
    (r"meddra\s*preferred\s*term\s*code", "preferred term code"),
    (r"meddra\s*high\s*level\s*term\s*name", "high level term"),
    (r"meddra\s*high\s*level\s*term\s*code", "high level term code"),
    (r"meddra\s*high\s*level\s*group\s*term\s*name", "high level group term"),
    (r"meddra\s*high\s*level\s*group\s*term\s*code", "high level group term code"),
    (r"meddra\s*system\s*organ\s*class\s*name", "system organ class"),
    (r"meddra\s*system\s*organ\s*class\s*code", "system organ class code"),
    (r"meddra\s*system\s*organ\s*class\s*abbreviation", "system organ class"),
    (r"meddra\s*version", "meddra version"),
    (r"medication\s*code", "medication code"),
    (r"active\s*ingredient", "active ingredient"),
    (r"atc\s*code", "atc"),
    (r"atc\s*dictionary\s*text", "atc"),
    (r"drug\s*dictionary\s*version", "dictionary version"),
    (r"medication\s*dictionary\s*text", "modified"),
    (r"preferred\s*name", "standardized"),
    (r"pref\.?\s*group", "body system"),
]


def parse_sdtm_standards(excel_path: Path, sheet_name: str | int | None = None):
    """Parse SDTM Standards Excel and generate all cache files."""

    if not excel_path.exists():
        raise FileNotFoundError(f"Excel not found: {excel_path}")

    print("=" * 70)
    print("  PARSING AZ CORPORATE SDTM STANDARDS")
    print("=" * 70)
    print(f"\n  File: {excel_path}")

    # Find correct sheet
    xl = pd.ExcelFile(excel_path)
    if sheet_name is None:
        for name in xl.sheet_names:
            if "variable" in name.lower():
                sheet_name = name
                break
        if sheet_name is None:
            sheet_name = xl.sheet_names[0]

    print(f"  Sheet: {sheet_name}")

    df = pd.read_excel(excel_path, sheet_name=sheet_name)
    print(f"  Total rows: {len(df)}")
    print(f"  Columns: {list(df.columns)}")

    # Find columns
    def _get_col(name):
        for c in df.columns:
            if c.lower().strip() == name.lower():
                return c
        return None

    dataset_col = _get_col("Dataset")
    variable_col = _get_col("Variable")
    label_col = _get_col("Label")
    core_col = _get_col("Core")
    origin_col = _get_col("Origin")
    format_col = _get_col("Format")

    if not all([dataset_col, variable_col, label_col]):
        raise ValueError(f"Missing columns. Need: Dataset, Variable, Label. Found: {list(df.columns)}")

    # ─── Parse all rows into structured data ───
    spec_by_dataset: dict[str, list[dict]] = defaultdict(list)
    # domain → normalized_label → list of entries (multiple variables per label possible)
    label_to_variable: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for _, row in df.iterrows():
        dataset = str(row[dataset_col]).strip().upper() if pd.notna(row[dataset_col]) else ""
        variable = str(row[variable_col]).strip().upper() if pd.notna(row[variable_col]) else ""
        label = str(row[label_col]).strip() if pd.notna(row[label_col]) else ""

        if not dataset or not variable or not label:
            continue

        core = str(row[core_col]).strip() if core_col and pd.notna(row[core_col]) else ""
        origin = str(row[origin_col]).strip() if origin_col and pd.notna(row[origin_col]) else ""
        fmt = str(row[format_col]).strip() if format_col and pd.notna(row[format_col]) else ""

        # Extract codelist
        codelist = ""
        if fmt:
            cl_match = re.search(r"\(?(C\d+|AZC\d+)\)?", fmt)
            if cl_match:
                codelist = cl_match.group(1)

        is_supp = core.lower() == "supp"
        base_domain = dataset
        if dataset.startswith("SUPP"):
            base_domain = dataset[4:]
            is_supp = True

        entry = {
            "dataset": dataset,
            "variable": variable,
            "label": label,
            "label_normalized": label.lower().strip(),
            "core": core,
            "origin": origin,
            "codelist_code": codelist,
            "is_supplemental": is_supp,
            "base_domain": base_domain,
        }
        spec_by_dataset[dataset].append(entry)

        # Build label→variable index
        label_to_variable[base_domain][label.lower().strip()].append(entry)

    # ─── Generate dictionary field rules ───
    # For each domain, create rules for MedDRA/coding fields
    dict_rules: dict[str, dict[str, dict]] = {}

    for domain, label_map in label_to_variable.items():
        dict_rules[domain] = {}
        for norm_label, entries in label_map.items():
            # Check if this is a dictionary-derived field
            is_dict_field = any(kw in norm_label for kw in [
                "meddra", "lowest level", "preferred term", "high level",
                "system organ class", "body system", "dictionary",
                "medication code", "active ingredient", "atc code",
                "atc dictionary", "modified reported", "standardized",
            ])
            if is_dict_field and entries:
                # Take the first entry (primary mapping)
                primary = entries[0]
                dict_rules[domain][norm_label] = {
                    "variable": primary["variable"],
                    "label": primary["label"],
                    "is_supplemental": primary["is_supplemental"],
                    "codelist_code": primary["codelist_code"],
                    "core": primary["core"],
                    "base_domain": primary["base_domain"],
                    "dataset": primary["dataset"],
                    # Include all mappings if multiple
                    "all_mappings": [
                        {
                            "variable": e["variable"],
                            "dataset": e["dataset"],
                            "is_supplemental": e["is_supplemental"],
                        }
                        for e in entries
                    ],
                }

    # ─── Save outputs ───
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_DIR / "sdtm_spec_by_dataset.json", "w", encoding="utf-8") as f:
        json.dump(spec_by_dataset, f, indent=2, ensure_ascii=False)

    with open(OUTPUT_DIR / "sdtm_dictionary_field_rules.json", "w", encoding="utf-8") as f:
        json.dump(dict_rules, f, indent=2, ensure_ascii=False)

    with open(OUTPUT_DIR / "form_to_domain_map.json", "w", encoding="utf-8") as f:
        json.dump(_FORM_TO_DOMAIN, f, indent=2, ensure_ascii=False)

    with open(OUTPUT_DIR / "sdtm_label_to_variable.json", "w", encoding="utf-8") as f:
        # Convert defaultdict to regular dict for serialization
        serializable = {}
        for domain, labels in label_to_variable.items():
            serializable[domain] = {}
            for lbl, entries in labels.items():
                serializable[domain][lbl] = entries
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    # ─── Report ───
    total_dict_rules = sum(len(v) for v in dict_rules.values())
    total_labels = sum(len(v) for v in label_to_variable.values())

    print(f"\n  {'─' * 50}")
    print(f"  PARSING COMPLETE")
    print(f"  {'─' * 50}")
    print(f"  Datasets:                  {len(spec_by_dataset)}")
    print(f"  Total variables:           {sum(len(v) for v in spec_by_dataset.values())}")
    print(f"  Label→Variable entries:    {total_labels}")
    print(f"  Dictionary field rules:    {total_dict_rules}")
    print(f"  Form→Domain mappings:      {len(_FORM_TO_DOMAIN)}")

    print(f"\n  DICTIONARY RULES BY DOMAIN:")
    for domain in sorted(dict_rules.keys()):
        print(f"    {domain:<8} → {len(dict_rules[domain]):>3} rules")

    print(f"\n  OUTPUT FILES:")
    for f in OUTPUT_DIR.glob("sdtm_*"):
        size_kb = f.stat().st_size / 1024
        print(f"    ✅ {f.name:<40} ({size_kb:.1f} KB)")

    return spec_by_dataset, dict_rules, label_to_variable


if __name__ == "__main__":
    from config.settings import SDTM_STANDARDS_FILE
    excel = SDTM_STANDARDS_FILE if hasattr(sys.modules.get("config.settings", None), "SDTM_STANDARDS_FILE") else None

    if len(sys.argv) >= 2:
        excel = Path(sys.argv[1])
    elif excel is None:
        # Try common locations
        candidates = [
            Path("input/AZ_Corporate_SDTM_Standards.xlsx"),
            Path("data/AZ_Corporate_SDTM_Standards.xlsx"),
        ]
        for c in candidates:
            if c.exists():
                excel = c
                break

    if excel is None or not excel.exists():
        print("  ERROR: Provide Excel path as argument")
        sys.exit(1)

    sheet = sys.argv[2] if len(sys.argv) >= 3 else None
    parse_sdtm_standards(excel, sheet)
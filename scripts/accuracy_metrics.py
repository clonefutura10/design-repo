"""
Accuracy & Coverage Metrics Generator — No reference PDF needed.

Uses the pipeline's own resolution results to compute demo-ready metrics.
"""

import json
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass, field

import sys
sys.path.insert(0, ".")

from src.pdf_parser.extractor import extract_crf
from src.resolution.noise_filter import is_noise_field
from src.resolution.tier0_rules import Tier0Rules, _reset_usage_tracking
from src.resolution.tier1_not_submitted import Tier1NotSubmitted
from src.resolution.models import ResolutionResult, ResolutionTier


# =============================================================================
# CONFIG
# =============================================================================

BLANK_CRF = Path("input/blank_crf_clean.pdf")
OUTPUT_METRICS = Path("output/accuracy_metrics.json")


def main():
    print("=" * 60)
    print("  GENERATING ACCURACY & COVERAGE METRICS")
    print("=" * 60)

    # ── Run pipeline ──
    parse_result = extract_crf(BLANK_CRF)
    all_fields = parse_result.all_fields
    data_fields = [f for f in all_fields if not is_noise_field(f)]

    tier0 = Tier0Rules()
    tier1 = Tier1NotSubmitted()
    _reset_usage_tracking()

    results: list[ResolutionResult] = []
    for fld in data_fields:
        t1 = tier1.resolve(field_label=fld.field_label, form_code=fld.form_code)
        if t1:
            results.append(t1)
            continue
        t0 = tier0.resolve(form_code=fld.form_code, field_label=fld.field_label)
        if t0:
            results.append(t0)
            continue
        results.append(ResolutionResult(
            form_code=fld.form_code,
            field_label=fld.field_label,
            resolved=False,
            tier=ResolutionTier.UNRESOLVED,
            confidence=0.0,
            sdtm_domain="",
            sdtm_variable="",
        ))

    # ══════════════════════════════════════════════════════════════
    # METRICS COMPUTATION
    # ══════════════════════════════════════════════════════════════

    # --- Per-domain metrics ---
    domain_stats: dict[str, dict] = defaultdict(lambda: {
        "total_fields": 0,
        "resolved": 0,
        "unresolved": 0,
        "not_submitted": 0,
        "variables_used": set(),
        "forms_covered": set(),
        "confidence_sum": 0.0,
    })

    # --- Per-form metrics ---
    form_stats: dict[str, dict] = defaultdict(lambda: {
        "total_fields": 0,
        "resolved": 0,
        "unresolved": 0,
        "primary_domain": "",
        "domains_involved": set(),
    })

    # --- Unresolved tracking ---
    unresolved_by_form: dict[str, list] = defaultdict(list)
    unresolved_labels: list[str] = []

    for fld, res in zip(data_fields, results):
        form = fld.form_code or "UNKNOWN"
        form_stats[form]["total_fields"] += 1

        if res.resolved:
            domain = res.sdtm_domain.upper() if res.sdtm_domain else "UNKNOWN"
            # Strip SUPP prefix for base domain stats
            base_domain = domain[4:] if domain.startswith("SUPP") else domain

            domain_stats[base_domain]["total_fields"] += 1
            domain_stats[base_domain]["resolved"] += 1
            domain_stats[base_domain]["variables_used"].add(
                f"{res.sdtm_domain}.{res.sdtm_variable}"
            )
            domain_stats[base_domain]["forms_covered"].add(form)
            domain_stats[base_domain]["confidence_sum"] += res.confidence

            form_stats[form]["resolved"] += 1
            form_stats[form]["domains_involved"].add(base_domain)

            if res.is_not_submitted:
                domain_stats[base_domain]["not_submitted"] += 1
        else:
            form_stats[form]["unresolved"] += 1
            unresolved_by_form[form].append(fld.field_label)
            unresolved_labels.append(fld.field_label)

    # --- Compute derived metrics ---
    total_fields = len(data_fields)
    total_resolved = sum(1 for r in results if r.resolved)
    total_unresolved = total_fields - total_resolved

    # Domain accuracy ranking
    domain_accuracy = {}
    for domain, stats in domain_stats.items():
        total = stats["resolved"] + stats.get("unresolved_from_domain", 0)
        if stats["resolved"] > 0:
            avg_confidence = stats["confidence_sum"] / stats["resolved"]
        else:
            avg_confidence = 0.0
        domain_accuracy[domain] = {
            "domain": domain,
            "fields_mapped": stats["resolved"],
            "unique_variables": len(stats["variables_used"]),
            "forms_covered": len(stats["forms_covered"]),
            "avg_confidence": round(avg_confidence * 100, 1),
            "not_submitted": stats["not_submitted"],
        }

    # Sort by fields mapped
    sorted_domains = sorted(
        domain_accuracy.values(),
        key=lambda x: x["fields_mapped"],
        reverse=True,
    )

    # Form accuracy ranking
    form_accuracy = {}
    for form, stats in form_stats.items():
        total = stats["total_fields"]
        resolved = stats["resolved"]
        rate = (resolved / total * 100) if total > 0 else 0
        form_accuracy[form] = {
            "form": form,
            "total_fields": total,
            "resolved": resolved,
            "unresolved": stats["unresolved"],
            "resolution_rate": round(rate, 1),
            "domains_involved": sorted(stats["domains_involved"]),
        }

    sorted_forms = sorted(
        form_accuracy.values(),
        key=lambda x: x["resolution_rate"],
        reverse=True,
    )

    # Unresolved pattern analysis
    unresolved_counter = Counter(unresolved_labels)
    common_unresolved = [
        {"label": label, "count": count}
        for label, count in unresolved_counter.most_common(20)
    ]

    # Forms needing improvement
    forms_needing_work = [
        f for f in sorted_forms
        if f["resolution_rate"] < 85 and f["total_fields"] >= 3
    ]

    # ══════════════════════════════════════════════════════════════
    # BUILD REPORT
    # ══════════════════════════════════════════════════════════════

    report = {
        "summary": {
            "total_crf_pages": parse_result.total_pdf_pages,
            "total_forms": len(form_stats),
            "total_fields_analyzed": total_fields,
            "total_resolved": total_resolved,
            "total_unresolved": total_unresolved,
            "resolution_rate": round(total_resolved / total_fields * 100, 1),
            "domains_covered": len(domain_accuracy),
            "unique_variables_assigned": len(set(
                f"{r.sdtm_domain}.{r.sdtm_variable}"
                for r in results if r.resolved and r.sdtm_variable
            )),
        },
        "domain_breakdown": sorted_domains,
        "top_performing_domains": [
            d for d in sorted_domains[:5] if d["fields_mapped"] >= 10
        ],
        "domain_coverage_summary": {
            "fully_automated": [d["domain"] for d in sorted_domains if d["avg_confidence"] >= 95],
            "high_confidence": [d["domain"] for d in sorted_domains if 90 <= d["avg_confidence"] < 95],
            "needs_improvement": [d["domain"] for d in sorted_domains if d["avg_confidence"] < 90 and d["fields_mapped"] > 0],
        },
        "form_breakdown": sorted_forms,
        "top_performing_forms": [f for f in sorted_forms[:10] if f["total_fields"] >= 3],
        "forms_needing_improvement": forms_needing_work[:10],
        "unresolved_analysis": {
            "total_unresolved": total_unresolved,
            "unique_unresolved_labels": len(unresolved_counter),
            "most_common_unresolved": common_unresolved,
            "unresolved_by_form": {
                form: labels[:5]
                for form, labels in sorted(
                    unresolved_by_form.items(),
                    key=lambda x: len(x[1]),
                    reverse=True,
                )[:10]
            },
        },
        "improvement_suggestions": [
            {
                "priority": "HIGH",
                "area": f"Form: {f['form']}",
                "current_rate": f"{f['resolution_rate']}%",
                "unresolved_fields": f["unresolved"],
                "suggestion": "Add study-specific patterns or use LLM context for complex multi-domain forms",
            }
            for f in forms_needing_work[:5]
        ],
    }

    # ══════════════════════════════════════════════════════════════
    # PRINT SUMMARY
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'─' * 60}")
    print(f"  OVERALL METRICS")
    print(f"{'─' * 60}")
    print(f"  Total CRF Pages:          {report['summary']['total_crf_pages']}")
    print(f"  Total Forms:              {report['summary']['total_forms']}")
    print(f"  Total Fields:             {report['summary']['total_fields_analyzed']}")
    print(f"  Resolved:                 {report['summary']['total_resolved']}")
    print(f"  Unresolved:               {report['summary']['total_unresolved']}")
    print(f"  Resolution Rate:          {report['summary']['resolution_rate']}%")
    print(f"  Domains Covered:          {report['summary']['domains_covered']}")
    print(f"  Unique Variables Used:    {report['summary']['unique_variables_assigned']}")

    print(f"\n{'─' * 60}")
    print(f"  DOMAIN BREAKDOWN (by fields mapped)")
    print(f"{'─' * 60}")
    print(f"  {'Domain':<10} {'Mapped':>7} {'Variables':>10} {'Forms':>6} {'Confidence':>11}")
    print(f"  {'─'*10} {'─'*7} {'─'*10} {'─'*6} {'─'*11}")
    for d in sorted_domains:
        if d["fields_mapped"] >= 5:
            print(f"  {d['domain']:<10} {d['fields_mapped']:>7} {d['unique_variables']:>10} {d['forms_covered']:>6} {d['avg_confidence']:>10.1f}%")

    print(f"\n{'─' * 60}")
    print(f"  FORM BREAKDOWN (top 15 by resolution rate)")
    print(f"{'─' * 60}")
    print(f"  {'Form':<15} {'Total':>6} {'Resolved':>9} {'Rate':>7} {'Domains'}")
    print(f"  {'─'*15} {'─'*6} {'─'*9} {'─'*7} {'─'*20}")
    for f in sorted_forms[:15]:
        if f["total_fields"] >= 3:
            domains_str = ", ".join(f["domains_involved"][:3])
            print(f"  {f['form']:<15} {f['total_fields']:>6} {f['resolved']:>9} {f['resolution_rate']:>6.1f}% {domains_str}")

    print(f"\n{'─' * 60}")
    print(f"  FORMS NEEDING IMPROVEMENT")
    print(f"{'─' * 60}")
    for f in forms_needing_work[:8]:
        print(f"  ✗ {f['form']:<15} {f['resolution_rate']:>5.1f}% ({f['unresolved']} unresolved)")
        if f["form"] in unresolved_by_form:
            for label in unresolved_by_form[f["form"]][:3]:
                print(f"      → \"{label}\"")

    print(f"\n{'─' * 60}")
    print(f"  MOST COMMON UNRESOLVED LABELS")
    print(f"{'─' * 60}")
    for item in common_unresolved[:12]:
        print(f"  [{item['count']:>3}x] \"{item['label']}\"")

    print(f"\n{'─' * 60}")
    print(f"  IMPROVEMENT SUGGESTIONS")
    print(f"{'─' * 60}")
    for sug in report["improvement_suggestions"]:
        print(f"  [{sug['priority']}] {sug['area']} — {sug['current_rate']} → {sug['suggestion']}")

    print("\n" + "=" * 60)

    # Save JSON
    # Convert sets to lists for JSON serialization
    OUTPUT_METRICS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_METRICS, "w") as f:
        json.dump(report, f, indent=2, default=list)
    print(f"\n  Metrics saved: {OUTPUT_METRICS}")


if __name__ == "__main__":
    main()
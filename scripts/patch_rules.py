"""
patch_rules.py — Generates additional rule tuples for tier0_rules.py.

Run this to see the rules, then paste them into your _RULES list in
src/resolution/tier0_rules.py.

Usage:
    python scripts/patch_rules.py
"""

# ═══════════════════════════════════════════════════════════════════════════════
# NEW RULES TO ADD TO _RULES in src/resolution/tier0_rules.py
#
# Format: (form_pattern, label_pattern, domain, variable, codelist, is_supplemental)
# ═══════════════════════════════════════════════════════════════════════════════

ADDITIONAL_RULES = [
    # ─────────────────────────────────────────────────────────────────────────
    # AE — Adverse Events (additional patterns)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^AE\d*$", r"^start\s*date$", "AE", "AESTDTC", "", False),
    (r"^AE\d*$", r"^end\s*date$", "AE", "AEENDTC", "", False),
    (r"^AE\d*$", r"were\s*any\s*adverse\s*events?\s*(experienced|reported)", "AE", "AEOCCUR", "", False),
    (r"^AE\d*$", r"ae\s*required\s*treatment", "AE", "AECONTRT", "", True),
    (r"^AE\d*$", r"date\s*ae\s*met\s*criteria\s*for\s*sae", "AE", "AESDT", "", True),
    (r"^AE\d*$", r"date\s*investigator\s*became\s*aware", "AE", "AEAWDT", "", True),
    (r"^AE\d*$", r"immediately\s*life.?threaten", "AE", "AESLIFE", "", False),
    (r"^AE\d*$", r"requires?\s*in.?patient\s*hospitali", "AE", "AESHOSP", "", False),
    (r"^AE\d*$", r"results?\s*in\s*persistent.*disab", "AE", "AESDISAB", "", False),
    (r"^AE\d*$", r"congenital\s*anomaly|birth\s*defect", "AE", "AESCONG", "", False),
    (r"^AE\d*$", r"other.*important\s*medical\s*event", "AE", "AESMIE", "", False),
    (r"^AE\d*$", r"results?\s*in\s*death", "AE", "AESDTH", "", False),
    (r"^AE\d*$", r"serious\s*criteria.*results?\s*in\s*death", "AE", "AESDTH", "", False),
    (r"^AE\d*$", r"related\s*to\s*(?:bronchoscopy|ct\s*scan|study\s*procedure|other)", "AE", "AERELSP", "", True),
    (r"^AE\d*$", r"related\s*to\s*other\s*study\s*procedure,?\s*specify", "AE", "AERELSPN", "", True),
    (r"^AE\d*$", r"sae\s*description", "AE", "SAECOMM", "", True),

    # ─────────────────────────────────────────────────────────────────────────
    # CE — Clinical Events (exacerbations)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^CE\d*$", r"was\s*there\s*any\s*new.*exacerbation", "CE", "CEOCCUR", "", False),
    (r"^CE\d*$", r"exacerbation.*start\s*date", "CE", "CESTDTC", "", False),
    (r"^CE\d*$", r"exacerbation.*end\s*date", "CE", "CEENDTC", "", False),
    (r"^CE\d*$", r"ongoing\s*at\s*study\s*end", "CE", "CEONGO", "", False),
    (r"^CE\d*$", r"did\s*the\s*exacerbation\s*visit\s*occur", "CE", "CEEXVST", "", True),
    (r"^CE\d*$", r"sputum\s*purulence", "CE", "CETERM", "", False),
    (r"^CE\d*$", r"sputum\s*volume\s*increase", "CE", "CETERM", "", False),
    (r"^CE\d*$", r"dyspnea.*exercise\s*intolerance", "CE", "CETERM", "", False),
    (r"^CE\d*$", r"fatigue.*malaise", "CE", "CETERM", "", False),
    (r"^CE\d*$", r"other\s*symptoms?/findings?$", "CE", "CETERM", "", False),
    (r"^CE\d*$", r"other\s*symptoms?/findings?,?\s*specify", "CE", "CEMODIFY", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # DS — Disposition (additional patterns)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^DS\d*$", r"informed\s*consent\s*withdrawal\s*date", "DS", "DSSTDTC", "", False),
    (r"^DS\d*$", r"what\s*was\s*the\s*protocol\s*milestone", "DS", "DSTERM", "", False),
    (r"^DS\d*$", r"consent\s*withdrawal\s*category", "DS", "DSSPFY", "", True),
    (r"^DS\d*$", r"other\s*status,?\s*specify", "DS", "DSMODIFY", "", False),
    (r"^DS\d*$", r"completion\s*or\s*discontinuation\s*date", "DS", "DSSTDTC", "", False),
    (r"^DS\d*$", r"re.?signed\s*informed\s*consent$", "DS", "DSOCCUR", "", False),
    (r"^DS\d*$", r"csp\s*version.*re.?signed", "DS", "DSSPFY", "", True),
    (r"^DS\d*$", r"date\s*of\s*re.?signed", "DS", "DSSTDTC", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # LB — Laboratory (additional patterns)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^LB\d*$", r"^collection\s*date$", "LB", "LBDTC", "", False),
    (r"^LB\d*$", r"^collection\s*time", "LB", "LBTM", "", False),
    (r"^LB\d*$", r"if\s*abnormal.*clinically\s*significant,?\s*specify", "LB", "LBCLSIG", "", True),
    (r"^LB\d*$", r"^if\s*no,?\s*please\s*specify\s*the\s*reason", "LB", "LBREASND", "", False),
    # Individual test names → LBORRES (the test name IS the label, result is LBORRES)
    (r"^LB\d*$", r"^hb$|^hemoglobin$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"leukocyte\s*count", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"eosinophils?\s*(?:differential\s*count|%|\()", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"neutrophils?\s*(?:differential\s*count|%|\()", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"basophils?\s*(?:differential\s*count|%|\()", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"platelet\s*count", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"haematocrit|hematocrit", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"^rbc$|red\s*blood\s*cell", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"creatinine", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"bilirubin.*total", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"alanine\s*aminotransferase|^alt\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"aspartate\s*aminotransferase|^ast\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"alkaline\s*phosphatase|^alp\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"gamma.?glutamyl\s*transferase|^ggt\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"^albumin$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"potassium\s*\(?k\)?", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"sodium\s*\(?na\)?", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"blood\s*urea\s*nitrogen|^bun\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"cholesterol.*total", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"activated\s*partial\s*thromboplastin|^aptt\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"international\s*normalized\s*ratio|^inr\)?$", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"^appearance\s*and\s*colo", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"microscopic\s*(?:red|white)\s*blood", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"occult\s*blood", "LB", "LBORRES", "", False),
    (r"^LB\d*$", r"pregnancy\s*result", "LB", "LBORRES", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # RE — Respiratory (Spirometry, FeNO)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^RE\d*$", r"was\s*(?:the\s*)?spirometry\s*performed", "RE", "REOCCUR", "", False),
    (r"^RE\d*$", r"was\s*feno\s*performed", "RE", "REOCCUR", "", False),
    (r"^RE\d*$", r"^examination\s*date$", "RE", "REDTC", "", False),
    (r"^RE\d*$", r"fev1\s*%\s*predicted", "RE", "REORRES", "", False),
    (r"^RE\d*$", r"fvc\s*%\s*predicted", "RE", "REORRES", "", False),
    (r"^RE\d*$", r"fef\s*25.?75\s*%?\s*predicted", "RE", "REORRES", "", False),
    (r"^RE\d*$", r"fractional\s*exhaled\s*nitric\s*oxide", "RE", "REORRES", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # BE — Biospecimen Events (sample collection)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^BE\d*$", r"was\s*(?:the\s*)?(?:external\s*data\s*)?sample\s*collected", "BE", "BEOCCUR", "", False),
    (r"^BE\d*$", r"whether\s*collect\s*the\s*sample", "BE", "BEOCCUR", "", False),
    (r"^BE\d*$", r"^collection\s*date$", "BE", "BEDTC", "", False),
    (r"^BE\d*$", r"^collection\s*time", "BE", "BETM", "", False),
    (r"^BE\d*$", r"^accession\s*number", "BE", "BEREFID", "", False),
    (r"^BE\d*$", r"blood\s*sample\s*for", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"sputum\s*sample\s*for", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"urine\s*sample\s*for", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"bronchoalveolar\s*lavage", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"endobronchial\s*biopsy", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"endobronchial\s*brushing", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"nasal\s*brushing", "BE", "BESPEC", "", False),
    (r"^BE\d*$", r"nasal\s*lining\s*fluid", "BE", "BESPEC", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # PR — Procedures (additional patterns)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^PR\d*$", r"was\s*(?:the\s*)?ct\s*scan\s*performed", "PR", "PROCCUR", "", False),
    (r"^PR\d*$", r"^scan\s*date$", "PR", "PRSTDTC", "", False),
    (r"^PR\d*$", r"^start\s*date$", "PR", "PRSTDTC", "", False),
    (r"^PR\d*$", r"^end\s*date$", "PR", "PRENDTC", "", False),
    (r"^PR\d*$", r"what\s*kind\s*of\s*infection", "PR", "PRMODIFY", "", False),
    (r"^PR\d*$", r"other,?\s*specify$", "PR", "PRMODIFY", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # DD — Death Details (additional patterns)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^DD\d*$", r"^death\s*date$", "DD", "DTHDT", "", False),
    (r"^DD\d*$", r"(?:primary\s*)?cause\s*of\s*death", "DD", "DTHCAUS", "", False),
    (r"^DD\d*$", r"related\s*to\s*disease\s*under", "DD", "DTHREL", "", True),

    # ─────────────────────────────────────────────────────────────────────────
    # SC — Subject Characteristics / Enrolment
    # ─────────────────────────────────────────────────────────────────────────
    (r"^SC\d*$", r"what\s*(?:is|was)\s*the\s*enrol", "SC", "SCORRES", "", False),
    (r"^SC\d*$", r"what\s*(?:is|was)\s*the\s*study\s*code", "SC", "SCORRES", "", False),
    (r"^SC\d*$", r"what\s*was\s*the\s*group\s*allocation", "SC", "SCORRES", "", False),
    (r"^SC\d*$", r"bronchiectasis\s*cohort", "SC", "SCORRES", "", False),
    (r"^SC\d*$", r"healthy\s*control\s*cohort", "SC", "SCORRES", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # DM — Demographics (additional)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^DM\d*$", r"other\s*race,?\s*specify", "DM", "RACEOTH", "", True),

    # ─────────────────────────────────────────────────────────────────────────
    # RP — Reproductive (additional)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^RP\d*$", r"was\s*(?:the\s*)?child\s*bearing\s*potential", "RP", "RPOCCUR", "", False),
    (r"^RP\d*$", r"was\s*(?:the\s*)?pregnancy\s*test\s*performed", "RP", "RPOCCUR", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # CM — Concomitant Medications (additional)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^CM\d*$", r"^start\s*date$", "CM", "CMSTDTC", "", False),
    (r"^CM\d*$", r"^end\s*date$", "CM", "CMENDTC", "", False),
    (r"^CM\d*$", r"action\s*taken\s*(?:at\s*)?end\s*of\s*medication", "CM", "CMACTTK", "", False),
    (r"^CM\d*$", r"(?:therapy|medication)\s*reason|reason\s*for\s*(?:therapy|use)", "CM", "CMINDC", "", False),
    (r"^CM\d*$", r"(?:other|if\s*other)\s*(?:frequency|route|unit),?\s*specify", "CM", "CMMODIFY", "", True),
    (r"^CM\d*$", r"dose\s*adjusted\s*to\s*new", "CM", "CMDOSE", "", False),
    (r"^CM\d*$", r"historical\s*exacerbation", "CM", "CMINDC", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # HO — Healthcare Encounters (for HRU forms)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^HO\d*$", r"any\s*emergency\s*room\s*visit", "HO", "HOOCCUR", "", False),
    (r"^HO\d*$", r"any\s*unscheduled\s*physician\s*visit", "HO", "HOOCCUR", "", False),
    (r"^HO\d*$", r"was\s*unscheduled\s*visit\s*assessment\s*performed", "HO", "HOOCCUR", "", False),

    # ─────────────────────────────────────────────────────────────────────────
    # Generic date patterns (work across multiple domains via inference)
    # ─────────────────────────────────────────────────────────────────────────
    (r"^VS\d*$", r"^examination\s*date$", "VS", "VSDTC", "", False),
    (r"^PE\d*$", r"^examination\s*date$", "PE", "PEDTC", "", False),
    (r"^SV\d*$", r"^visit\s*date$", "SV", "SVSTDTC", "", False),
]


def main():
    """Print the rules for manual insertion."""
    print("=" * 70)
    print(f"  ADDITIONAL RULES TO ADD ({len(ADDITIONAL_RULES)} rules)")
    print("=" * 70)
    print()
    print("Copy the following block and paste it at the END of your _RULES")
    print("list in src/resolution/tier0_rules.py (before the closing bracket):")
    print()
    print("    # ═══════════════════════════════════════════════════════════")
    print("    # ADDITIONAL RULES — Generated by patch_rules.py")
    print("    # ═══════════════════════════════════════════════════════════")
    for rule in ADDITIONAL_RULES:
        print(f"    {rule},")
    print()
    print("=" * 70)
    print(f"Total new rules: {len(ADDITIONAL_RULES)}")
    print()

    # Also verify they compile
    import re
    errors = 0
    for i, (form_pat, label_pat, domain, var, cl, supp) in enumerate(ADDITIONAL_RULES):
        try:
            re.compile(form_pat)
            re.compile(label_pat, re.IGNORECASE)
        except re.error as e:
            print(f"  ERROR in rule {i}: {e}")
            print(f"    form_pat: {form_pat}")
            print(f"    label_pat: {label_pat}")
            errors += 1

    if errors == 0:
        print("All rules compile successfully.")
    else:
        print(f"\n{errors} rules have regex errors — fix before adding!")


if __name__ == "__main__":
    main()
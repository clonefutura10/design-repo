"""
Findings domain qualifier resolver.

For Findings domains (VS, LB, EG), annotated CRFs include TESTCD where-clauses
to identify which test record a variable belongs to. E.g.:
    VS.VSORRES → VS.VSORRES / VSTESTCD="WEIGHT"
    LB.LBORRES → LB.LBORRES / LBTESTCD="ALB"

This resolver examines the field label and context labels to determine the
appropriate test code qualifier and appends it to the ResolutionResult.
"""

from __future__ import annotations
import re
from src.resolution.models import ResolutionResult


# Variables in Findings domains that need TESTCD qualifiers
_FINDINGS_QUALIFIER_VARS: dict[str, set[str]] = {
    "VS": {"VSORRES", "VSORRESU", "VSSTRESN", "VSSTRESC", "VSSTRESU",
           "VSNRIND", "VSSTAT", "VSREASND"},
    "LB": {"LBORRES", "LBORRESU", "LBSTRESN", "LBSTRESC", "LBSTRESU",
           "LBNRIND", "LBORNRHI", "LBORNRLO", "LBSTAT", "LBREASND"},
    "EG": {"EGORRES", "EGORRESU", "EGSTRESN", "EGSTRESC", "EGSTRESU",
           "EGNRIND", "EGSTAT", "EGREASND"},
}

_DOMAIN_TESTCD_VAR = {"VS": "VSTESTCD", "LB": "LBTESTCD", "EG": "EGTESTCD"}

_VS_TESTS: dict[str, str] = {
    "weight": "WEIGHT", "body weight": "WEIGHT",
    "height": "HEIGHT", "body height": "HEIGHT",
    "systolic blood pressure": "SYSBP", "systolic bp": "SYSBP",
    "systolic pressure": "SYSBP", "sbp": "SYSBP",
    "diastolic blood pressure": "DIABP", "diastolic bp": "DIABP",
    "diastolic pressure": "DIABP", "dbp": "DIABP",
    "pulse rate": "PULSE", "pulse": "PULSE", "heart rate": "PULSE",
    "temperature": "TEMP", "body temperature": "TEMP",
    "oral temperature": "TEMP", "axillary temperature": "TEMP",
    "tympanic temperature": "TEMP",
    "respiratory rate": "RESP", "respiration rate": "RESP",
    "body mass index": "BMI", "bmi": "BMI",
    "oxygen saturation": "OXYSAT", "o2 saturation": "OXYSAT", "spo2": "OXYSAT",
    "waist circumference": "WSTCIR", "hip circumference": "HIPCIR",
    "waist-to-hip ratio": "WHRAT",
    "upper arm circumference": "ARMCIR",
    "ecog performance status": "ECOG", "ecog": "ECOG",
    "performance status": "ECOG",
    "pain score": "PAIN", "pain": "PAIN",
    "forced expiratory volume": "FEV1", "fev1": "FEV1",
    "forced vital capacity": "FVC", "fvc": "FVC",
    "visual acuity": "VISACU",
    "intraocular pressure": "IOP",
}

_LB_TESTS: dict[str, str] = {
    "albumin": "ALB",
    "alkaline phosphatase": "ALP", "alp": "ALP",
    "alanine aminotransferase": "ALT", "alt": "ALT", "sgpt": "ALT",
    "aspartate aminotransferase": "AST", "ast": "AST", "sgot": "AST",
    "bilirubin": "BILI", "total bilirubin": "BILI",
    "direct bilirubin": "BILIDIR", "indirect bilirubin": "BILIINDR",
    "blood urea nitrogen": "BUN", "bun": "BUN", "urea": "BUN",
    "calcium": "CA", "chloride": "CL",
    "cholesterol": "CHOL", "total cholesterol": "CHOL",
    "creatinine": "CREAT", "serum creatinine": "CREAT",
    "creatine kinase": "CK", "ck": "CK", "cpk": "CK",
    "c-reactive protein": "CRP", "crp": "CRP",
    "erythrocytes": "RBC", "red blood cells": "RBC", "rbc": "RBC",
    "ferritin": "FERRITN",
    "gamma-glutamyltransferase": "GGT", "ggt": "GGT", "gamma gt": "GGT",
    "glucose": "GLUC", "blood glucose": "GLUC", "fasting glucose": "GLUCF",
    "haematocrit": "HCT", "hematocrit": "HCT", "hct": "HCT",
    "haemoglobin": "HGB", "hemoglobin": "HGB", "hgb": "HGB", "hb": "HGB",
    "hdl": "HDL", "hdl cholesterol": "HDL", "high-density lipoprotein": "HDL",
    "ldl": "LDL", "ldl cholesterol": "LDL", "low-density lipoprotein": "LDL",
    "insulin": "INS",
    "international normalised ratio": "INR", "inr": "INR",
    "iron": "FE", "serum iron": "FE",
    "lactate dehydrogenase": "LDH", "ldh": "LDH",
    "leukocytes": "WBC", "white blood cells": "WBC", "wbc": "WBC",
    "lymphocytes": "LYMPH", "lymphocyte": "LYMPH",
    "magnesium": "MG",
    "monocytes": "MONO", "monocyte": "MONO",
    "neutrophils": "NEUT", "neutrophil": "NEUT",
    "eosinophils": "EOS", "eosinophil": "EOS",
    "basophils": "BASO", "basophil": "BASO",
    "phosphate": "PHOS", "phosphorus": "PHOS",
    "platelets": "PLAT", "platelet count": "PLAT", "plt": "PLAT",
    "potassium": "K", "serum potassium": "K",
    "prolactin": "PRL",
    "protein": "PROT", "total protein": "PROT",
    "prothrombin time": "PT", "pt": "PT",
    "activated partial thromboplastin time": "APTT", "aptt": "APTT", "ptt": "APTT",
    "sodium": "NA", "serum sodium": "NA",
    "thyrotropin": "TSH", "tsh": "TSH",
    "triglycerides": "TRIG", "triglyceride": "TRIG",
    "triiodothyronine": "T3", "t3": "T3",
    "thyroxine": "T4", "t4": "T4",
    "urate": "URATE", "uric acid": "URATE",
    "mean corpuscular volume": "MCV", "mcv": "MCV",
    "mean corpuscular haemoglobin": "MCH", "mch": "MCH", "mchc": "MCHC",
    "reticulocytes": "RETIC", "reticulocyte count": "RETIC",
    "egfr": "EGFR", "estimated glomerular filtration rate": "EGFR",
    "hba1c": "HBA1C", "glycated haemoglobin": "HBA1C",
    "cd4": "CD4", "cd4 lymphocyte count": "CD4",
    "cd8": "CD8", "cd8 lymphocyte count": "CD8",
    "viral load": "VIRLOAD", "hiv rna": "VIRLOAD",
    "psa": "PSA", "prostate specific antigen": "PSA",
    "cea": "CEA", "carcinoembryonic antigen": "CEA",
    "ca 125": "CA125", "ca125": "CA125",
    "ca 19-9": "CA199", "ca19-9": "CA199",
}

_EG_TESTS: dict[str, str] = {
    "pr interval": "PRXXX", "p-r interval": "PRXXX",
    "rr interval": "RRXXX",
    "qt interval": "QTXXX", "qtcf": "QTCFXXX", "qtcb": "QTCBXXX",
    "heart rate": "HRATE", "ventricular rate": "VRATE",
    "qrs duration": "QRSDUR",
    "overall interpretation": "INTP", "interpretation": "INTP",
    "ecg interpretation": "INTP",
}

_DOMAIN_TEST_MAP: dict[str, dict[str, str]] = {
    "VS": _VS_TESTS,
    "LB": _LB_TESTS,
    "EG": _EG_TESTS,
}


def _norm_label(text: str) -> str:
    text = re.sub(r'[\xa0  -​]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


class FindingsQualifierResolver:
    """
    Post-processor that adds TESTCD where-clauses to Findings domain results.

    Called after primary SDTM resolution. Examines the field label and context
    to determine whether a TESTCD qualifier can be added.
    """

    def _find_test_code(self, domain: str, label_norm: str) -> str | None:
        test_map = _DOMAIN_TEST_MAP.get(domain, {})
        if label_norm in test_map:
            return test_map[label_norm]
        # Substring containment
        for test_name, test_code in test_map.items():
            if test_name in label_norm or label_norm in test_name:
                return test_code
        return None

    def resolve_qualifier(
        self,
        result: ResolutionResult,
        field_label: str,
        context_labels_before: list[str] | None = None,
        value_options: list[str] | None = None,
    ) -> str | None:
        """
        Return the where-clause string (e.g. 'VSTESTCD = "WEIGHT"') or None.
        """
        domain = (result.sdtm_domain or "").upper()
        variable = (result.sdtm_variable or "").upper()

        if domain not in _FINDINGS_QUALIFIER_VARS:
            return None
        if variable not in _FINDINGS_QUALIFIER_VARS[domain]:
            return None

        testcd_var = _DOMAIN_TESTCD_VAR[domain]

        label_norm = _norm_label(field_label)

        # 1. Field label IS a specific test name (e.g. "Weight", "Height")
        test_code = self._find_test_code(domain, label_norm)
        if test_code:
            return f'{testcd_var} = "{test_code}"'

        # 2. Check context labels before (preceding field label = test name)
        for ctx in reversed(context_labels_before or []):
            test_code = self._find_test_code(domain, _norm_label(ctx))
            if test_code:
                return f'{testcd_var} = "{test_code}"'

        # 3. Check value options (e.g. dropdown containing test names)
        for opt in (value_options or []):
            test_code = self._find_test_code(domain, _norm_label(opt))
            if test_code:
                return f'{testcd_var} = "{test_code}"'

        return None

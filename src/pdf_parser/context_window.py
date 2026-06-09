"""
Contextual window builder for CRF fields.

For each field on a page, builds the contextual window of surrounding
field labels. This context is critical for disambiguating generic labels
like "Date", "Result", "Comment" which map to different SDTM variables
depending on their form context.

Example:
    Field: "Date"
    Context before: ["Was the ECG performed?"]
    Context after: ["Overall ECG evaluation"]
    → Unambiguously maps to EGDAT

    Field: "Date"
    Context before: ["Medication or Therapy"]
    Context after: ["Treatment start date"]
    → Maps to CMSTDTC
"""

from __future__ import annotations

from src.pdf_parser.field_identifier import CRFField


def build_contextual_windows(
    fields: list[CRFField],
    window_size: int = 3,
) -> list[CRFField]:
    """
    Enrich each field with its contextual window (surrounding labels).

    Only non-instruction fields are considered as context providers.
    Instructions are skipped when building windows.

    Args:
        fields: List of CRFField objects from a single page.
        window_size: Number of surrounding fields to include (before + after).

    Returns:
        The same list with context_labels_before and context_labels_after populated.
    """
    # Get indices of actual data fields (not instructions)
    data_field_indices = [
        i for i, f in enumerate(fields)
        if not f.is_instruction and f.field_label.strip()
    ]

    for pos, field_idx in enumerate(data_field_indices):
        field = fields[field_idx]

        # Context before: up to window_size preceding data fields
        before_labels = []
        for offset in range(1, window_size + 1):
            prev_pos = pos - offset
            if prev_pos >= 0:
                prev_field = fields[data_field_indices[prev_pos]]
                before_labels.insert(0, prev_field.field_label)

        # Context after: up to window_size following data fields
        after_labels = []
        for offset in range(1, window_size + 1):
            next_pos = pos + offset
            if next_pos < len(data_field_indices):
                next_field = fields[data_field_indices[next_pos]]
                after_labels.append(next_field.field_label)

        field.context_labels_before = before_labels
        field.context_labels_after = after_labels

    return fields
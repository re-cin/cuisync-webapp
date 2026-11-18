"""Field-level background attribution; no source can verify an unsupported claim.

Two text fields may share one source if its excerpt actually supports both.
The record-level label summarizes their *existing* independent provenance.
"""

FIELDS = ("historical_background", "cultural_background")
KNOWN = frozenset(("source_based", "gpt_fallback"))


def method_for_fields(generation_fields, default=None):
    """Return source_based, gpt_fallback or mixed using declared field methods.

    Do not turn a partially source-backed record into fully source-based merely
    because at least one source URL is present. Old/incomplete records may lack
    field-level metadata; their legacy label is preserved in that case.
    """
    methods = {str((generation_fields or {}).get(field) or '').strip()
               for field in FIELDS}
    methods &= KNOWN
    if methods == KNOWN:
        return "mixed"
    if methods == {"source_based"}:
        return "source_based"
    if methods == {"gpt_fallback"}:
        return "gpt_fallback"
    return default if default in ("source_based", "gpt_fallback", "mixed") else None


def field_method(generation_fields, field, overall_method=None):
    """Return one field's declared provenance; never infer it from 'mixed'."""
    declared = (generation_fields or {}).get(field)
    if declared in KNOWN:
        return declared
    return overall_method if overall_method in KNOWN else None

"""Response-shape adapters which never alter the archived API payload."""

from __future__ import annotations


def template_result(payload: dict) -> dict:
    """Accept the live root-level template schema and legacy res envelopes."""
    if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
        raise ValueError("Unsuccessful template response")
    if isinstance(payload.get("res"), dict):
        return payload["res"]
    if (payload.get("schema_version") == "agent_personal_templates_v1"
            and isinstance(payload.get("changes"), list)
            and type(payload.get("next_cursor")) is int
            and type(payload.get("has_more")) is bool):
        return payload
    raise ValueError("Unknown template response schema")

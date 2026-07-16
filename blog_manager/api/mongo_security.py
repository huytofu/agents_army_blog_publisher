"""Safe MongoDB query and update builders for the blog API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import re
from typing import Any


_FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x0c\x0e-\x1f\x7f-\x9f]")
_DANGEROUS_TEXT = re.compile(r"javascript:|<script\b|</script>", re.IGNORECASE)


def build_safe_eq_query(field: str, value: Any) -> dict[str, dict[str, Any]]:
    """Build an equality query that cannot promote user input into operators."""
    return {_safe_field_name(field): {"$eq": _safe_scalar_value(value)}}


def build_safe_set_update(updates: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a `$set` update after validating fields and scalar values."""
    safe_updates = {
        _safe_field_name(field): _safe_scalar_value(value)
        for field, value in updates.items()
    }
    if not safe_updates:
        raise ValueError("Mongo update requires at least one safe field.")
    return {"$set": safe_updates}


def build_safe_set_on_insert_update(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a `$setOnInsert` update for upsert creation values."""
    safe_values = {
        _safe_field_name(field): _safe_scalar_value(value)
        for field, value in values.items()
    }
    if not safe_values:
        raise ValueError("Mongo upsert requires at least one insert field.")
    return {"$setOnInsert": safe_values}


def merge_update_operators(*updates: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge pre-validated Mongo update operators."""
    merged: dict[str, dict[str, Any]] = {}
    for update in updates:
        for operator, values in update.items():
            if operator not in {"$set", "$setOnInsert", "$inc"}:
                raise ValueError(f"Unsafe Mongo update operator: {operator}")
            merged.setdefault(operator, {}).update(values)
    return merged


def build_safe_inc_update(increments: Mapping[str, int]) -> dict[str, dict[str, int]]:
    safe_increments = {
        _safe_field_name(field): _safe_int_increment(value)
        for field, value in increments.items()
    }
    if not safe_increments:
        raise ValueError("Mongo increment requires at least one safe field.")
    return {"$inc": safe_increments}


def sanitize_text(value: str, *, max_length: int = 2000) -> str:
    """Strip control characters and reject obvious script/operator payloads."""
    if not isinstance(value, str):
        raise ValueError("Expected text value.")
    cleaned = _CONTROL_CHARS.sub("", value)
    if _DANGEROUS_TEXT.search(cleaned):
        raise ValueError("Text contains an unsafe pattern.")
    if len(cleaned) > max_length:
        raise ValueError("Text exceeds maximum length.")
    return cleaned


def _safe_field_name(field: str) -> str:
    if not isinstance(field, str):
        raise ValueError("Mongo field name must be a string.")
    if not _FIELD_PATTERN.fullmatch(field):
        raise ValueError("Mongo field name contains unsafe characters.")
    return field


def _safe_scalar_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, max_length=4096)
    if isinstance(value, datetime) or value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        raise ValueError("Mongo query/update value cannot be a mapping.")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raise ValueError("Mongo query/update value cannot be a sequence.")
    raise ValueError(f"Unsupported Mongo query/update value type: {type(value).__name__}")


def _safe_int_increment(value: int) -> int:
    if not isinstance(value, int):
        raise ValueError("Mongo increment value must be an integer.")
    if value < -1000 or value > 1000:
        raise ValueError("Mongo increment value is outside the allowed range.")
    return value

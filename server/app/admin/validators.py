"""Small form-field validators that collect readable errors instead of raising.

Admin forms are plain HTML posts, so each route validates its fields, collects
``errors`` keyed by field name and re-renders the form with the user's input kept.
"""

from __future__ import annotations

import re


def clean_text(
    value: str | None, *, field: str, errors: dict[str, str], max_len: int, required: bool = True
) -> str:
    text = (value or "").strip()
    if required and not text:
        errors[field] = "This field is required."
    elif len(text) > max_len:
        errors[field] = f"At most {max_len} characters."
    return text


def clean_int(
    value: str | None, *, field: str, errors: dict[str, str], lo: int, hi: int
) -> int | None:
    text = (value or "").strip()
    if not text.lstrip("-").isdigit():
        errors[field] = "Enter a whole number."
        return None
    number = int(text)
    if not lo <= number <= hi:
        errors[field] = f"Must be between {lo} and {hi}."
        return None
    return number


def clean_usn(value: str | None, *, pattern: str, errors: dict[str, str]) -> str:
    usn = (value or "").strip().upper()
    if not usn:
        errors["usn"] = "USN is required."
    elif not re.fullmatch(pattern, usn):
        errors["usn"] = "USN format looks wrong (expected something like 1BM22EC001)."
    return usn


def clean_email(value: str | None, *, errors: dict[str, str]) -> str:
    email = (value or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors["email"] = "Enter a valid email address."
    return email


def clean_password(value: str | None, *, field: str, errors: dict[str, str]) -> str:
    password = value or ""
    if len(password) < 10:
        errors[field] = "Use at least 10 characters."
    return password

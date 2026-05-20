"""Broken import module for eval testing."""

from app.util import format_name


def greet(name: str) -> str:
    return f"Hello, {format_name(name)}"

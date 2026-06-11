"""Fixture with an intentional assertion failure."""


def normalize_name(value: str) -> str:
    return value.strip().lower()

"""String utilities."""

def capitalize_words(text: str) -> str:
    return " ".join(w.capitalize() for w in text.split())

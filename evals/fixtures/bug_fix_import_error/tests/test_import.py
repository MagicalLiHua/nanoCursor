from app.main import greet


def test_greet_formats_name():
    assert greet("  ada lovelace ") == "Hello, Ada Lovelace!"

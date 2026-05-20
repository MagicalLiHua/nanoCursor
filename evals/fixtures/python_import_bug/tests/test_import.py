"""Test that the import is fixed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_import_works():
    from app.main import greet
    assert greet("world") == "Hello, World!"


def test_no_import_error():
    try:
        from app.main import greet
        assert callable(greet)
    except ImportError:
        raise AssertionError("Import should not fail after fix")

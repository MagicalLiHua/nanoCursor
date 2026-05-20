import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from util import capitalize_words, reverse_string

def test_capitalize():
    assert capitalize_words("hello world") == "Hello World"

def test_reverse_string():
    assert reverse_string("abc") == "cba"
    assert reverse_string("") == ""

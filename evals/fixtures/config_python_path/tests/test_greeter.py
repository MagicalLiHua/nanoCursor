import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from greeter import hello

def test_hello():
    assert hello("World") == "Hello, World!"

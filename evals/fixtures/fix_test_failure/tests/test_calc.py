import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from calc import add, multiply

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0

def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(0, 5) == 0

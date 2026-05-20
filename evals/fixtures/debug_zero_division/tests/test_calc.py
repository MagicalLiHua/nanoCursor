import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from calc import divide

def test_divide():
    assert divide(10, 2) == 5

def test_divide_by_zero():
    result = divide(10, 0)
    assert result == "Cannot divide by zero"

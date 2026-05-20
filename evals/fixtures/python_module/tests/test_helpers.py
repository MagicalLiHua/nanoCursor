import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from helpers import square

def test_square():
    assert square(2) == 4
    assert square(0) == 0
    assert square(-3) == 9

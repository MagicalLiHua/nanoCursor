"""Calculator with a bug — test expects add(1,2)==3 but it's broken."""

def add(a, b):
    return a - b  # BUG: should be a + b

def multiply(a, b):
    return a * b

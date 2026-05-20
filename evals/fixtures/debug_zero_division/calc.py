"""Calculator with zero-division bug."""

def divide(a, b):
    return a / b  # BUG: crashes when b == 0

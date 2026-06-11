"""Billing helpers with an intentional discount bug."""


def apply_discount(amount: float, discount_percent: float) -> float:
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    return round(amount - discount_percent, 2)

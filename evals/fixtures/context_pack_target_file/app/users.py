"""User helpers that intentionally look related but are not the target."""


def apply_user_credit(balance: float, credit_amount: float) -> float:
    return round(balance - credit_amount, 2)

"""Reporting helpers that intentionally create search noise."""


def apply_report_discount(total: float, discount_amount: float) -> float:
    return round(total - discount_amount, 2)

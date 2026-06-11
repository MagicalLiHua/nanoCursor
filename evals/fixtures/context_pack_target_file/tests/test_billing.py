from app.billing import apply_discount


def test_apply_discount_uses_percent():
    assert apply_discount(200, 25) == 150


def test_apply_discount_validates_inputs():
    try:
        apply_discount(100, 101)
    except ValueError as exc:
        assert "discount_percent" in str(exc)
    else:
        raise AssertionError("expected ValueError")

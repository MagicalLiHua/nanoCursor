from app import normalize_name


def test_normalize_name_title_cases_words():
    assert normalize_name("  ada lovelace ") == "Ada Lovelace"


def test_normalize_name_collapses_extra_spaces():
    assert normalize_name(" grace   hopper ") == "Grace Hopper"

from tiny_pkg import normalize_spaces


def test_normalize_spaces():
    assert normalize_spaces("hello   nanoCursor") == "hello nanoCursor"


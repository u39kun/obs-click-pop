"""Tier 1 — §1.3: Circle expiration (pure logic, no OBS)."""

from click_pop_core import expire_circles


def test_nothing_expired():
    active = [("a", 10.0), ("b", 11.0)]
    still, expired = expire_circles(active, 9.0)
    assert [n for n, _ in still] == ["a", "b"]
    assert expired == []


def test_one_expired():
    active = [("a", 10.0), ("b", 11.0)]
    still, expired = expire_circles(active, 10.5)
    assert [n for n, _ in still] == ["b"]
    assert expired == ["a"]


def test_all_expired():
    active = [("a", 10.0), ("b", 11.0)]
    still, expired = expire_circles(active, 12.0)
    assert still == []
    assert expired == ["a", "b"]


def test_empty_list():
    still, expired = expire_circles([], 10.0)
    assert still == []
    assert expired == []


def test_exact_boundary_expires():
    active = [("a", 10.0)]
    still, expired = expire_circles(active, 10.0)
    assert still == []
    assert expired == ["a"]

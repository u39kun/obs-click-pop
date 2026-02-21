"""Tier 1 — §1.2: Pool slot allocation (pure logic, no OBS)."""

from click_pop_core import allocate_slot


def test_empty_pool_returns_slot_0():
    active = []
    name, evicted = allocate_slot("__click_pop_L_", 5, active)
    assert name == "__click_pop_L_0"
    assert evicted is None


def test_slot_0_busy_returns_slot_1():
    active = [("__click_pop_L_0", 99.0)]
    name, evicted = allocate_slot("__click_pop_L_", 5, active)
    assert name == "__click_pop_L_1"
    assert evicted is None


def test_all_slots_busy_evicts_oldest():
    active = [(f"__click_pop_L_{i}", 100.0 + i) for i in range(5)]
    name, evicted = allocate_slot("__click_pop_L_", 5, active)
    assert name == "__click_pop_L_0"
    assert evicted == "__click_pop_L_0"
    # The evicted entry should have been removed from active_clicks
    assert all(n != "__click_pop_L_0" for n, _ in active)


def test_left_full_right_gets_slot_0():
    active = [(f"__click_pop_L_{i}", 100.0 + i) for i in range(5)]
    name, evicted = allocate_slot("__click_pop_R_", 5, active)
    assert name == "__click_pop_R_0"
    assert evicted is None


def test_gap_in_pool_fills_gap():
    active = [
        ("__click_pop_L_0", 100.0),
        ("__click_pop_L_1", 101.0),
        ("__click_pop_L_3", 103.0),
    ]
    name, evicted = allocate_slot("__click_pop_L_", 5, active)
    assert name == "__click_pop_L_2"
    assert evicted is None


def test_max_1_slot_busy_evicts():
    active = [("__click_pop_L_0", 100.0)]
    name, evicted = allocate_slot("__click_pop_L_", 1, active)
    assert name == "__click_pop_L_0"
    assert evicted == "__click_pop_L_0"


def test_mixed_lr_evicts_correct_type():
    active = [
        ("__click_pop_R_0", 100.0),
        ("__click_pop_L_0", 101.0),
        ("__click_pop_L_1", 102.0),
    ]
    name, evicted = allocate_slot("__click_pop_L_", 2, active)
    # Should evict the oldest L entry, not the R entry
    assert evicted == "__click_pop_L_0"
    assert name == "__click_pop_L_0"
    # R_0 should still be in active
    assert ("__click_pop_R_0", 100.0) in active

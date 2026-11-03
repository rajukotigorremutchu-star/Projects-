import pytest
from pydantic import ValidationError

from flagsvc.evaluator import bucket, evaluate
from flagsvc.models import Flag


def flag(**kw):
    kw.setdefault("key", "f")
    return Flag(**kw)


def cond(attr, op, value):
    return {"attribute": attr, "op": op, "value": value}


def test_disabled_returns_off_value():
    d = evaluate(flag(enabled=False, off_value="x", default_value="y"), {})
    assert (d.value, d.reason) == ("x", "disabled")


def test_default_when_no_rules_match():
    f = flag(rules=[{"conditions": [cond("plan", "eq", "pro")], "value": True}])
    d = evaluate(f, {"plan": "free"})
    assert (d.value, d.reason, d.rule_index) == (False, "default", None)


def test_first_matching_rule_wins():
    f = flag(rules=[
        {"conditions": [cond("plan", "eq", "pro")], "value": "a"},
        {"conditions": [cond("plan", "eq", "pro")], "value": "b"}])
    assert evaluate(f, {"plan": "pro"}).value == "a"


@pytest.mark.parametrize("op,value,actual,expected", [
    ("eq", 1, 1, True), ("eq", 1, True, False), ("eq", True, 1, False),
    ("eq", 1, 1.0, True), ("neq", "a", "b", True), ("neq", "a", "a", False),
    ("in", ["a", "b"], "b", True), ("in", ["a"], "c", False),
    ("not_in", ["a"], "c", True), ("not_in", ["a"], "a", False),
    ("gt", 5, 6, True), ("gt", 5, 5, False), ("gte", 5, 5, True),
    ("lt", 5, 4, True), ("lte", 5, 5, True), ("gt", 5, "6", False),
    ("gt", 5, True, False),
    ("contains", "ab", "xaby", True), ("starts_with", "x", "xy", True),
    ("ends_with", "y", "xy", True), ("contains", "a", 5, False),
])
def test_operators(op, value, actual, expected):
    f = flag(rules=[{"conditions": [cond("a", op, value)], "value": True}])
    assert evaluate(f, {"a": actual}).value is expected


@pytest.mark.parametrize("op,value", [("neq", "x"), ("not_in", ["x"])])
def test_missing_attribute_never_matches_even_negative_ops(op, value):
    f = flag(rules=[{"conditions": [cond("a", op, value)], "value": True}])
    assert evaluate(f, {}).reason == "default"


def test_conditions_are_anded():
    f = flag(rules=[{"conditions": [cond("a", "eq", 1), cond("b", "eq", 2)], "value": True}])
    assert evaluate(f, {"a": 1, "b": 2}).value is True
    assert evaluate(f, {"a": 1, "b": 3}).value is False


def test_bucket_is_deterministic_and_in_range():
    assert bucket("f", "s", "u1") == bucket("f", "s", "u1")
    assert all(0 <= bucket("f", "s", f"u{i}") < 100 for i in range(500))


def test_salt_and_key_change_bucketing():
    a = [bucket("f", "s1", f"u{i}") for i in range(50)]
    assert a != [bucket("f", "s2", f"u{i}") for i in range(50)]
    assert a != [bucket("g", "s1", f"u{i}") for i in range(50)]


def test_rollout_proportion_is_close_to_target():
    f = flag(rules=[{"rollout": 30, "value": True}])
    n = 5000
    hits = sum(evaluate(f, {"user_id": f"user-{i}"}).value is True for i in range(n))
    assert abs(hits / n - 0.30) < 0.03  # measured on a fixed id set; hash is deterministic


def test_rollout_is_monotonic_users_stay_in_when_percentage_grows():
    users = [f"u{i}" for i in range(2000)]
    def on(p):
        f = flag(rules=[{"rollout": p, "value": True}])
        return {u for u in users if evaluate(f, {"user_id": u}).value is True}
    assert on(10) <= on(40) <= on(80)
    assert on(0) == set() and len(on(100)) == len(users)


def test_rollout_without_user_id_is_excluded():
    f = flag(rules=[{"rollout": 99.99, "value": True}])
    assert evaluate(f, {}).reason == "default"


def test_rollout_miss_falls_through_to_next_rule():
    f = flag(rules=[{"rollout": 0, "value": "a"}, {"value": "b"}])
    d = evaluate(f, {"user_id": "u"})
    assert (d.value, d.rule_index) == ("b", 1)


def test_split_is_sticky_and_roughly_weighted():
    f = flag(salt="exp", rules=[{"split": [{"value": "A", "weight": 70}, {"value": "B", "weight": 30}]}])
    first = [evaluate(f, {"user_id": f"u{i}"}).value for i in range(3000)]
    assert first == [evaluate(f, {"user_id": f"u{i}"}).value for i in range(3000)]
    assert abs(first.count("A") / 3000 - 0.7) < 0.04


def test_split_independent_of_rollout_bucket():
    # A 50% rollout and a 50/50 split must not be perfectly correlated.
    f = flag(rules=[{"rollout": 50, "split": [{"value": "A", "weight": 50}, {"value": "B", "weight": 50}]}])
    vals = [evaluate(f, {"user_id": f"u{i}"}) for i in range(2000)]
    served = [d.value for d in vals if d.reason == "rule_match"]
    assert 0.35 < served.count("A") / len(served) < 0.65


@pytest.mark.parametrize("rule", [
    {},  # neither value nor split
    {"value": 1, "split": [{"value": 1, "weight": 100}]},
    {"split": [{"value": 1, "weight": 60}]},
    {"split": []},
    {"value": True, "rollout": 101},
    {"value": True, "conditions": [cond("a", "in", "notalist")]},
])
def test_invalid_rules_rejected(rule):
    with pytest.raises(ValidationError):
        flag(rules=[rule])


def test_invalid_key_rejected():
    with pytest.raises(ValidationError):
        Flag(key="Bad Key")

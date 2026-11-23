"""Pure, deterministic flag evaluation (no I/O)."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .models import Condition, Decision, Flag, Rule

BUCKETS = 10_000  # 0.01% resolution


def bucket(flag_key: str, salt: str, subject: str, purpose: str = "") -> float:
    """Map (flag, salt, subject) to a stable value in [0, 100)."""
    digest = hashlib.sha256(f"{flag_key}:{salt}:{purpose}:{subject}".encode()).digest()
    n = int.from_bytes(digest[:8], "big") % BUCKETS
    return n / (BUCKETS / 100)


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _eq(a: Any, b: Any) -> bool:
    """Equality that never treats True as 1 (or False as 0)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def condition_matches(cond: Condition, context: Mapping[str, Any]) -> bool:
    """Missing attributes never match (including for negative operators), so a
    rule can't accidentally apply to users we know nothing about."""
    if cond.attribute not in context:
        return False
    actual, expected, op = context[cond.attribute], cond.value, cond.op
    if op == "eq":
        return _eq(actual, expected)
    if op == "neq":
        return not _eq(actual, expected)
    if op == "in":
        return any(_eq(actual, e) for e in expected)
    if op == "not_in":
        return not any(_eq(actual, e) for e in expected)
    if op in ("gt", "gte", "lt", "lte"):
        if not (_num(actual) and _num(expected)):
            return False
        return {"gt": actual > expected, "gte": actual >= expected,
                "lt": actual < expected, "lte": actual <= expected}[op]
    if op in ("contains", "starts_with", "ends_with"):
        if not (isinstance(actual, str) and isinstance(expected, str)):
            return False
        return {"contains": expected in actual,
                "starts_with": actual.startswith(expected),
                "ends_with": actual.endswith(expected)}[op]
    return False  # unreachable: Operator is a closed Literal


def _serve(flag: Flag, rule: Rule, subject: str):
    if rule.value is not None:
        return rule.value
    point = bucket(flag.key, flag.salt, subject, purpose="split")
    acc = 0.0
    for part in rule.split:  # type: ignore[union-attr]
        acc += part.weight
        if point < acc:
            return part.value
    return rule.split[-1].value  # type: ignore[index]  # float rounding guard


def evaluate(flag: Flag, context: Mapping[str, Any]) -> Decision:
    if not flag.enabled:
        return Decision(key=flag.key, value=flag.off_value, reason="disabled")
    subject = str(context.get("user_id", ""))
    for i, rule in enumerate(flag.rules):
        if not all(condition_matches(c, context) for c in rule.conditions):
            continue
        if rule.rollout < 100:
            # No subject -> cannot be bucketed consistently -> excluded.
            if not subject or bucket(flag.key, flag.salt, subject, "rollout") >= rule.rollout:
                continue
        return Decision(key=flag.key, value=_serve(flag, rule, subject),
                        reason="rule_match", rule_index=i)
    return Decision(key=flag.key, value=flag.default_value, reason="default")

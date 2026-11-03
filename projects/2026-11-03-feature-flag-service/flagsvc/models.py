from __future__ import annotations

import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

Scalar = Union[bool, int, float, str]
Operator = Literal[
    "eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte",
    "contains", "starts_with", "ends_with",
]
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Condition(BaseModel):
    attribute: str = Field(min_length=1)
    op: Operator
    value: Any

    @model_validator(mode="after")
    def _check_value(self) -> "Condition":
        if self.op in ("in", "not_in") and not isinstance(self.value, list):
            raise ValueError(f"operator '{self.op}' requires a list value")
        return self


class WeightedValue(BaseModel):
    value: Scalar
    weight: float = Field(ge=0, le=100)


class Rule(BaseModel):
    """All conditions must match (AND). A matching rule serves either a fixed
    ``value`` or a weighted ``split``; ``rollout`` limits it to a percentage of
    matching users (the rest fall through to the next rule)."""

    description: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    rollout: float = Field(default=100, ge=0, le=100)
    value: Optional[Scalar] = None
    split: Optional[list[WeightedValue]] = None

    @model_validator(mode="after")
    def _check_serve(self) -> "Rule":
        if (self.value is None) == (self.split is None):
            raise ValueError("rule must set exactly one of 'value' or 'split'")
        if self.split is not None:
            if not self.split:
                raise ValueError("split must not be empty")
            total = sum(s.weight for s in self.split)
            if abs(total - 100) > 1e-6:
                raise ValueError(f"split weights must sum to 100, got {total}")
        return self


class FlagDefinition(BaseModel):
    description: str = ""
    enabled: bool = True
    # Returned when the flag is disabled.
    off_value: Scalar = False
    # Returned when enabled but no rule matches.
    default_value: Scalar = False
    salt: str = ""
    rules: list[Rule] = Field(default_factory=list)


class Flag(FlagDefinition):
    key: str

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not KEY_RE.match(v):
            raise ValueError("key must match ^[a-z0-9][a-z0-9._-]{0,63}$")
        return v


class Decision(BaseModel):
    key: str
    value: Scalar
    reason: Literal["disabled", "rule_match", "default", "missing_flag"]
    rule_index: Optional[int] = None

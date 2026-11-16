"""Ports of gold_logic_focus.c:14,51,93,116 (ARM64 client, not server code).

Single precision operations follow ghidra-economy-audit.md:101-121. Signed
overflow / out-of-range narrowing is rejected, never assigned invented prices.
Ordinary negative-input branches are retained for parity; mutation APIs reject
negative costs separately. No algebraic simplification of floating expressions.
"""

from __future__ import annotations

import math
import struct

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


class CostDomainError(ValueError):
    """Input or intermediate lies outside the defined client arithmetic domain."""


def signed(value: int, bits: int = 32) -> int:
    """Require an actual signed integer, rejecting bools and silent narrowing."""
    if type(value) is not int or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
        raise CostDomainError(f"expected signed int{bits}")
    return value


def float32(value: float | int) -> float:
    """Round once to IEEE-754 binary32, rejecting non-finite results."""
    try:
        result = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise CostDomainError("value does not fit float32") from exc
    if not math.isfinite(result):
        raise CostDomainError("non-finite float32")
    return result


def _round_multiple(value: int, divisor: int) -> int:
    # All call sites reaching this helper have positive values.
    return signed(((value + divisor - 1) // divisor) * divisor)


def time_to_finish_now_gold(seconds: int) -> int:
    """TimeUtil::timeToFinishNowGold(long long); preserve the 959→960 drop."""
    signed(seconds, 64)
    if seconds <= 180:
        return 1
    minutes = signed(seconds // 60)
    if minutes < 16:
        return minutes * 5
    value = signed(int((1.0 / math.sqrt(float(minutes)) + 1.0) * float(minutes) + 50.0))
    return _round_multiple(value, 10)


def gold_cost_by_time_left_in_draft(seconds: int) -> int:
    """TimeUtil::goldCostByTimeLeftInDraft(int), including negative→5."""
    signed(seconds)
    if seconds == 0:
        return 0
    if seconds <= 59:
        return 5
    minutes = seconds // 60
    if minutes < 16:
        return _round_multiple(minutes * 4, 5)
    value = signed(int((1.0 / math.sqrt(float(minutes)) + 1.0) * float(minutes) + 40.0))
    return _round_multiple(value, 10)


def get_gold_cost_in_school_field(seconds: int) -> int:
    """TimeUtil::getGoldCostInSchoolField(int); division truncates, not ceil."""
    signed(seconds)
    if seconds == 0:
        return 0
    if seconds >= 500020:
        return 25000
    if seconds <= 19:
        return 1
    return seconds // 20


def gold_cost_by_resource_and_people(resource_value: int, people: int) -> int:
    """Port two float32 ceiling-like conversions and the recovered exponent."""
    signed(resource_value)
    signed(people)
    quotient = float32(float32(resource_value) / 100.0)
    rounded = int(quotient)  # C truncation toward zero, also for negatives.
    if float32(quotient - float32(rounded)) > 0.0:
        rounded += 1
    if people > 119:
        power = math.pow(float(people), 0.3333333432674408)
        people = signed(int(float(people) * (1.0 / power) * 5.0))
    subtotal = signed(signed(rounded) + people)
    cost = float32(float32(subtotal) / 5.0)
    result = int(cost)
    if float32(cost - float32(result)) > 0.0:
        result += 1
    return signed(signed(result) * 5)


# Searchable original names for cross-referencing the Ghidra export.
timeToFinishNowGold = time_to_finish_now_gold
goldCostByTimeLeftInDraft = gold_cost_by_time_left_in_draft
getGoldCostInSchoolField = get_gold_cost_in_school_field
goldCostByResourceAndPeople = gold_cost_by_resource_and_people

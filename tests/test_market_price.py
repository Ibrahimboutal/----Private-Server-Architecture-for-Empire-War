"""Boundary vectors for the explicitly supplied local safe pricing model."""

import math

import pytest

from empire_war_private_server.vuln_research import calculate_market_price_safe


@pytest.mark.parametrize("gold,hour,count,expected", [
    (0.0, 1.0, 1, 0), (10, 2, 3, 60),
    (1.9, 1.0, 1, 1), (-1.9, 1.0, 1, -1),
    (-2.0, -3.0, 4, 24), (2.0, 3.0, -4, -24),
    (2147483647.0, 1.0, 1, 2147483647),
    (-2147483648.0, 1.0, 1, -2147483648),
    (2147483646.0, 1.0, 1, 2147483646),
    (2147483648.0, 0.5, 1, 1073741824),
    (1e300, 1.0, 0, 0),
])
def test_defined_results_truncate_toward_zero(gold, hour, count, expected):
    assert calculate_market_price_safe(gold, hour, count) == {"ret": 0, "total_value": expected}


@pytest.mark.parametrize("gold,hour,count", [
    (2147483648.0, 1.0, 1), (-2147483649.0, 1.0, 1),
    (math.nextafter(2147483647.0, math.inf), 1.0, 1),
    (math.nextafter(-2147483648.0, -math.inf), 1.0, 1),
    (2147483646.0, 2.0, 1), (2147483646.0, 1.0, 2),
    (1e308, 2.0, 1), (1e308, 1.0, 2),
    (1e308, 2.0, 0),
])
def test_overflow_rejected_before_integer_conversion(gold, hour, count):
    assert calculate_market_price_safe(gold, hour, count) == {
        "ret": 19011, "msg": "Price exceeds maximum allowed value"}


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("position", [0, 1])
def test_nonfinite_operands_fail_even_when_multiplied_by_zero(value, position):
    operands = [1.0, 1.0, 0]
    operands[position] = value
    assert calculate_market_price_safe(*operands)["ret"] == 19011


@pytest.mark.parametrize("position", [0, 1, 2])
def test_huge_integer_conversion_returns_error_without_arithmetic_exception(position):
    operands = [1.0, 1.0, 1]
    operands[position] = 10**10000
    assert calculate_market_price_safe(*operands)["ret"] == 19011


@pytest.mark.parametrize("gold,hour,count", [
    (True, 1.0, 1), (1.0, False, 1), (1.0, 1.0, True),
    ("1", 1.0, 1), (1.0, None, 1), (1.0, 1.0, 1.0),
    (1.0, 1.0, math.nan), ([], 1.0, 1), (1.0, {}, 1),
])
def test_invalid_types_do_not_get_coerced(gold, hour, count):
    assert calculate_market_price_safe(gold, hour, count) == {
        "ret": 900103, "msg": "Invalid market price inputs"}


def test_multiplication_order_preserves_binary64_underflow():
    # Multiplying hour by count first would produce a nonzero result. The
    # supplied model multiplies gold by hour first, which underflows to zero.
    assert calculate_market_price_safe(1e-200, 1e-200, 10**300) == {"ret": 0, "total_value": 0}


def test_numeric_subclasses_cannot_inject_conversion_or_multiplication():
    class UnexpectedFloat(float):
        def __float__(self):
            raise AssertionError("conversion must not run")
    assert calculate_market_price_safe(UnexpectedFloat(1), 1.0, 1)["ret"] == 900103

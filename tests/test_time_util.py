"""Boundary vectors independently recorded in ghidra-economy-audit.md."""

import pytest

from empire_war_private_server.economy.time_util import (
    CostDomainError, gold_cost_by_resource_and_people, gold_cost_by_time_left_in_draft,
    get_gold_cost_in_school_field, time_to_finish_now_gold,
)


@pytest.mark.parametrize("seconds,expected", [(-1, 1), (0, 1), (180, 1), (181, 15),
    (900, 75), (959, 75), (960, 70), (3600, 120)])
def test_finish_gold(seconds, expected):
    assert time_to_finish_now_gold(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [(-1, 5), (0, 0), (1, 5), (59, 5),
    (60, 5), (120, 10), (900, 60), (960, 60), (3600, 110)])
def test_draft_gold(seconds, expected):
    assert gold_cost_by_time_left_in_draft(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [(-1, 1), (0, 0), (19, 1), (20, 1),
    (39, 1), (40, 2), (500019, 25000), (500020, 25000), (2**31-1, 25000)])
def test_school_gold(seconds, expected):
    assert get_gold_cost_in_school_field(seconds) == expected


@pytest.mark.parametrize("resource,people,expected", [(0, 0, 0), (100, 0, 5),
    (500, 0, 5), (501, 0, 10), (0, 119, 120), (0, 120, 125),
    (-501, 0, -5), (2**31-1, 0, 21474835)])
def test_resources_float32(resource, people, expected):
    assert gold_cost_by_resource_and_people(resource, people) == expected


@pytest.mark.parametrize("value", [True, 1.5, float("inf"), "1", 2**63])
def test_no_silent_argument_coercion(value):
    with pytest.raises(CostDomainError):
        time_to_finish_now_gold(value)


def test_undefined_or_overflowing_domains_fail_closed():
    with pytest.raises(CostDomainError):
        time_to_finish_now_gold(2**63-1)
    with pytest.raises(CostDomainError):
        gold_cost_by_time_left_in_draft(2**31)
    with pytest.raises(CostDomainError):
        gold_cost_by_resource_and_people(-(2**31), -(2**31))

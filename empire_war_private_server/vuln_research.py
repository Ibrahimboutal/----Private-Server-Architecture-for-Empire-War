"""Pure, bounded arithmetic for the local market research model.

This implements the supplied defensive ``gold * hour * res_count`` model. It
does not establish the official server's arithmetic or the reported crash's
cause. No networking, persistence, crash or hang behavior lives in this module.
"""

from __future__ import annotations

import math

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
PRICE_OVERFLOW = 19011
INVALID_PRICE_INPUT = 900103
PRICE_OVERFLOW_MESSAGE = "Price exceeds maximum allowed value"


def calculate_market_price_safe(gold: float, hour: float, res_count: int) -> dict[str, int | str]:
    """Evaluate the local price model without an unchecked integer conversion.

    ``gold`` and ``hour`` accept built-in int/float values; ``res_count`` must be
    a built-in int. Booleans, subclasses and coercible strings are rejected.
    All operands convert to Python float (binary64) before multiplication, in
    the order ``(gold * hour) * res_count``. This avoids arbitrary-precision
    integer multiplication and makes the model's numeric semantics explicit.

    Non-finite operands/intermediates and failed float conversion return 19011.
    The final floating value must lie within the signed int32 bounds *before*
    truncation toward zero. Wrong input types return the local code 900103.

    Negative values and zero remain valid arithmetic inputs here. The wire
    handler separately validates whether they are permitted market requests.
    No individual ``gold`` threshold is inferred from the reported incident.
    """
    if type(gold) not in (int, float) or type(hour) not in (int, float) or type(res_count) is not int:
        return {"ret": INVALID_PRICE_INPUT, "msg": "Invalid market price inputs"}

    def overflow() -> dict[str, int | str]:
        return {"ret": PRICE_OVERFLOW, "msg": PRICE_OVERFLOW_MESSAGE}

    try:
        gold_value, hour_value, count_value = float(gold), float(hour), float(res_count)
    except OverflowError:
        return overflow()
    if not all(math.isfinite(value) for value in (gold_value, hour_value, count_value)):
        return overflow()

    subtotal = gold_value * hour_value
    if not math.isfinite(subtotal):
        return overflow()
    total_value = subtotal * count_value
    if not math.isfinite(total_value) or not INT32_MIN <= total_value <= INT32_MAX:
        return overflow()
    return {"ret": 0, "total_value": int(total_value)}

"""Finite immutable layered decimals for the incremental game."""

from __future__ import annotations
from dataclasses import dataclass
from decimal import (
    Decimal,
    Context,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_CEILING,
    localcontext,
)

CONTEXT = Context(prec=50, Emax=999999999, Emin=-999999999)
TEN = Decimal(10)
PROMOTE = Decimal("1e15")
ZERO_JSON = {"sign": 0, "layer": "0", "mag": "0"}


def _canonical_layer(value: str) -> str:
    """Validate a non-negative arbitrary-length decimal layer without ``int``."""
    if (
        not isinstance(value, str)
        or not value.isdecimal()
        or (len(value) > 1 and value[0] == "0")
    ):
        raise ValueError("Invalid LayeredDecimal layer.")
    return value


def _layer_compare(left: str, right: str) -> int:
    """Compare two canonical decimal strings without converting them to integers."""
    return (
        (len(left), left) > (len(right), right)
        and 1
        or ((len(left), left) < (len(right), right) and -1 or 0)
    )


def _layer_add_one(layer: str) -> str:
    """Add one to an arbitrary-length canonical decimal string."""
    digits = list(layer)
    carry = 1
    for index in range(len(digits) - 1, -1, -1):
        digit = ord(digits[index]) - ord("0") + carry
        digits[index] = str(digit % 10)
        carry = digit // 10
    return ("1" if carry else "") + "".join(digits)


def _layer_sub_one(layer: str) -> str:
    """Subtract one from a positive arbitrary-length decimal string."""
    digits = list(layer)
    for index in range(len(digits) - 1, -1, -1):
        if digits[index] != "0":
            digits[index] = str(ord(digits[index]) - ord("0") - 1)
            break
        digits[index] = "9"
    return "".join(digits).lstrip("0") or "0"


def _d(value: object) -> Decimal:
    """Create a finite Decimal using the game's shared precision context."""
    with localcontext(CONTEXT):
        result = CONTEXT.create_decimal(str(value))

    if not result.is_finite():
        raise ValueError("Only finite values are supported.")

    return result


@dataclass(frozen=True, slots=True)
class LayeredDecimal:
    """A normalized decimal represented by sign, layer, and magnitude."""

    sign: int = 0
    layer: str = "0"
    mag: Decimal = Decimal(0)

    def __post_init__(self):
        """Validate invariants and normalize zero to its single representation."""
        if self.sign not in (-1, 0, 1) or not self.mag.is_finite():
            raise ValueError("Invalid LayeredDecimal.")
        _canonical_layer(self.layer)

        if self.sign == 0:
            object.__setattr__(self, "layer", "0")
            object.__setattr__(self, "mag", Decimal(0))
        elif self.mag < 0:
            raise ValueError("Magnitude cannot be negative.")

    @classmethod
    def of(cls, value: object) -> "LayeredDecimal":
        """Create a layer-zero value from an ordinary finite value."""
        d = _d(value)
        if not d:
            return cls()

        with localcontext(CONTEXT):
            return cls(1 if d > 0 else -1, "0", abs(d)).normalised()

    @classmethod
    def from_json(cls, value: object) -> "LayeredDecimal":
        """Validate and deserialize the canonical JSONB representation."""
        if not isinstance(value, dict) or set(value) != {"sign", "layer", "mag"}:
            raise ValueError("Invalid number storage format.")

        sign, layer, mag = value["sign"], value["layer"], value["mag"]
        if (
            sign not in (-1, 0, 1)
            or not isinstance(layer, str)
            or not layer.isdecimal()
            or not isinstance(mag, str)
        ):
            raise ValueError("Invalid number storage format.")

        result = cls(sign, layer, _d(mag)).normalised()
        if result.to_json() != value:
            raise ValueError("Number storage format is not normalized.")

        return result

    def to_json(self):
        """Return the single canonical representation stored in PostgreSQL."""
        if not self.sign:
            return dict(ZERO_JSON)

        return {"sign": self.sign, "layer": self.layer, "mag": format(self.mag, "f")}

    def normalised(self):
        """Promote or demote this value into its stable layer representation."""
        if not self.sign or not self.mag:
            return LayeredDecimal()
        with localcontext(CONTEXT):
            layer, mag = self.layer, +self.mag
            if layer == "0" and mag >= PROMOTE:
                layer, mag = "1", mag.log10()

            # Small layer-one exponents can safely return to ordinary values.
            if layer == "1" and mag < 15:
                layer, mag = "0", TEN**mag

            return LayeredDecimal(self.sign, layer, +mag)

    def _compare(self, other):
        """Compare this value's ordering with another supported numeric value."""
        other = coerce(other)
        if self.sign != other.sign:
            return (self.sign > other.sign) - (self.sign < other.sign)
        if not self.sign:
            return 0
        if self.layer != other.layer:
            out = _layer_compare(self.layer, other.layer)
        else:
            out = (self.mag > other.mag) - (self.mag < other.mag)

        return out * self.sign

    def __eq__(self, other):
        if not isinstance(other, (LayeredDecimal, int, float, Decimal, str)):
            return NotImplemented
        return self._compare(other) == 0

    def __lt__(self, other):
        return self._compare(other) < 0

    def __le__(self, other):
        return self._compare(other) <= 0

    def __gt__(self, other):
        return self._compare(other) > 0

    def __ge__(self, other):
        return self._compare(other) >= 0

    def __neg__(self):
        return LayeredDecimal(-self.sign, self.layer, self.mag)

    def log10(self):
        if self.sign <= 0:
            raise ValueError("Only positive values have logarithms.")
        return self._log10_magnitude()

    def _log10_magnitude(self):
        """Return the base-ten logarithm of this value's unsigned magnitude."""
        if self.layer == "0":
            with localcontext(CONTEXT):
                return LayeredDecimal.of(self.mag.log10())
        return LayeredDecimal(1, _layer_sub_one(self.layer), self.mag).normalised()

    def ln(self):
        with localcontext(CONTEXT):
            return self.log10() * (
                Decimal("2.3025850929940456840179914546843642076011014886288")
            )

    def __add__(self, other):
        other = coerce(other)
        if not self.sign:
            return other
        if not other.sign:
            return self
        if self.sign != other.sign:
            return self - LayeredDecimal(-other.sign, other.layer, other.mag)
        big, small = (self, other) if self >= other else (other, self)
        if big.layer != small.layer:
            return big
        if big.layer == "0":
            with localcontext(CONTEXT):
                return LayeredDecimal(big.sign, "0", big.mag + small.mag).normalised()
        # Use 10^a + 10^b = 10^(a + log10(1 + 10^(b-a))) in log space.
        if big.mag - small.mag > 55:
            return big
        with localcontext(CONTEXT):
            return LayeredDecimal(
                big.sign,
                big.layer,
                big.mag + (Decimal(1) + TEN ** (small.mag - big.mag)).log10(),
            ).normalised()

    def __radd__(self, other):
        return self + other

    def __sub__(self, other):
        other = coerce(other)
        if not other.sign:
            return self
        if not self.sign:
            return LayeredDecimal(-other.sign, other.layer, other.mag)
        if self.sign != other.sign:
            return self + LayeredDecimal(-other.sign, other.layer, other.mag)
        magnitude_comparison = (
            _layer_compare(self.layer, other.layer)
            if self.layer != other.layer
            else (self.mag > other.mag) - (self.mag < other.mag)
        )
        if not magnitude_comparison:
            return LayeredDecimal()
        big, small = (self, other) if magnitude_comparison > 0 else (other, self)
        sign = self.sign if magnitude_comparison > 0 else -self.sign
        if big.layer != small.layer:
            return LayeredDecimal(sign, big.layer, big.mag)
        if big.layer == "0":
            with localcontext(CONTEXT):
                return LayeredDecimal(sign, "0", big.mag - small.mag).normalised()
        if big.mag - small.mag > 55:
            return LayeredDecimal(sign, big.layer, big.mag)
        with localcontext(CONTEXT):
            remainder = Decimal(1) - TEN ** (small.mag - big.mag)
            if remainder <= 0:
                # The difference is below Decimal precision; treat it as zero
                # rather than constructing an invalid negative exponent.
                return LayeredDecimal()
            result_mag = big.mag + remainder.log10()
            return (
                LayeredDecimal(sign, big.layer, result_mag).normalised()
                if result_mag >= 0
                else LayeredDecimal()
            )

    def __rsub__(self, other):
        return coerce(other) - self

    def __mul__(self, other):
        other = coerce(other)
        if not self.sign or not other.sign:
            return LayeredDecimal()
        if self.layer == other.layer == "0":
            with localcontext(CONTEXT):
                return LayeredDecimal(
                    self.sign * other.sign, "0", self.mag * other.mag
                ).normalised()
        # Multiplication becomes addition in log space; higher layers dominate.
        return _power_of_ten(
            self._log10_magnitude() + other._log10_magnitude()
        ).with_sign(self.sign * other.sign)

    def __rmul__(self, other):
        return self * other

    def __truediv__(self, other):
        other = coerce(other)
        if not other.sign:
            raise ZeroDivisionError
        if not self.sign:
            return self
        if self.layer == other.layer == "0":
            with localcontext(CONTEXT):
                return LayeredDecimal(
                    self.sign * other.sign, "0", self.mag / other.mag
                ).normalised()
        return _power_of_ten(
            self._log10_magnitude() - other._log10_magnitude()
        ).with_sign(self.sign * other.sign)

    def __rtruediv__(self, other):
        return coerce(other) / self

    def __pow__(self, exponent):
        exponent = coerce(exponent)
        if self.sign < 0:
            raise ValueError("Unsupported exponentiation.")
        if not exponent.sign:
            return LayeredDecimal.of(1)
        # a^b = 10^(log10(a) * b). Materialize ordinary results only; promote
        # larger results to the next exponent layer.
        log_result = self.log10() * exponent
        return _power_of_ten(log_result)

    def __rpow__(self, other):
        return coerce(other) ** self

    def floor(self):
        if self.layer != "0":
            return self
        with localcontext(CONTEXT):
            return LayeredDecimal(
                self.sign, "0", self.mag.to_integral_value(rounding=ROUND_FLOOR)
            ).normalised()

    def ceil(self):
        if self.layer != "0":
            return self
        with localcontext(CONTEXT):
            return LayeredDecimal(
                self.sign, "0", self.mag.to_integral_value(rounding=ROUND_CEILING)
            ).normalised()

    def with_sign(self, sign):
        return LayeredDecimal(sign if self.sign else 0, self.layer, self.mag)

    def is_affordable(self, cost):
        return self >= cost


def coerce(value):
    return value if isinstance(value, LayeredDecimal) else LayeredDecimal.of(value)


def _power_of_ten(log_result):
    """Build a positive power of ten from a layered logarithm without recursion."""
    if log_result.sign < 0:
        if log_result.layer != "0":
            raise ValueError("Unsupported exponentiation.")
        with localcontext(CONTEXT):
            return LayeredDecimal(1, "0", TEN**-log_result.mag).normalised()
    if not log_result.sign:
        return LayeredDecimal.of(1)
    if log_result.layer == "0":
        with localcontext(CONTEXT):
            if log_result.mag < 15:
                return LayeredDecimal(1, "0", TEN**log_result.mag).normalised()
        return LayeredDecimal(1, "1", log_result.mag).normalised()
    return LayeredDecimal(
        1, _layer_add_one(log_result.layer), log_result.mag
    ).normalised()


def maximum(a, b):
    return max(a, b)


def _format_layer_magnitude(magnitude: Decimal) -> str:
    """Format a layer suffix magnitude for readable display."""
    if magnitude < Decimal(10000):
        displayed = magnitude.quantize(Decimal(".01"), rounding=ROUND_DOWN)
        return f"{displayed:f}".rstrip("0").rstrip(".")
    return format_amount(LayeredDecimal.of(magnitude))


def format_amount(value: LayeredDecimal) -> str:
    """Format quantities as truncated ordinary or nested scientific notation."""
    value = coerce(value)
    if not value.sign:
        return "0"

    if value.layer == "0":
        if value.mag < Decimal(10000):
            return str(int(value.mag))

        with localcontext(CONTEXT):
            exponent = value.mag.log10()
            exponent_floor = exponent.to_integral_value(rounding=ROUND_FLOOR)
            mantissa = TEN ** (exponent - exponent_floor)
            return f"{mantissa.quantize(Decimal('.01'), rounding=ROUND_DOWN):f}e{int(exponent)}"

    # Layer one stores log10(value); recursively format exceptionally large exponents.
    if value.layer == "1":
        exponent = value.mag
        exponent_floor = exponent.to_integral_value(rounding=ROUND_FLOOR)
        mantissa = TEN ** (exponent - exponent_floor)
        displayed_exponent = format_amount(LayeredDecimal.of(exponent))
        return f"{mantissa.quantize(Decimal('1'), rounding=ROUND_DOWN):f}e{displayed_exponent}"

    return f"1e{_format_layer_magnitude(value.mag)}e^{value.layer}"

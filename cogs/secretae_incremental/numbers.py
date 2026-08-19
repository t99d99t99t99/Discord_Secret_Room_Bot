"""시크리타이 인크리멘탈에서 쓰는 유한 불변 계층형 십진수를 제공합니다."""

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
    """정수 변환 없이 음이 아닌 무한 자릿수 십진 계층을 검증합니다."""
    if (
        not isinstance(value, str)
        or not value.isdecimal()
        or (len(value) > 1 and value[0] == "0")
    ):
        raise ValueError("잘못된 LayeredDecimal layer")
    return value


def _layer_compare(left: str, right: str) -> int:
    """두 정규 십진 문자열을 정수로 변환하지 않고 비교합니다."""
    return (
        (len(left), left) > (len(right), right)
        and 1
        or ((len(left), left) < (len(right), right) and -1 or 0)
    )


def _layer_add_one(layer: str) -> str:
    """자릿수 제한이 없는 정규 십진 문자열에 1을 더합니다."""
    digits = list(layer)
    carry = 1
    for index in range(len(digits) - 1, -1, -1):
        digit = ord(digits[index]) - ord("0") + carry
        digits[index] = str(digit % 10)
        carry = digit // 10
    return ("1" if carry else "") + "".join(digits)


def _layer_sub_one(layer: str) -> str:
    """양수인 자릿수 제한 없는 십진 문자열에서 1을 뺍니다."""
    digits = list(layer)
    for index in range(len(digits) - 1, -1, -1):
        if digits[index] != "0":
            digits[index] = str(ord(digits[index]) - ord("0") - 1)
            break
        digits[index] = "9"
    return "".join(digits).lstrip("0") or "0"


def _d(value: object) -> Decimal:
    """게임의 공통 정밀도 문맥에서 유한한 Decimal을 생성합니다."""
    with localcontext(CONTEXT):
        result = CONTEXT.create_decimal(str(value))

    if not result.is_finite():
        raise ValueError("유한한 수만 사용할 수 있습니다.")

    return result


@dataclass(frozen=True, slots=True)
class LayeredDecimal:
    """부호, 계층, 크기로 나타낸 정규화된 십진수입니다."""

    sign: int = 0
    layer: str = "0"
    mag: Decimal = Decimal(0)

    def __post_init__(self):
        """불변 조건을 검증하고 0을 유일한 표현으로 정규화합니다."""
        if self.sign not in (-1, 0, 1) or not self.mag.is_finite():
            raise ValueError("잘못된 LayeredDecimal")
        _canonical_layer(self.layer)

        if self.sign == 0:
            object.__setattr__(self, "layer", "0")
            object.__setattr__(self, "mag", Decimal(0))
        elif self.mag < 0:
            raise ValueError("magnitude는 음수가 될 수 없습니다.")

    @classmethod
    def of(cls, value: object) -> "LayeredDecimal":
        """일반적인 유한 값에서 0계층 값을 생성합니다."""
        d = _d(value)
        if not d:
            return cls()

        with localcontext(CONTEXT):
            return cls(1 if d > 0 else -1, "0", abs(d)).normalised()

    @classmethod
    def from_json(cls, value: object) -> "LayeredDecimal":
        """정규 JSONB 표현을 검증하고 역직렬화합니다."""
        if not isinstance(value, dict) or set(value) != {"sign", "layer", "mag"}:
            raise ValueError("잘못된 숫자 저장 형식")

        sign, layer, mag = value["sign"], value["layer"], value["mag"]
        if (
            sign not in (-1, 0, 1)
            or not isinstance(layer, str)
            or not layer.isdecimal()
            or not isinstance(mag, str)
        ):
            raise ValueError("잘못된 숫자 저장 형식")

        result = cls(sign, layer, _d(mag)).normalised()
        if result.to_json() != value:
            raise ValueError("정규화되지 않은 숫자 저장 형식")

        return result

    def to_json(self):
        """PostgreSQL에 기록하는 유일한 표현으로 이 값을 반환합니다."""
        if not self.sign:
            return dict(ZERO_JSON)

        return {"sign": self.sign, "layer": self.layer, "mag": format(self.mag, "f")}

    def normalised(self):
        """값을 안정적인 계층 표현으로 승격하거나 축소합니다."""
        if not self.sign or not self.mag:
            return LayeredDecimal()
        with localcontext(CONTEXT):
            layer, mag = self.layer, +self.mag
            if layer == "0" and mag >= PROMOTE:
                layer, mag = "1", mag.log10()

            # 작은 지수의 1계층 값은 일반적인 값으로 안전하게 축소할 수 있습니다.
            if layer == "1" and mag < 15:
                layer, mag = "0", TEN**mag

            return LayeredDecimal(self.sign, layer, +mag)

    def _compare(self, other):
        """이 값과 다른 값의 순서를 비교합니다."""
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
            raise ValueError("양수의 로그만 계산할 수 있습니다.")
        return self._log10_magnitude()

    def _log10_magnitude(self):
        """부호를 무시하고 이 값의 크기에 대한 상용로그를 반환합니다."""
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
        # 10^a + 10^b = 10^(a + log10(1 + 10^(b-a)))를 사용합니다.
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
                # 두 값의 차이가 Decimal의 표현 정밀도보다 작습니다.
                # 잘못된 음수 지수를 만들지 않도록 잔여값을 0으로 처리합니다.
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
        # 로그 공간의 곱셈은 덧셈이며, 높은 계층에서는 큰 피연산자가 지배합니다.
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
            raise ValueError("지원하지 않는 거듭제곱")
        if not exponent.sign:
            return LayeredDecimal.of(1)
        # a^b = 10^(log10(a) * b)입니다. 결과가 작을 때만 실제 값을 만들고,
        # 그렇지 않으면 다음 계층의 지수로 승격합니다.
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
    """계층형 상용로그에서 양의 10의 거듭제곱을 재귀 없이 구성합니다."""
    if log_result.sign < 0:
        if log_result.layer != "0":
            raise ValueError("지원하지 않는 거듭제곱")
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


def format_amount(value: LayeredDecimal) -> str:
    """수량을 절삭한 일반 표기 또는 중첩 과학 표기법으로 형식화합니다."""
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

    # 1계층에서는 값이 10^(저장된 로그)이며, 거대한 지수는 재귀적으로 표시합니다.
    if value.layer == "1":
        exponent = value.mag
        exponent_floor = exponent.to_integral_value(rounding=ROUND_FLOOR)
        mantissa = TEN ** (exponent - exponent_floor)
        displayed_exponent = format_amount(LayeredDecimal.of(exponent))
        return f"{mantissa.quantize(Decimal('.01'), rounding=ROUND_DOWN):f}e{displayed_exponent}"

    return f"1e1e{format_amount(LayeredDecimal.of(value.mag))}"

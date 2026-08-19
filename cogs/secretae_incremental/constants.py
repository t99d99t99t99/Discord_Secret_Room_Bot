"""변경할 수 없는 게임 키와 한국어 표시 데이터를 정의합니다."""

from types import MappingProxyType

RED, ORANGE, YELLOW, GREEN, BLUE, PURPLE, WHITE, BLACK = (
    "RED",
    "ORANGE",
    "YELLOW",
    "GREEN",
    "BLUE",
    "PURPLE",
    "WHITE",
    "BLACK",
)
CIRCLE, SQUARE, HEART = "CIRCLE", "SQUARE", "HEART"
COLORS = (RED, ORANGE, YELLOW, GREEN, BLUE, PURPLE, WHITE, BLACK)
SHAPES = (CIRCLE, SQUARE, HEART)
SECRETS = COLORS + SHAPES
PRODUCTION_ORDER = tuple(reversed(COLORS))
MAX_SYNTHESIS_ORDER = (
    CIRCLE,
    SQUARE,
    HEART,
    BLACK,
    WHITE,
    PURPLE,
    BLUE,
    GREEN,
    YELLOW,
    ORANGE,
    RED,
)

SYMBOLS = MappingProxyType(
    dict(
        zip(SECRETS, ("🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "⬜️", "⬛️", "◯", "□", "♡"))
    )
)
ORGANIC_SYMBOLS = MappingProxyType(
    dict(zip(COLORS, ("❤️", "🧡", "💛", "💚", "💙", "💜", "🤍", "🖤")))
)
KOREAN_NAMES = MappingProxyType(
    dict(
        zip(
            SECRETS,
            (
                "빨강",
                "주황",
                "노랑",
                "초록",
                "파랑",
                "보라",
                "하양",
                "검정",
                "원형",
                "사각형",
                "하트 모양",
            ),
        )
    )
)
INITIAL_SECRETS = 100
INITIAL_ORGANICS = 1

SHARD_SYMBOL = "✨"  # 빛나는 파편

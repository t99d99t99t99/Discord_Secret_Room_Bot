from cogs.submission_management.common import (
    DISCORD_MESSAGE_MAX_LENGTH,
    split_discord_message_content,
)


def test_split_discord_message_content_preserves_long_content():
    content = "a" * (DISCORD_MESSAGE_MAX_LENGTH * 2 + 1)

    chunks = split_discord_message_content(content)

    assert [len(chunk) for chunk in chunks] == [2000, 2000, 1]
    assert "".join(chunks) == content


def test_split_discord_message_content_returns_one_chunk_at_limit():
    content = "가" * DISCORD_MESSAGE_MAX_LENGTH

    assert split_discord_message_content(content) == [content]

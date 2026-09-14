"""Order the declarative schema groups used during database startup."""

from __future__ import annotations

from .community_schema import initialize_community_schema
from .legacy_schema import initialize_legacy_schema


async def initialize_base_schema(conn) -> None:
    """Create stable tables before their versioned data migrations run."""
    await initialize_community_schema(conn)
    await initialize_legacy_schema(conn)

"""기존 ``sc_*`` 데이터를 한 번만 트랜잭션으로 전환합니다."""

from collections import Counter
import hashlib
import json

from .constants import COLORS, SECRETS, SHAPES
from .numbers import LayeredDecimal as N, maximum

MIGRATION_KEY = "legacy-secretae-v1"


def _valid_nonnegative_int(value, fallback):
    """기존 정수가 유효하면 반환하고, 아니면 지정한 기본값을 반환합니다."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else fallback
    )


def _normalise_factory(arr, width, height):
    """기존 공장의 두 배열 방향을 모두 처리하고, 유효하지 않으면 빈 목록을 반환합니다."""
    if (
        not isinstance(arr, list)
        or not isinstance(width, int)
        or not isinstance(height, int)
    ):
        return []
    if len(arr) == height and all(
        isinstance(row, list) and len(row) == width for row in arr
    ):
        return [cell for row in arr for cell in row]
    if len(arr) == width and all(
        isinstance(column, list) and len(column) == height for column in arr
    ):
        return [arr[x][y] for y in range(height) for x in range(width)]
    return []


async def run_pending_migrations(pool):
    """인크리멘탈 Discord 코그를 불러오기 전에 기존 플레이어를 한 번 이관합니다."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 문서화한 30일 동안 변경 불가능한 이관 증적을 보관합니다.
            await conn.execute(
                "DELETE FROM si_migration_reports WHERE migrated_at < NOW() - INTERVAL '30 days'"
            )
            await conn.execute(
                "DELETE FROM si_migration_snapshots WHERE created_at < NOW() - INTERVAL '30 days'"
            )
            await conn.execute("SELECT pg_advisory_xact_lock(739182641)")
            if await conn.fetchval(
                "SELECT 1 FROM si_migrations WHERE migration_key=$1", MIGRATION_KEY
            ):
                return

            user_rows = await conn.fetch(
                "SELECT discord_id, story_essence_count FROM sc_users"
            )
            secret_rows = await conn.fetch(
                "SELECT discord_id, color_secrets, shape_secrets FROM sc_secrets"
            )
            factory_rows = await conn.fetch(
                "SELECT discord_id, slot, width, height, arr FROM sc_factories"
            )
            snapshot = {
                "users": [dict(row) for row in user_rows],
                "secrets": [dict(row) for row in secret_rows],
                "factories": [dict(row) for row in factory_rows],
            }
            snapshot_bytes = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            checksum = hashlib.sha256(snapshot_bytes).hexdigest()
            await conn.execute(
                "INSERT INTO si_migration_snapshots(migration_key, snapshot, checksum) VALUES($1, $2, $3)",
                MIGRATION_KEY,
                snapshot,
                checksum,
            )

            users = {row["discord_id"]: row for row in snapshot["users"]}
            secrets = {row["discord_id"]: row for row in snapshot["secrets"]}
            factories = snapshot["factories"]
            player_ids = (
                set(users) | set(secrets) | {row["discord_id"] for row in factories}
            )
            by_player = {}
            for factory in factories:
                by_player.setdefault(factory["discord_id"], []).append(factory)

            total = N.of(0)
            highest = N.of(0)
            for discord_id in player_ids:
                warnings = []
                user = users.get(discord_id)
                raw_essence = user["story_essence_count"] if user else 0
                essence_value = _valid_nonnegative_int(raw_essence, 0)
                if essence_value != raw_essence:
                    warnings.append("legacy Essence was invalid; used 0")
                essence = N.of(essence_value)

                source = secrets.get(discord_id)
                color_source = source["color_secrets"] if source else {}
                shape_source = source["shape_secrets"] if source else {}
                amounts = {}
                for key in COLORS + SHAPES:
                    resource_source = color_source if key in COLORS else shape_source
                    raw = (
                        resource_source.get(key)
                        if isinstance(resource_source, dict)
                        else None
                    )
                    amount = _valid_nonnegative_int(raw, 100)
                    if amount != raw:
                        warnings.append(f"{key} was invalid; used 100")
                    amounts[key] = N.of(amount)

                pairs = Counter()
                for factory in by_player.get(discord_id, []):
                    for cell in _normalise_factory(
                        factory["arr"], factory["width"], factory["height"]
                    ):
                        if not isinstance(cell, dict):
                            warnings.append("invalid factory cell skipped")
                            continue
                        color, shape = cell.get("color"), cell.get("shape")
                        if (
                            isinstance(color, int)
                            and 0 <= color < 8
                            and isinstance(shape, int)
                            and 0 <= shape < 3
                        ):
                            pairs[(color, shape)] += 1
                        else:
                            warnings.append("invalid factory cell skipped")
                bonuses = {key: N.of(0) for key in SECRETS}
                for (color, shape), count in pairs.items():
                    bonus = N.of(count * (count + 1) // 2)
                    bonuses[COLORS[color]] = bonuses[COLORS[color]] + bonus
                    bonuses[SHAPES[shape]] = bonuses[SHAPES[shape]] + bonus
                amounts = {key: amounts[key] + bonuses[key] for key in SECRETS}

                await conn.execute(
                    "INSERT INTO si_players(discord_id, shards, essence) VALUES($1, $2, $3) "
                    "ON CONFLICT(discord_id) DO UPDATE SET shards=EXCLUDED.shards, essence=EXCLUDED.essence, last_produced_game_date=NULL",
                    discord_id,
                    N.of(1).to_json(),
                    essence.to_json(),
                )
                await conn.execute(
                    "INSERT INTO si_secrets(discord_id, amounts) VALUES($1, $2) "
                    "ON CONFLICT(discord_id) DO UPDATE SET amounts=EXCLUDED.amounts",
                    discord_id,
                    {key: value.to_json() for key, value in amounts.items()},
                )
                await conn.execute(
                    "INSERT INTO si_organics(discord_id, amounts) VALUES($1, $2) "
                    "ON CONFLICT(discord_id) DO UPDATE SET amounts=EXCLUDED.amounts",
                    discord_id,
                    {key: N.of(1).to_json() for key in COLORS},
                )
                await conn.execute(
                    "INSERT INTO si_migration_reports(discord_id, migration_key, report) VALUES($1, $2, $3)",
                    discord_id,
                    MIGRATION_KEY,
                    {
                        "snapshot_id": MIGRATION_KEY,
                        "legacy_essence": essence.to_json(),
                        "carried_secrets": {
                            key: (amounts[key] - bonuses[key]).to_json()
                            for key in SECRETS
                        },
                        "final_secrets": {
                            key: value.to_json() for key, value in amounts.items()
                        },
                        "pair_counts": {
                            f"{color}:{shape}": count
                            for (color, shape), count in pairs.items()
                        },
                        "bonuses": {
                            key: value.to_json() for key, value in bonuses.items()
                        },
                        "warnings": warnings,
                    },
                )
                total = total + essence
                highest = maximum(highest, essence)

            await conn.execute(
                "UPDATE si_world_state SET total_essence=$1, highest_essence=$2, updated_at=NOW() WHERE singleton=TRUE",
                total.to_json(),
                highest.to_json(),
            )
            await conn.execute(
                "INSERT INTO si_migrations(migration_key, details) VALUES($1, $2)",
                MIGRATION_KEY,
                {
                    "legacy_players": len(player_ids),
                    "snapshot_checksum": checksum,
                    "verified_player_count": len(player_ids),
                    "verified_total_essence": total.to_json(),
                },
            )

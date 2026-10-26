"""Create and restore PostgreSQL custom-format backups for the bot database."""

from __future__ import annotations

import argparse
from glob import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

from dotenv import load_dotenv


def _database_url(value: str | None) -> str:
    """Return the configured PostgreSQL connection URL or explain how to set it."""
    database_url = value or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL or pass --database-url.")
    return database_url


def _client_environment(database_url: str) -> dict[str, str]:
    """Translate a PostgreSQL URL into libpq environment variables.

    Keeping credentials out of command arguments prevents process listings and
    exception tracebacks from exposing the database password.
    """
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL.")
    if not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL must include a host and database name.")

    environment = os.environ.copy()
    # Avoid forwarding the original credential-bearing URI to the client.
    environment.pop("DATABASE_URL", None)
    environment.update(
        {
            "PGHOST": unquote(parsed.hostname),
            "PGPORT": str(parsed.port or 5432),
            "PGDATABASE": unquote(parsed.path.lstrip("/")),
        }
    )
    if parsed.username is not None:
        environment["PGUSER"] = unquote(parsed.username)
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)

    # These are the libpq settings commonly supplied in hosted database URLs.
    query_environment_names = {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "application_name": "PGAPPNAME",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "channel_binding": "PGCHANNELBINDING",
        "gssencmode": "PGGSSENCMODE",
        "options": "PGOPTIONS",
        "target_session_attrs": "PGTARGETSESSIONATTRS",
    }
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        environment_name = query_environment_names.get(key)
        if environment_name:
            environment[environment_name] = value
    return environment


def _client_executable(name: str) -> str:
    """Prefer the newest versioned Debian client, then use the system PATH."""
    versioned_candidates = glob(f"/usr/lib/postgresql/*/bin/{name}")
    if versioned_candidates:
        return max(
            versioned_candidates,
            key=lambda path: tuple(
                int(part) for part in Path(path).parts[-3].split(".")
            ),
        )
    return shutil.which(name) or name


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    """Run a PostgreSQL client command and report missing tools clearly."""
    try:
        subprocess.run(command, check=True, env=environment)
    except FileNotFoundError as error:
        raise RuntimeError(
            f"{command[0]} was not found; install PostgreSQL client tools in the environment running this command."
        ) from error
    except subprocess.CalledProcessError as error:
        # Suppress CalledProcessError context: process arguments can contain
        # secrets when callers provide connection details.
        raise RuntimeError(
            f"{Path(command[0]).name} failed with exit code {error.returncode}."
        ) from None


def create_backup(database_url: str, destination: Path) -> Path:
    """Write an atomic, compressed PostgreSQL custom-format archive."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(destination.name + ".partial")
    try:
        _run(
            [
                _client_executable("pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(temporary_path),
            ],
            environment=_client_environment(database_url),
        )
        # Archives contain private community records, so restrict local access.
        temporary_path.chmod(0o600)
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def restore_backup(
    database_url: str, archive: Path, *, clean: bool, assume_yes: bool
) -> None:
    """Validate an archive, then restore it to the configured database."""
    if not archive.is_file():
        raise ValueError(f"Backup archive does not exist: {archive}")

    # Ask pg_restore to parse the archive before making any database changes.
    pg_restore = _client_executable("pg_restore")
    _run([pg_restore, "--list", str(archive)])
    if not assume_yes:
        answer = input(
            f"Restore {archive} into the configured database? This changes live data. [y/N] "
        )
        if answer.strip().lower() != "y":
            print("Restore cancelled.")
            return

    command = [
        pg_restore,
        "--single-transaction",
        "--no-owner",
        "--no-acl",
    ]
    if clean:
        # This removes objects represented by the archive before recreating them.
        command.extend(["--clean", "--if-exists"])
    command.append(str(archive))
    _run(command, environment=_client_environment(database_url))


def _default_backup_path() -> Path:
    """Return a timestamped archive path in the local backups directory."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("backups") / f"secretroombot-{timestamp}.dump"


def main(argv: list[str] | None = None) -> int:
    """Run the backup or restore command-line workflow."""
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", help="PostgreSQL URL; defaults to DATABASE_URL"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    backup_parser = commands.add_parser("backup", help="Create a custom-format archive")
    backup_parser.add_argument(
        "--output", type=Path, help="Archive path (default: timestamped backups/ file)"
    )
    scheduled_parser = commands.add_parser(
        "scheduled", help="Create a local archive when called by cron or a scheduler"
    )
    scheduled_parser.add_argument(
        "--output", type=Path, help="Archive path (default: timestamped backups/ file)"
    )
    restore_parser = commands.add_parser(
        "restore", help="Restore a custom-format archive"
    )
    restore_parser.add_argument("archive", type=Path)
    restore_parser.add_argument(
        "--clean", action="store_true", help="Drop archived objects before restoring"
    )
    restore_parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive restore confirmation"
    )
    args = parser.parse_args(argv)

    try:
        database_url = _database_url(args.database_url)
        if args.action in {"backup", "scheduled"}:
            path = create_backup(database_url, args.output or _default_backup_path())
            print(f"Backup written to {path}")
        else:
            restore_backup(
                database_url, args.archive, clean=args.clean, assume_yes=args.yes
            )
            if args.yes:
                print(f"Restore completed from {args.archive}")
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

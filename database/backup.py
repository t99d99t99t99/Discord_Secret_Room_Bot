"""Create and restore PostgreSQL custom-format backups for the bot database."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv


def _database_url(value: str | None) -> str:
    """Return the configured PostgreSQL connection URL or explain how to set it."""
    database_url = value or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL or pass --database-url.")
    return database_url


def _run(command: list[str]) -> None:
    """Run a PostgreSQL client command and report missing tools clearly."""
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as error:
        raise RuntimeError(
            f"{command[0]} was not found; install PostgreSQL client tools in the environment running this command."
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"{command[0]} failed with exit code {error.returncode}."
        ) from error


def create_backup(database_url: str, destination: Path) -> Path:
    """Write an atomic, compressed PostgreSQL custom-format archive."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(destination.name + ".partial")
    try:
        _run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(temporary_path),
                "--dbname",
                database_url,
            ]
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
    _run(["pg_restore", "--list", str(archive)])
    if not assume_yes:
        answer = input(
            f"Restore {archive} into the configured database? This changes live data. [y/N] "
        )
        if answer.strip().lower() != "y":
            print("Restore cancelled.")
            return

    command = [
        "pg_restore",
        "--single-transaction",
        "--no-owner",
        "--no-acl",
        "--dbname",
        database_url,
    ]
    if clean:
        # This removes objects represented by the archive before recreating them.
        command.extend(["--clean", "--if-exists"])
    command.append(str(archive))
    _run(command)


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

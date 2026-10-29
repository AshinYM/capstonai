"""Create validated SQLite snapshots without overwriting existing files."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile


def validate(connection):
    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ValueError('SQLite integrity check failed')
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'documents', 'chunks', 'messages'} <= tables:
        raise ValueError('Not a review database: required tables missing')


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not source.is_file():
        raise FileNotFoundError('Source database does not exist')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Destination already exists; choose a new path')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.review-snapshot-', dir=destination.parent)
    os.close(descriptor)
    try:
        # SQLite's backup API includes committed WAL data consistently.
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader:
            validate(reader)
            with closing(sqlite3.connect(temporary)) as writer:
                reader.backup(writer)
                # The standalone backup must not rely on external WAL files.
                writer.execute('PRAGMA journal_mode=DELETE')
                validate(writer)
        # Atomic publication with no replacement, even if another writer races us.
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup', help='Snapshot an existing review database')
    backup.add_argument('--database', required=True)
    backup.add_argument('--output', required=True)
    restore = commands.add_parser('restore', help='Restore into a NEW database path; stop the app first')
    restore.add_argument('--snapshot', required=True)
    restore.add_argument('--database', required=True)
    args = parser.parse_args()
    try:
        result = snapshot(args.database, args.output) if args.command == 'backup' else snapshot(args.snapshot, args.database)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f'Snapshot failed: {exc}\n')
    print(f'{args.command}: {result}')


if __name__ == '__main__':
    main()

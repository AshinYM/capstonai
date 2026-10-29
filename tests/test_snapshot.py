import sqlite3
import pytest
from scripts.db_snapshot import snapshot


def database(path):
    db = sqlite3.connect(path)
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE documents(id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE chunks(id TEXT, document_id TEXT, text TEXT);
        CREATE TABLE messages(content TEXT);
        CREATE TABLE section_summaries(key TEXT, value TEXT);''')
    return db


def test_live_wal_backup_and_restore(tmp_path):
    source = tmp_path/'live.db'
    with database(source) as db:
        db.execute("INSERT INTO documents VALUES('doc', 'sample')")
        db.execute("INSERT INTO chunks VALUES('chunk', 'doc', 'original')")
        db.execute("INSERT INTO messages VALUES('answer')")
        db.execute("INSERT INTO section_summaries VALUES('cache', 'summary')")
        db.commit()
        db.execute("INSERT INTO messages VALUES('uncommitted')")
        backup = snapshot(source, tmp_path/'backup.db')
        restored = snapshot(backup, tmp_path/'restored.db')
        with sqlite3.connect(restored) as result:
            assert result.execute('SELECT content FROM messages').fetchall() == [('answer',)]
            assert result.execute('SELECT text FROM chunks').fetchone() == ('original',)
            assert result.execute('SELECT value FROM section_summaries').fetchone() == ('summary',)
        db.rollback()
    assert backup.stat().st_mode & 0o777 == 0o600


def test_no_overwrite_or_source_creation(tmp_path):
    missing = tmp_path/'missing.db'
    with pytest.raises(FileNotFoundError):
        snapshot(missing, tmp_path/'backup.db')
    assert not missing.exists()
    with database(tmp_path/'source.db'):
        pass
    target = tmp_path/'existing.db'
    target.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        snapshot(tmp_path/'source.db', target)
    assert target.read_bytes() == b'keep'


def test_wrong_database_and_corruption_not_published(tmp_path):
    wrong = tmp_path/'other.db'
    with sqlite3.connect(wrong) as db:
        db.execute('CREATE TABLE unrelated(id)')
    target = tmp_path/'backup.db'
    with pytest.raises(ValueError):
        snapshot(wrong, target)
    assert not target.exists()
    corrupt = tmp_path/'corrupt.db'
    corrupt.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        snapshot(corrupt, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.review-snapshot-*'))

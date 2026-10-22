import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE,
                    created TEXT DEFAULT CURRENT_TIMESTAMP, characters INTEGER);
                CREATE TABLE IF NOT EXISTS chunks(
                    id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    page INTEGER, start_line INTEGER, end_line INTEGER, text TEXT);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY, session TEXT, role TEXT, content TEXT,
                    metadata TEXT, scope TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, name, content, chunks):
        digest = hashlib.sha256(content).hexdigest()
        with self.connect() as db:
            prior = db.execute('SELECT * FROM documents WHERE digest=?', (digest,)).fetchone()
            if prior:
                return dict(prior) | {'duplicate': True}
            doc = uuid.uuid4().hex
            db.execute('INSERT INTO documents(id,name,digest,characters) VALUES(?,?,?,?)',
                       (doc, name, digest, sum(len(c['text']) for c in chunks)))
            for i, chunk in enumerate(chunks):
                db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                           (f'{doc[:12]}-{i+1}', doc, chunk['page'], chunk['start_line'],
                            chunk['end_line'], chunk['text']))
            row = db.execute('SELECT * FROM documents WHERE id=?', (doc,)).fetchone()
            return dict(row) | {'duplicate': False}

    def documents(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute("""SELECT d.*,count(c.id) AS chunk_count
                FROM documents d LEFT JOIN chunks c ON c.document_id=d.id
                GROUP BY d.id ORDER BY d.created DESC,d.id""")]

    def chunks(self, ids):
        if not ids:
            return []
        with self.connect() as db:
            return [dict(x) for x in db.execute(
                'SELECT c.*,d.name FROM chunks c JOIN documents d ON c.document_id=d.id '
                'WHERE d.id IN (' + ','.join('?' for _ in ids) + ') ORDER BY d.created,d.id,c.rowid', ids)]

    def chunk(self, cid):
        with self.connect() as db:
            row = db.execute('SELECT c.*,d.name FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?', (cid,)).fetchone()
            return dict(row) if row else None

    def delete(self, doc):
        with self.connect() as db:
            return db.execute('DELETE FROM documents WHERE id=?', (doc,)).rowcount > 0

    def history(self, session, ids):
        scope = json.dumps(sorted(ids))
        with self.connect() as db:
            rows = db.execute('SELECT role,content,metadata,created FROM messages WHERE session=? AND scope=? ORDER BY id DESC LIMIT 20', (session, scope)).fetchall()
            return [dict(x) | {'metadata': json.loads(x['metadata'])} for x in reversed(rows)]

    def save_turn(self, session, ids, message, answer, metadata):
        scope = json.dumps(sorted(ids))
        with self.connect() as db:
            db.executemany('INSERT INTO messages(session,role,content,metadata,scope) VALUES(?,?,?,?,?)',
                [(session, 'user', message, '{}', scope),
                 (session, 'assistant', answer, json.dumps(metadata, ensure_ascii=False), scope)])

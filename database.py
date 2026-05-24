import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "vault-index.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            name TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_name TEXT NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY(note_name) REFERENCES notes(name) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_note TEXT NOT NULL,
            target_note TEXT NOT NULL,
            FOREIGN KEY(source_note) REFERENCES notes(name) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tags_note ON tags(note_name);
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
        CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_note);
    """)
    conn.commit()
    conn.close()


def index_note(name, content, created_at, updated_at, tags, links):
    """Index or re-index a note."""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO notes (name, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, content, created_at, updated_at),
    )
    conn.execute("DELETE FROM tags WHERE note_name = ?", (name,))
    conn.execute("DELETE FROM links WHERE source_note = ?", (name,))
    for tag in tags:
        conn.execute("INSERT INTO tags (note_name, tag) VALUES (?, ?)", (name, tag))
    for link in links:
        conn.execute("INSERT INTO links (source_note, target_note) VALUES (?, ?)", (name, link))
    conn.commit()
    conn.close()


def remove_note(name):
    conn = get_conn()
    conn.execute("DELETE FROM notes WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def search_notes(query, tag=None):
    conn = get_conn()
    if tag:
        rows = conn.execute(
            "SELECT n.name, n.updated_at FROM notes n JOIN tags t ON n.name = t.note_name WHERE t.tag = ? AND n.content LIKE ? ORDER BY n.updated_at DESC",
            (tag, f"%{query}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT name, updated_at FROM notes WHERE content LIKE ? OR name LIKE ? ORDER BY updated_at DESC",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_tags():
    conn = get_conn()
    rows = conn.execute("SELECT tag, COUNT(*) as cnt FROM tags GROUP BY tag ORDER BY cnt DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_links():
    conn = get_conn()
    rows = conn.execute("SELECT source_note, target_note FROM links").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_note_names():
    conn = get_conn()
    rows = conn.execute("SELECT name FROM notes ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]

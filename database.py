import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "vault-index.db")

from vault import note_exists, get_all_folders as vault_folders


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
            folder TEXT NOT NULL DEFAULT '',
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


def migrate_db():
    conn = get_conn()
    try:
        conn.execute("ALTER TABLE notes ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.close()


def index_note(name, content, created_at, updated_at, tags, links, folder=""):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO notes (name, folder, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, folder, content, created_at, updated_at),
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


def search_notes(query, tag=None, folder=None):
    conn = get_conn()
    params = []
    where_clauses = []
    if tag:
        where_clauses.append("t.tag = ?")
        params.append(tag)
    if folder:
        where_clauses.append("n.folder = ?")
        params.append(folder)
    where_clauses.append("(n.content LIKE ? OR n.name LIKE ?)")
    params.extend([f"%{query}%", f"%{query}%"])
    where_sql = " AND ".join(where_clauses)
    join_sql = " JOIN tags t ON n.name = t.note_name" if tag else ""
    rows = conn.execute(
        f"SELECT n.name, n.folder, n.updated_at FROM notes n{join_sql} WHERE {where_sql} ORDER BY n.updated_at DESC",
        params,
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


def get_backlinks(name):
    conn = get_conn()
    rows = conn.execute(
        "SELECT source_note FROM links WHERE target_note = ? ORDER BY source_note",
        (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_note_names():
    conn = get_conn()
    rows = conn.execute("SELECT name FROM notes ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_notes_by_folder(folder):
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, updated_at FROM notes WHERE folder = ? ORDER BY updated_at DESC",
        (folder,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        if note_exists(r["name"]):
            result.append({"name": r["name"], "updated_at": r["updated_at"]})
    return result


def clear_all_notes():
    """Delete all notes, tags, and links from the index."""
    conn = get_conn()
    conn.execute("DELETE FROM tags")
    conn.execute("DELETE FROM links")
    conn.execute("DELETE FROM notes")
    conn.commit()
    conn.close()


def get_all_folders_with_counts():
    """Return folders that actually exist on disk, with note counts from DB."""
    folders = vault_folders()
    if not folders:
        return []
    conn = get_conn()
    placeholders = ",".join("?" for _ in folders)
    rows = conn.execute(
        f"SELECT folder, COUNT(*) as count FROM notes WHERE folder IN ({placeholders}) GROUP BY folder ORDER BY folder",
        folders,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

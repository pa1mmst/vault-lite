import os
import re
from datetime import datetime, timezone

VAULT_DIR = os.path.join(os.path.dirname(__file__), "vault")

# Regex for wiki-links: [[note name]]
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\](?!\])")
# Regex for tags: #word (not in code blocks, not part of URL)
TAG_RE = re.compile(r"(?<!\w)#([a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_\-/]*)")
# Regex for markdown frontmatter
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def ensure_vault_dir():
    os.makedirs(VAULT_DIR, exist_ok=True)


def get_note_path(name):
    # Sanitize: replace / and .. to prevent path traversal
    safe = name.replace("..", "").replace("/", "_")
    return os.path.join(VAULT_DIR, f"{safe}.md")


def note_exists(name):
    return os.path.exists(get_note_path(name))


def read_note(name):
    path = get_note_path(name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    stat = os.stat(path)
    return {
        "name": name,
        "content": content,
        "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def write_note(name, content):
    ensure_vault_dir()
    path = get_note_path(name)
    existed = os.path.exists(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    stat = os.stat(path)
    return {
        "name": name,
        "content": content,
        "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat() if existed else datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def delete_note(name):
    path = get_note_path(name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def list_notes():
    ensure_vault_dir()
    notes = []
    for f in sorted(os.listdir(VAULT_DIR)):
        if f.endswith(".md"):
            name = f[:-3]
            note = read_note(name)
            if note:
                notes.append(note)
    return notes


def parse_tags(content):
    """Extract #tags from content."""
    return list(set(TAG_RE.findall(content)))


def parse_wikilinks(content):
    """Extract [[wiki-links]] from content."""
    return list(set(WIKILINK_RE.findall(content)))


def strip_frontmatter(content):
    """Remove YAML frontmatter if present."""
    m = FRONTMATTER_RE.match(content)
    if m:
        return content[m.end():]
    return content

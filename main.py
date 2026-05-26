import os
import json
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from database import (
    init_db, migrate_db, index_note, remove_note, search_notes,
    get_all_tags, get_all_links, get_all_note_names, get_backlinks,
    get_notes_by_folder, get_all_folders_with_counts,
)
from vault import (
    read_note, write_note, delete_note, list_notes, get_folder,
    parse_tags, parse_wikilinks, strip_frontmatter, note_exists,
)

try:
    from weasyprint import HTML as WeasyPrintHTML
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    migrate_db()
    for note in list_notes():
        tags = parse_tags(note["content"])
        links = parse_wikilinks(note["content"])
        index_note(note["name"], note["content"], note["created_at"], note["updated_at"], tags, links, folder=note["folder"])
    yield

app = FastAPI(title="folio", lifespan=lifespan)

# ── Static files ──────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ── Markdown → HTML (minimal, no deps) ───────────────────────
def md_to_html(text):
    """Minimal Markdown → HTML converter."""
    import re
    lines = text.split("\n")
    html_lines = []
    in_code = False
    in_list = False
    in_olist = False

    for line in lines:
        stripped = line.strip()

        # Code block
        if stripped.startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = stripped[3:].strip()
                html_lines.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        # Horizontal rule
        if re.match(r'^\s*[-*_]{3,}\s*$', stripped):
            if in_list:
                html_lines.append("</ul>" if not in_olist else "</ol>")
                in_list = False
                in_olist = False
            html_lines.append("<hr>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            if in_list:
                html_lines.append("</ul>" if not in_olist else "</ol>")
                in_list = False
                in_olist = False
            html_lines.append(f"<blockquote>{inline(stripped[2:])}</blockquote>")
            continue

        # Close list when line doesn't match any list pattern
        is_ul = stripped.startswith("- ") or stripped.startswith("* ")
        is_ol = bool(re.match(r'^\d+\.\s', stripped))
        if in_list and not is_ul and not is_ol:
            html_lines.append("</ul>" if not in_olist else "</ol>")
            in_list = False
            in_olist = False

        # Headings
        if stripped.startswith("###### "):
            html_lines.append(f"<h6>{inline(stripped[7:])}</h6>")
        elif stripped.startswith("##### "):
            html_lines.append(f"<h5>{inline(stripped[6:])}</h5>")
        elif stripped.startswith("#### "):
            html_lines.append(f"<h4>{inline(stripped[5:])}</h4>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{inline(stripped[2:])}</h1>")
        # Unordered list item
        elif is_ul:
            if not in_list or in_olist:
                html_lines.append("<ul>")
                in_list = True
                in_olist = False
            html_lines.append(f"<li>{inline(stripped[2:])}</li>")
        # Ordered list item
        elif is_ol:
            if not in_list or not in_olist:
                html_lines.append("<ol>")
                in_list = True
                in_olist = True
            content = re.sub(r'^\d+\.\s', '', stripped)
            html_lines.append(f"<li>{inline(content)}</li>")
        # Empty line
        elif stripped == "":
            if in_list:
                html_lines.append("</ul>" if not in_olist else "</ol>")
                in_list = False
                in_olist = False
            html_lines.append("<br>")
        else:
            if in_list:
                html_lines.append("</ul>" if not in_olist else "</ol>")
                in_list = False
                in_olist = False
            html_lines.append(f"<p>{inline(line)}</p>")

    if in_list:
        html_lines.append("</ul>" if not in_olist else "</ol>")
    if in_code:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


def inline(text):
    """Process inline Markdown: bold, italic, code, links, wiki-links, tags."""
    # Code (protect first)
    parts = []
    i = 0
    while i < len(text):
        if text[i] == "`":
            end = text.find("`", i + 1)
            if end != -1:
                parts.append(f"<code>{text[i+1:end].replace('&','&amp;').replace('<','&lt;')}</code>")
                i = end + 1
                continue
        parts.append(text[i])
        i += 1
    text = "".join(parts)

    # Bold
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Images ![alt](url)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1">', text)
    # Wiki-links [[name]]
    text = re.sub(r"\[\[([^\]]+)\]\]", r'<a href="/note/\1" class="wikilink">\1</a>', text)
    # Regular links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank">\1</a>', text)
    # Tags #tag
    text = re.sub(r"(?<!\w)#([a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_\-/]*)",
                   r'<a href="/?tag=\1" class="tag">#\1</a>', text)

    return text


# ── HTML template ─────────────────────────────────────────────
BASE_STYLE = """
:root {
  --bg-primary: #f8f9fa;
  --bg-secondary: #ffffff;
  --bg-tertiary: #f1f3f5;
  --bg-hover: #f0f1f3;
  --bg-active: #e9ecef;
  --text-primary: #1a1a2e;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  --text-accent: #e07a5f;
  --accent: #e07a5f;
  --accent-hover: #c96a4f;
  --accent-muted: rgba(224, 122, 95, 0.1);
  --accent-glow: rgba(224, 122, 95, 0.18);
  --border: #e5e7eb;
  --border-muted: #f0f1f3;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-full: 9999px;
  --easing: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);
  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  --sidebar-width: 260px;
  --note-list-width: 300px;
  --header-height: 48px;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --duration-fast: 150ms;
  --duration-normal: 250ms;
  --font-size-xs: 0.6875rem;
  --font-size-sm: 0.813rem;
  --font-size-base: 0.875rem;
  --font-size-md: 1rem;
  --font-size-lg: 1.125rem;
  --font-size-xl: 1.375rem;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 400;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  min-height: 100vh;
}
a { color: var(--accent); text-decoration: none; transition: color 0.2s var(--easing); }
a:hover { color: var(--accent-hover); }
"""


def _folder_tree_html(folders, current_folder=""):
    """Build nested folder tree HTML from flat folder list."""
    tree = {}
    for folder in folders:
        parts = folder.split("/")
        current = tree
        path = ""
        for part in parts:
            path = f"{path}/{part}" if path else part
            if part not in current:
                current[part] = {"path": path, "children": {}}
            current = current[part]["children"]

    def _render(node, depth=0):
        html = ""
        for name, data in sorted(node.items()):
            style = f"padding-left:{16 + depth * 14}px;"
            active = ' folder-link-active' if data["path"] == current_folder else ''
            html += f'<a href="/?folder={data["path"]}" class="sidebar-folder-link{active}" style="{style}">{name}</a>'
            if data["children"]:
                html += _render(data["children"], depth + 1)
        return html

    return _render(tree)


def sidebar_html(active="notes", tags=None, folders=None, current_folder="", backlinks=None):
    nav_items = {"notes": {"label": "Notes", "href": "/", "icon": "file-text"}, "graph": {"label": "Graph", "href": "/graph", "icon": "graph"}}
    notes_svg = '<path d="M2 4h12M2 8h12M2 12h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
    graph_svg = '<circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/><path d="M5 8l2 2 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    nav_links = "".join(
        f'<a href="{v["href"]}" class="sidebar-link{" active" if k == active else ""}">'
        f'<svg class="sidebar-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">'
        f'{notes_svg if v["icon"] == "file-text" else graph_svg}'
        f'</svg>{v["label"]}</a>'
        for k, v in nav_items.items()
    )
    tag_links = ""
    if tags:
        for t in tags:
            tag_links += f'<a href="/?tag={t["tag"]}" class="sidebar-tag">#{t["tag"]}</a>'

    folder_links = _folder_tree_html(folders or [], current_folder) if folders else ""

    backlink_items = ""
    if backlinks is not None:
        if backlinks:
            backlink_items = "".join(
                f'<a href="/note/{bl["source_note"]}" class="sidebar-backlink-item">{bl["source_note"]}</a>'
                for bl in backlinks
            )
        else:
            backlink_items = '<div class="sidebar-backlink-empty">No backlinks</div>'

    return f"""<aside class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <div class="sidebar-brand"><a href="/">folio</a></div>
    <button class="sidebar-close" onclick="toggleSidebar()" aria-label="Close sidebar">&times;</button>
  </div>
  <nav class="sidebar-nav">{nav_links}</nav>
  <div class="sidebar-section">
    <button class="sidebar-collapse-btn" onclick="toggleFolderSection()" id="folderToggle">
      <svg class="chevron" id="folderChevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
        <path d="M4.5 3L7.5 6L4.5 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Folders
    </button>
    <div class="sidebar-folders" id="folderSection">{folder_links}</div>
  </div>
  <div class="sidebar-section">
    <button class="sidebar-collapse-btn" onclick="toggleTagSection()" id="tagToggle">
      <svg class="chevron" id="tagChevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
        <path d="M4.5 3L7.5 6L4.5 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Tags
    </button>
    <div class="sidebar-tags" id="tagSection">{tag_links}</div>
  </div>
  <div class="sidebar-section" id="backlinkSidebarSection">
    <button class="sidebar-collapse-btn" onclick="toggleBacklinkSection()" id="backlinkToggle">
      <svg class="chevron" id="backlinkChevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
        <path d="M4.5 3L7.5 6L4.5 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Backlinks
    </button>
    <div class="sidebar-backlinks" id="backlinkSection">{backlink_items}</div>
  </div>
</aside>"""


def render_page(title, body, active="notes", current_folder="", backlinks=None):
    tags = get_all_tags()
    folders = get_all_folders_with_counts()
    folder_names = sorted(set(f["folder"] for f in folders))
    sb = sidebar_html(active, tags, folder_names, current_folder, backlinks)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — folio</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>{BASE_STYLE}</style>
    <link rel="stylesheet" href="/static/style.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.0/styles/github.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.0/highlight.min.js"></script>
</head>
<body>
    <div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>
    <button class="hamburger" id="hamburgerBtn" onclick="toggleSidebar()" aria-label="Toggle sidebar">
        <span></span><span></span><span></span>
    </button>
    <div class="toast-container" id="toastContainer"></div>
    <div class="hotkeys-btn" id="hotkeysBtn" onclick="toggleHotkeys()" title="Keyboard shortcuts">?</div>
    <div class="hotkeys-modal" id="hotkeysModal">
        <div class="hotkeys-overlay" onclick="toggleHotkeys()"></div>
        <div class="hotkeys-content">
            <div class="hotkeys-header">
                <h3>Keyboard Shortcuts</h3>
                <button class="hotkeys-close" onclick="toggleHotkeys()">&times;</button>
            </div>
            <div class="hotkeys-list">
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Ctrl</kbd> + <kbd>B</kbd></span><span class="hotkey-desc">Bold</span></div>
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Ctrl</kbd> + <kbd>I</kbd></span><span class="hotkey-desc">Italic</span></div>
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Ctrl</kbd> + <kbd>S</kbd></span><span class="hotkey-desc">Save note</span></div>
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Ctrl</kbd> + <kbd>N</kbd></span><span class="hotkey-desc">New note</span></div>
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Ctrl</kbd> + <kbd>E</kbd></span><span class="hotkey-desc">Toggle edit/preview</span></div>
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Ctrl</kbd> + <kbd>K</kbd></span><span class="hotkey-desc">Search notes</span></div>
                <div class="hotkey-row"><span class="hotkey-keys"><kbd>Esc</kbd></span><span class="hotkey-desc">Close modal / Cancel</span></div>
            </div>
        </div>
    </div>
    <div class="app-layout">
        {sb}
        <main class="main-content">
            {body}
        </main>
    </div>
    <script>
    function toggleSidebar() {{
        document.getElementById('sidebar').classList.toggle('open');
        document.getElementById('sidebarOverlay').classList.toggle('open');
    }}
    function toggleFolderSection() {{
        var section = document.getElementById('folderSection');
        var chevron = document.getElementById('folderChevron');
        section.classList.toggle('collapsed');
        chevron.classList.toggle('rotated');
    }}
    function toggleTagSection() {{
        var section = document.getElementById('tagSection');
        var chevron = document.getElementById('tagChevron');
        section.classList.toggle('collapsed');
        chevron.classList.toggle('rotated');
    }}
    function toggleBacklinkSection() {{
        var section = document.getElementById('backlinkSection');
        var chevron = document.getElementById('backlinkChevron');
        section.classList.toggle('collapsed');
        chevron.classList.toggle('rotated');
    }}
    function showToast(message, type) {{
        var container = document.getElementById('toastContainer');
        var toast = document.createElement('div');
        toast.className = 'toast toast-' + type;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(function() {{
            toast.classList.add('toast-exit');
            setTimeout(function() {{ toast.remove(); }}, 300);
        }}, 4000);
    }}
    function toggleHotkeys() {{
        document.getElementById('hotkeysModal').classList.toggle('open');
    }}
    document.addEventListener('keydown', function(e) {{
        if (e.key === 'Escape') {{
            var modal = document.getElementById('hotkeysModal');
            if (modal && modal.classList.contains('open')) {{
                modal.classList.remove('open');
            }}
        }}
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {{
            e.preventDefault();
            var input = document.getElementById('searchInput');
            if (input) input.focus();
        }}
        if ((e.ctrlKey || e.metaKey) && e.key === 'n') {{
            e.preventDefault();
            window.location = '/edit/new';
        }}
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {{
            var saveBtn = document.querySelector('.editor-actions .btn-primary');
            if (saveBtn && typeof saveNote === 'function') {{
                e.preventDefault();
                saveNote();
            }}
        }}
    }});
    hljs.highlightAll();
    </script>
</body>
</html>"""


# ── Export helpers ─────────────────────────────────────────────

EXPORT_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #fff; color: #333; padding: 40px 20px; line-height: 1.6; }
.container { max-width: 800px; margin: 0 auto; }
a { color: #7c83fd; text-decoration: none; }
h1, h2, h3 { margin: 20px 0 10px; }
p { margin: 10px 0; line-height: 1.7; }
ul { margin: 10px 0; padding-left: 24px; }
li { margin: 4px 0; }
code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-size: 0.9rem; }
pre { background: #f5f5f5; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 12px 0; }
img { max-width: 100%; height: auto; border-radius: 4px; margin: 8px 0; }
blockquote { border-left: 3px solid #7c83fd; padding-left: 16px; margin: 12px 0; color: #666; }
hr { border: none; border-top: 1px solid #ddd; margin: 24px 0; }
.tag { display: inline-block; padding: 2px 8px; background: #eee; border-radius: 4px; font-size: 0.8rem; color: #7c83fd; margin-right: 4px; margin-bottom: 4px; }
.tags { margin: 8px 0 16px; }
"""


def render_export_html(name, html_content, tags):
    tags_html = "".join(f'<a class="tag">#{t}</a>' for t in tags)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name}</title>
    <style>{EXPORT_STYLE}</style>
</head>
<body>
    <div class="container">
        <h1>{name}</h1>
        <div class="tags">{tags_html}</div>
        <hr>
        {html_content}
    </div>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────




@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = "", tag: str = "", folder: str = ""):
    all_tags = get_all_tags()
    tag_filter = f'&tag={tag}' if tag else ""
    folder_filter = f'&folder={folder}' if folder else ""

    if q or tag:
        notes = search_notes(q, tag=tag if tag else None, folder=folder if folder else None)
    elif folder:
        notes = get_notes_by_folder(folder)
    else:
        notes = []
        for n in list_notes():
            notes.append({"name": n["name"], "folder": n["folder"], "updated_at": n["updated_at"]})

    # Build tag options
    tag_options = '<option value="">All tags</option>'
    for t in all_tags:
        selected = 'selected' if t['tag'] == tag else ''
        tag_options += f'<option value="{t["tag"]}" {selected}>#{t["tag"]} ({t["cnt"]})</option>'

    note_cards = ""
    for n in notes:
        note_data = read_note(n["name"])
        tags = parse_tags(note_data["content"]) if note_data else []
        tags_html = "".join(f'<a href="/?tag={t}" class="tag">#{t}</a>' for t in tags[:5])
        updated = n.get("updated_at", "")[:10]
        content_preview = ""
        note_folder = n.get("folder", "")
        if note_data:
            raw_lines = [l.strip() for l in note_data["content"].split("\n") if l.strip()]
            for line in raw_lines:
                if not line.startswith("---") and not line.startswith("# "):
                    content_preview = line[:140]
                    break
        folder_badge = f'<span class="note-folder-badge">{note_folder}</span>' if note_folder else ""
        note_cards += f"""
        <div class="note-card" onclick="if(!event.target.closest('a,button')){{window.location='/note/{n['name']}'}}">
            <div class="note-card-header">
                {folder_badge}
                <h3 class="note-card-title"><a href="/note/{n['name']}">{n['name'].split('/')[-1]}</a></h3>
            </div>
            {'<div class="note-card-preview">' + content_preview + '</div>' if content_preview else '<div class="note-card-preview" style="color:var(--text-muted);font-style:italic;">Empty note</div>'}
            <div class="note-card-footer">
                <div class="tags">{tags_html}</div>
                <span class="note-card-meta">{updated}</span>
            </div>
        </div>"""

    if not note_cards:
        skeleton = ""
        if not q and not tag and not folder:
            skeleton = """
            <div class="skeleton-note"><div class="skeleton-line w-55"></div><div class="skeleton-line w-85"></div><div class="skeleton-line w-40"></div></div>
            <div class="skeleton-note"><div class="skeleton-line w-45"></div><div class="skeleton-line w-75"></div><div class="skeleton-line w-35"></div></div>
            <div class="skeleton-note"><div class="skeleton-line w-60"></div><div class="skeleton-line w-80"></div><div class="skeleton-line w-30"></div></div>
            <div class="skeleton-note"><div class="skeleton-line w-50"></div><div class="skeleton-line w-70"></div><div class="skeleton-line w-45"></div></div>
            <div class="skeleton-note"><div class="skeleton-line w-65"></div><div class="skeleton-line w-60"></div><div class="skeleton-line w-50"></div></div>
            <div class="skeleton-note"><div class="skeleton-line w-40"></div><div class="skeleton-line w-90"></div><div class="skeleton-line w-25"></div></div>
            """
        msg = "No notes here yet." if folder else "No notes yet. Create your first one!"
        note_cards = f'<div class="empty"><p>{msg}</p></div>' + skeleton

    folder_breadcrumb = ""
    page_title_html = '<h1 class="page-title">Notes</h1>'
    if folder:
        parts = folder.split("/")
        crumbs = '<a href="/" class="folder-crumb">Notes</a>'
        path = ""
        for p in parts:
            path = f"{path}/{p}" if path else p
            crumbs += '<span class="folder-crumb-sep">/</span>'
            crumbs += f'<a href="/?folder={path}" class="folder-crumb">{p}</a>'
        folder_breadcrumb = f'<div class="folder-breadcrumb">{crumbs}</div>'
        page_title_html = f'<div>{folder_breadcrumb}<h1 class="page-title">Notes</h1></div>'
    body = f"""
    <div class="page-header">
        {page_title_html}
        <a href="/edit/new" class="btn btn-primary">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style="margin-right:2px">
                <path d="M7 2v10M2 7h10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
            New Note
        </a>
    </div>
    <div class="search-bar">
        <input type="text" name="q" placeholder="Search notes..." value="{q}" id="searchInput"
               onkeydown="if(event.key==='Enter')window.location='/?q='+encodeURIComponent(this.value)+'{tag_filter}{folder_filter}'">
        <select onchange="window.location='/?tag='+encodeURIComponent(this.value)+'&q={q}{folder_filter}'">
            {tag_options}
        </select>
    </div>
    <div class="note-list">{note_cards}</div>
    """
    return render_page("Notes", body, current_folder=folder)


@app.get("/note/{name:path}", response_class=HTMLResponse)
async def view_note(name: str):
    note = read_note(name)
    if not note:
        return render_page("Not found", '<div class="empty"><p>Note not found.</p><a href="/" class="back-link">Back to notes</a></div>')

    content = strip_frontmatter(note["content"])
    html_content = md_to_html(content)
    tags = parse_tags(note["content"])
    tags_html = "".join(f'<a href="/?tag={t}" class="tag">#{t}</a>' for t in tags)

    backlinks = get_backlinks(name)

    note_folder = note.get("folder", "")
    folder_breadcrumb = ""
    if note_folder:
        parts = note_folder.split("/")
        crumbs = ""
        path = ""
        for p in parts:
            path = f"{path}/{p}" if path else p
            crumbs += f'<a href="/?folder={path}" class="folder-crumb">{p}</a>'
            if p != parts[-1]:
                crumbs += '<span class="folder-crumb-sep">/</span>'
        folder_breadcrumb = f'<div class="folder-breadcrumb">{crumbs}</div>'

    body = f"""
    <div class="note-view">
        <a href="/" class="back-link">Back to notes</a>
        {folder_breadcrumb}
        <h2>{name.split('/')[-1]}</h2>
        <div class="tags">{tags_html}</div>
        <div class="note-view-content">{html_content}</div>
        <div class="note-view-actions">
            <a href="/edit/{name}" class="btn">Edit</a>
            <div class="dropdown">
                <button class="btn" onclick="toggleExport(event)">Export</button>
                <div class="dropdown-menu" id="exportMenu">
                    <a href="/api/export-pdf/{name}" class="dropdown-item" target="_blank">Export PDF</a>
                    <a href="/api/export-html/{name}" class="dropdown-item" target="_blank">Export HTML</a>
                </div>
            </div>
            <button class="btn btn-danger" onclick="if(confirm('Delete?')){{showToast('Note deleted','success');fetch('/api/note/{name}',{{method:'DELETE'}}).then(function(){{setTimeout(function(){{window.location='/'}},300);}});}}">Delete</button>
        </div>
    <script>
    function toggleExport(e) {{
        e.stopPropagation();
        document.getElementById('exportMenu').classList.toggle('show');
    }}
    document.addEventListener('click', function() {{
        var m = document.getElementById('exportMenu');
        if (m) m.classList.remove('show');
    }});
    </script>
    </div>
    """
    return render_page(name, body, current_folder=note_folder, backlinks=backlinks)


@app.get("/edit/{name:path}", response_class=HTMLResponse)
async def edit_note(name: str):
    note = read_note(name)
    content = note["content"] if note else f"# {name}\n\nStart writing...\n"
    is_new = " (new)" if not note else ""
    body = f"""
    <div class="editor-title" style="display:flex;align-items:center;gap:10px">
        <span>Edit: {name}{is_new}</span>
        <span class="editor-status" id="editorStatus"></span>
    </div>
    <div class="editor-layout">
        <div class="editor-pane">
            <div class="toolbar" id="toolbar">
                <button type="button" data-cmd="undo" title="Undo (Ctrl+Z)">&#x21A9;</button>
                <button type="button" data-cmd="redo" title="Redo (Ctrl+Shift+Z)">&#x21AA;</button>
                <span class="separator"></span>
                <button type="button" data-cmd="bold" title="Bold (Ctrl+B)"><strong>B</strong></button>
                <button type="button" data-cmd="italic" title="Italic (Ctrl+I)"><em>I</em></button>
                <button type="button" data-cmd="strikethrough" title="Strikethrough"><span style="text-decoration:line-through">S</span></button>
                <span class="separator"></span>
                <button type="button" data-cmd="h1" title="Heading 1">H1</button>
                <button type="button" data-cmd="h2" title="Heading 2">H2</button>
                <button type="button" data-cmd="h3" title="Heading 3">H3</button>
                <span class="separator"></span>
                <button type="button" data-cmd="link" title="Link">Link</button>
                <button type="button" data-cmd="image" title="Image">Img</button>
                <button type="button" data-cmd="code" title="Code">&lt;/&gt;</button>
                <button type="button" data-cmd="list" title="List">List</button>
                <button type="button" data-cmd="quote" title="Quote">Quote</button>
                <span class="separator"></span>
                <button type="button" data-cmd="sourceToggle" title="Toggle source view" class="toolbar-toggle" id="sourceToggleBtn">&lt;/&gt;</button>
                <span class="editor-word-count" id="wordCount"></span>
            </div>
            <div id="wysiwygEditor" contenteditable="true" class="wysiwyg-editor"></div>
            <textarea id="sourceEditor" class="source-editor" style="display:none">{content}</textarea>
            <textarea id="markdownBuffer" style="display:none"></textarea>
        </div>
        <div class="preview-pane" id="preview"></div>
    </div>
    <div class="editor-actions">
        <button class="btn btn-primary" onclick="saveNote()">Save</button>
        <a href="/note/{name}" class="btn">Cancel</a>
    </div>

    <div class="attachments-panel" id="attachmentsPanel">
        <div class="attachments-header">
            <h3>Attachments</h3>
            <span class="attachments-count" id="attachmentsCount">0</span>
        </div>
        <div class="drop-zone" id="dropZone">
            <div class="drop-zone-content">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="17 8 12 3 7 8"/>
                    <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                <span>Drop images here or click to upload</span>
            </div>
            <input type="file" id="fileInput" accept="image/*" multiple hidden>
        </div>
        <div class="attachments-grid" id="attachmentsGrid"></div>
    </div>

    <script>
    const wysiwygEditor = document.getElementById('wysiwygEditor');
    const sourceEditor = document.getElementById('sourceEditor');
    const markdownBuffer = document.getElementById('markdownBuffer');
    const preview = document.getElementById('preview');
    const wordCountEl = document.getElementById('wordCount');
    const editorStatus = document.getElementById('editorStatus');

    let isSourceMode = false;
    let dirty = false;
    const NOTE_NAME = '{name}';

    // ── richToMarkdown: Convert contenteditable HTML to markdown ─────
    function richToMarkdown(html) {{
        const div = document.createElement('div');
        div.innerHTML = html;
        const lines = [];

        function collectInline(node) {{
            if (node.nodeType === Node.TEXT_NODE) return node.textContent;
            if (node.nodeType !== Node.ELEMENT_NODE) return '';
            const tag = node.tagName.toLowerCase();
            const inner = Array.from(node.childNodes).map(collectInline).join('');
            switch (tag) {{
                case 'strong': case 'b': return `**${{inner}}**`;
                case 'em': case 'i': return `*${{inner}}*`;
                case 's': case 'strike': case 'del': return `~~${{inner}}~~`;
                case 'code': return `\`${{inner}}\``;
                case 'a': return `[${{inner}}](${{node.getAttribute('href') || ''}})`;
                case 'img': return `![${{node.getAttribute('alt') || ''}}](${{node.getAttribute('src') || ''}})`;
                case 'br': return '\\n';
                default: return inner;
            }}
        }}

        function processBlock(node) {{
            if (node.nodeType === Node.TEXT_NODE) {{
                const text = node.textContent;
                if (text.trim()) lines.push(text);
                return;
            }}
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const tag = node.tagName.toLowerCase();
            const children = Array.from(node.childNodes);

            if (tag === 'p') {{
                const text = children.map(collectInline).join('').trim();
                if (text) lines.push(text);
                lines.push('');
            }} else if (tag === 'div') {{
                children.forEach(processBlock);
            }} else if (tag === 'h1' || tag === 'h2' || tag === 'h3' || tag === 'h4' || tag === 'h5' || tag === 'h6') {{
                const level = parseInt(tag[1]);
                const prefix = '#'.repeat(level);
                const text = children.map(collectInline).join('').trim();
                lines.push(`${{prefix}} ${{text}}`);
                lines.push('');
            }} else if (tag === 'ul' || tag === 'ol') {{
                const isOrdered = tag === 'ol';
                children.forEach((li, idx) => {{
                    if (li.nodeType === Node.ELEMENT_NODE && li.tagName.toLowerCase() === 'li') {{
                        const text = Array.from(li.childNodes).map(collectInline).join('').trim();
                        lines.push(isOrdered ? `${{idx + 1}}. ${{text}}` : `- ${{text}}`);
                    }}
                }});
                lines.push('');
            }} else if (tag === 'blockquote') {{
                const text = children.map(collectInline).join('').trim();
                lines.push(`> ${{text}}`);
                lines.push('');
            }} else if (tag === 'pre') {{
                const code = node.querySelector('code');
                const codeText = code ? code.textContent : node.textContent;
                lines.push('```');
                lines.push(codeText);
                lines.push('```');
                lines.push('');
            }} else if (tag === 'hr') {{
                lines.push('---');
                lines.push('');
            }} else if (tag === 'br') {{
                lines.push('');
            }} else {{
                const text = children.map(collectInline).join('').trim();
                if (text) lines.push(text);
            }}
        }}

        Array.from(div.childNodes).forEach(processBlock);
        return lines.join('\\n').replace(/\\n{{3,}}/g, '\\n\\n').trim();
    }}

    // ── Enhanced markdownToHtml for preview ──────────────────────
    function markdownToHtml(text) {{
        let lines = text.split('\\n');
        let html = [];
        let inCode = false;
        let inList = false;
        let inOrderedList = false;

        for (let line of lines) {{
            let s = line.trim();

            if (s.startsWith('```')) {{
                if (inCode) {{ html.push('</code></pre>'); inCode = false; }}
                else {{
                    let lang = s.slice(3).trim();
                    html.push(lang ? '<pre><code class="language-'+lang+'">' : '<pre><code>');
                    inCode = true;
                }}
                continue;
            }}
            if (inCode) {{
                html.push(line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'));
                continue;
            }}

            // Horizontal rule
            if (/^\s*[-*_]{{3,}}\s*$/.test(s)) {{
                if (inList) {{ html.push('</ul>'); inList = false; }}
                html.push('<hr>');
                continue;
            }}

            // Blockquote
            if (s.startsWith('> ')) {{
                if (inList) {{ html.push('</ul>'); inList = false; }}
                html.push('<blockquote>'+inline(s.slice(2))+'</blockquote>');
                continue;
            }}

            // Close lists when needed
            if (inList && !s.startsWith('- ') && !s.startsWith('* ') && !/^\d+\.\s/.test(s)) {{
                html.push('</ul>');
                inList = false;
            }}

            // Headings
            if (s.startsWith('###### ')) {{ html.push('<h6>'+inline(s.slice(7))+'</h6>'); }}
            else if (s.startsWith('##### ')) {{ html.push('<h5>'+inline(s.slice(6))+'</h5>'); }}
            else if (s.startsWith('#### ')) {{ html.push('<h4>'+inline(s.slice(5))+'</h4>'); }}
            else if (s.startsWith('### ')) {{ html.push('<h3>'+inline(s.slice(4))+'</h3>'); }}
            else if (s.startsWith('## ')) {{ html.push('<h2>'+inline(s.slice(3))+'</h2>'); }}
            else if (s.startsWith('# ')) {{ html.push('<h1>'+inline(s.slice(2))+'</h1>'); }}
            else if (s.startsWith('- ') || s.startsWith('* ')) {{
                if (!inList || inOrderedList) {{ html.push('<ul>'); inList = true; inOrderedList = false; }}
                html.push('<li>'+inline(s.slice(2))+'</li>');
            }}
            else if (/^\d+\.\s/.test(s)) {{
                if (!inList || !inOrderedList) {{ html.push('<ol>'); inList = true; inOrderedList = true; }}
                html.push('<li>'+inline(s.replace(/^\d+\.\s/, ''))+'</li>');
            }}
            else if (s === '') {{ html.push('<br>'); }}
            else {{ html.push('<p>'+inline(line)+'</p>'); }}
        }}
        if (inList) html.push(inOrderedList ? '</ol>' : '</ul>');
        if (inCode) html.push('</code></pre>');
        return html.join('\\n');
    }}

    function inline(text) {{
        if (!text) return '';
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
        text = text.replace(/~~(.+?)~~/g, '<s>$1</s>');
        text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1">');
        text = text.replace(/\[\[([^\]]+)\]\]/g, '<a href="/note/$1" class="wikilink">$1</a>');
        text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
        text = text.replace(/(?:^|\s)#([a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_\-/]*)/g, '$1<a href="/?tag=$2" class="tag">#$2</a>');
        return text;
    }}

    // ── Get current markdown content (from whichever mode is active) ──
    function getMarkdown() {{
        if (isSourceMode) return sourceEditor.value;
        return richToMarkdown(wysiwygEditor.innerHTML) || markdownBuffer.value;
    }}

    // ── Update preview and word count ─────────────────────────────
    function updatePreview() {{
        try {{
            const md = getMarkdown() || '';
            markdownBuffer.value = md;
            preview.innerHTML = markdownToHtml(md);
            try {{ preview.querySelectorAll('pre code').forEach(function(b) {{ hljs.highlightElement(b); }}); }} catch(e) {{}}
            // Word count
            const words = md.trim() ? md.trim().split(/\\s+/).length : 0;
            const chars = md.length;
            wordCountEl.textContent = words + ' words | ' + chars + ' chars';
        }} catch(e) {{
            console.error('Preview update failed:', e);
        }}
    }}

    let debounceTimer;
    function schedulePreview() {{
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(updatePreview, 200);
    }}

    // ── Init editor ──────────────────────────────────────────────
    function initEditor() {{
        const md = sourceEditor.value;
        markdownBuffer.value = md;
        wysiwygEditor.innerHTML = markdownToHtml(md);
        updatePreview();
    }}

    // ── WYSIWYG Editor input ──────────────────────────────────
    wysiwygEditor.addEventListener('input', function() {{
        schedulePreview();
        markDirty();
    }});

    // ── Source Editor input ──────────────────────────────────
    sourceEditor.addEventListener('input', function() {{
        schedulePreview();
        markDirty();
    }});

    // ── Toolbar ──────────────────────────────────────────────────
    document.getElementById('toolbar').addEventListener('click', function(e) {{
        const btn = e.target.closest('button');
        if (!btn) return;
        e.preventDefault();
        const cmd = btn.dataset.cmd;
        if (!cmd) return;

        if (cmd === 'sourceToggle') {{
            toggleSourceMode();
            return;
        }}
        if (cmd === 'undo') {{
            if (isSourceMode) {{ document.execCommand('undo'); return; }}
            document.execCommand('undo');
            schedulePreview();
            return;
        }}
        if (cmd === 'redo') {{
            if (isSourceMode) {{ document.execCommand('redo'); return; }}
            document.execCommand('redo');
            schedulePreview();
            return;
        }}

        if (isSourceMode) {{
            insertMarkdownSource(cmd);
            return;
        }}

        wysiwygEditor.focus();
        switch (cmd) {{
            case 'bold':
                document.execCommand('bold');
                break;
            case 'italic':
                document.execCommand('italic');
                break;
            case 'strikethrough':
                document.execCommand('strikeThrough');
                break;
            case 'h1':
                document.execCommand('formatBlock', false, '<h1>');
                break;
            case 'h2':
                document.execCommand('formatBlock', false, '<h2>');
                break;
            case 'h3':
                document.execCommand('formatBlock', false, '<h3>');
                break;
            case 'link': {{
                const sel = window.getSelection().toString().trim();
                const url = prompt('Enter URL:', 'https://');
                if (url) {{
                    if (sel) {{
                        document.execCommand('createLink', false, url);
                    }} else {{
                        document.execCommand('insertHTML', false, '<a href="' + url.replace(/"/g, '&quot;') + '">' + url.replace(/^https?:\/\//, '').replace(/\/$/, '') + '</a>');
                    }}
                }}
                break;
            }}
            case 'image': {{
                const url = prompt('Enter image URL:', 'https://');
                if (url) {{
                    document.execCommand('insertHTML', false, '<img src="' + url.replace(/"/g, '&quot;') + '" alt="image">');
                }}
                break;
            }}
            case 'code':
                if (window.getSelection().toString().trim()) {{
                    document.execCommand('insertHTML', false, '<code>' + window.getSelection().toString() + '</code>');
                }} else {{
                    document.execCommand('insertHTML', false, '<code>code</code>');
                    const sel = window.getSelection();
                    if (sel.rangeCount > 0) {{
                        const range = sel.getRangeAt(0);
                        range.setStart(range.startContainer, range.startOffset - 4);
                        range.setEnd(range.startContainer, range.startOffset);
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }}
                }}
                break;
            case 'list':
                document.execCommand('insertUnorderedList');
                break;
            case 'quote':
                document.execCommand('formatBlock', false, '<blockquote>');
                break;
        }}
        wysiwygEditor.focus();
        schedulePreview();
    }});

    // ── Source mode markdown insertion ─────────────────────────
    function getLineStart(text, pos) {{
        return text.lastIndexOf('\\n', pos - 1) + 1;
    }}

    function insert(ta, str, cursorOffset) {{
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        const before = ta.value.substring(0, start);
        const after = ta.value.substring(end);
        ta.value = before + str + after;
        const pos = start + (end > start ? str.length - (end - start) : cursorOffset);
        ta.setSelectionRange(pos, pos);
        ta.dispatchEvent(new Event('input'));
    }}

    function insertAtLine(ta, lineStart, oldLine, newLine, cursorPos) {{
        const before = ta.value.substring(0, lineStart);
        const after = ta.value.substring(lineStart + oldLine.length);
        ta.value = before + newLine + after;
        ta.setSelectionRange(cursorPos, cursorPos);
        ta.dispatchEvent(new Event('input'));
    }}

    function insertMarkdownSource(cmd) {{
        const ta = sourceEditor;
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        const text = ta.value;
        const sel = text.substring(start, end);
        const lineStart = getLineStart(text, start);
        const lineEnd = text.indexOf('\\n', start);
        const line = text.substring(lineStart, lineEnd === -1 ? text.length : lineEnd);
        const lineSelStart = start - lineStart;

        switch (cmd) {{
            case 'bold': {{
                const wrap = sel || 'bold text';
                insert(ta, `**${{wrap}}**`, 2);
                break;
            }}
            case 'italic': {{
                const wrap = sel || 'italic text';
                insert(ta, `*${{wrap}}*`, 1);
                break;
            }}
            case 'h1':
            case 'h2':
            case 'h3': {{
                const prefix = {{h1:'# ', h2:'## ', h3:'### '}}[cmd];
                if (line.startsWith(prefix)) {{
                    insertAtLine(ta, lineStart, line, line.slice(prefix.length), 0);
                }} else {{
                    const newLine = prefix + line;
                    insertAtLine(ta, lineStart, line, newLine, prefix.length + lineSelStart);
                }}
                break;
            }}
            case 'link': {{
                const wrap = sel || 'text';
                insert(ta, `[${{wrap}}](url)`, 1);
                break;
            }}
            case 'image': {{
                const wrap = sel || 'alt text';
                insert(ta, `![${{wrap}}](url)`, 1);
                break;
            }}
            case 'code': {{
                const wrap = sel || 'code';
                insert(ta, '`' + wrap + '`', 1);
                break;
            }}
            case 'list': {{
                const prefix = '- ';
                if (line.startsWith(prefix)) {{
                    insertAtLine(ta, lineStart, line, line.slice(2), 0);
                }} else {{
                    const newLine = prefix + line;
                    insertAtLine(ta, lineStart, line, newLine, 2 + lineSelStart);
                }}
                break;
            }}
            case 'quote': {{
                const prefix = '> ';
                if (line.startsWith(prefix)) {{
                    insertAtLine(ta, lineStart, line, line.slice(2), 0);
                }} else {{
                    const newLine = prefix + line;
                    insertAtLine(ta, lineStart, line, newLine, 2 + lineSelStart);
                }}
                break;
            }}
        }}
        ta.focus();
        schedulePreview();
    }}

    // ── Source mode toggle ──────────────────────────────────────
    function toggleSourceMode() {{
        isSourceMode = !isSourceMode;
        const btn = document.getElementById('sourceToggleBtn');
        if (isSourceMode) {{
            // Switch to source: sync textarea from wysiwyg
            sourceEditor.value = getMarkdown();
            wysiwygEditor.style.display = 'none';
            sourceEditor.style.display = 'block';
            sourceEditor.style.height = wysiwygEditor.offsetHeight + 'px';
            sourceEditor.focus();
            btn.classList.add('active');
            editorStatus.textContent = 'source mode';
        }} else {{
            // Switch to wysiwyg: render markdown as HTML
            wysiwygEditor.innerHTML = markdownToHtml(sourceEditor.value);
            wysiwygEditor.style.display = 'block';
            sourceEditor.style.display = 'none';
            wysiwygEditor.focus();
            btn.classList.remove('active');
            editorStatus.textContent = '';
        }}
        schedulePreview();
    }}

    // ── Keyboard shortcuts ────────────────────────────────────
    wysiwygEditor.addEventListener('keydown', function(e) {{
        if (e.key === 'Tab') {{
            e.preventDefault();
            document.execCommand('insertHTML', false, '    ');
            return;
        }}
        const mod = e.ctrlKey || e.metaKey;
        if (!mod) return;
        if (e.key === 'e') {{
            e.preventDefault();
            toggleViewMode();
            return;
        }}
        if (e.key === 'z' && !e.shiftKey) {{
            document.execCommand('undo');
            e.preventDefault();
            schedulePreview();
            return;
        }}
        if ((e.key === 'z' && e.shiftKey) || e.key === 'y') {{
            document.execCommand('redo');
            e.preventDefault();
            schedulePreview();
            return;
        }}
        // Let browser handle bold (Ctrl+B) and italic (Ctrl+I)
    }});

    sourceEditor.addEventListener('keydown', function(e) {{
        const mod = e.ctrlKey || e.metaKey;
        if (!mod) return;
        const map = {{b:'bold', i:'italic'}};
        const cmd = map[e.key];
        if (cmd) {{
            e.preventDefault();
            insertMarkdownSource(cmd);
            return;
        }}
        if (e.key === 'e') {{
            e.preventDefault();
            toggleViewMode();
        }}
    }});

    // ── Image upload ─────────────────────────────────────────
    async function uploadImage(file) {{
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/api/upload', {{ method: 'POST', body: formData }});
        const data = await resp.json();
        if (data.url) {{
            if (isSourceMode) {{
                const ta = sourceEditor;
                const pos = ta.selectionStart;
                const before = ta.value.substring(0, pos);
                const after = ta.value.substring(ta.selectionEnd);
                const imgMd = `![](${{data.url}})\\n`;
                ta.value = before + imgMd + after;
                ta.setSelectionRange(pos + imgMd.length, pos + imgMd.length);
                ta.dispatchEvent(new Event('input'));
            }} else {{
                document.execCommand('insertHTML', false, '<img src="' + data.url + '" alt="image">');
            }}
            schedulePreview();
        }}
    }}

    // Drag and drop
    wysiwygEditor.addEventListener('dragover', function(e) {{
        e.preventDefault();
        wysiwygEditor.style.outline = '2px dashed var(--accent)';
    }});
    wysiwygEditor.addEventListener('dragleave', function(e) {{
        e.preventDefault();
        wysiwygEditor.style.outline = '';
    }});
    wysiwygEditor.addEventListener('drop', async function(e) {{
        e.preventDefault();
        wysiwygEditor.style.outline = '';
        const files = e.dataTransfer.files;
        for (const file of files) {{
            if (file.type.startsWith('image/')) {{
                await uploadImage(file);
            }}
        }}
    }});

    // Paste handling for wysiwyg
    wysiwygEditor.addEventListener('paste', async function(e) {{
        const items = e.clipboardData.items;
        let hasImage = false;
        for (const item of items) {{
            if (item.type.startsWith('image/')) {{
                hasImage = true;
                e.preventDefault();
                const file = item.getAsFile();
                if (file) await uploadImage(file);
                return;
            }}
        }}
        if (!hasImage && !isSourceMode) {{
            e.preventDefault();
            const text = e.clipboardData.getData('text/plain');
            document.execCommand('insertText', false, text);
        }}
    }});

    // ── Save ────────────────────────────────────────────────────
    function saveNote() {{
        let content = getMarkdown();
        const name = '{name}' === 'new' ? prompt('Note name:') : '{name}';
        if (!name) return;
        const btn = document.querySelector('.editor-actions .btn-primary');
        const originalText = btn.textContent;
        btn.textContent = 'Saving...';
        btn.disabled = true;
        btn.classList.add('btn-saving');
        fetch('/api/note', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{name, content}})
        }}).then(function(r) {{
            if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.error || 'Save failed'); }});
            return r.json();
        }}).then(function(d) {{
            dirty = false;
            btn.textContent = 'Saved!';
            btn.disabled = false;
            btn.classList.remove('btn-saving');
            btn.classList.add('btn-saved');
            showToast('Note saved', 'success');
            setTimeout(function() {{
                window.location = '/note/' + d.name;
            }}, 600);
        }}).catch(function(e) {{
            console.error('Save error:', e);
            btn.textContent = originalText;
            btn.disabled = false;
            btn.classList.remove('btn-saving');
            showToast(e.message || 'Save failed', 'error');
        }});
    }}

    // ── Auto-save ─────────────────────────────────────────────
    function markDirty() {{
        dirty = true;
    }}

    function autoSave() {{
        if (NOTE_NAME === 'new') return;
        const content = getMarkdown();
        fetch('/api/note', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{name: NOTE_NAME, content}})
        }}).then(function(r) {{
            if (!r.ok) throw new Error('Auto-save HTTP ' + r.status);
            return r.json();
        }}).then(function() {{
            dirty = false;
        }}).catch(function(e) {{
            console.error('Auto-save error:', e);
        }});
    }}

    let autoSaveTimer;
    function startAutoSave() {{
        if (autoSaveTimer) return;
        autoSaveTimer = setInterval(function() {{
            if (dirty) autoSave();
        }}, 3000);
    }}

    // ── View mode toggle ──────────────────────────────────────
    let viewMode = 0;
    const editorPane = document.querySelector('.editor-pane');
    const previewPane = document.querySelector('.preview-pane');

    function toggleViewMode() {{
        viewMode = (viewMode + 1) % 3;
        editorPane.classList.remove('mode-hidden', 'mode-full');
        previewPane.classList.remove('mode-hidden', 'mode-full');
        if (viewMode === 1) {{
            previewPane.classList.add('mode-hidden');
            editorPane.classList.add('mode-full');
        }} else if (viewMode === 2) {{
            editorPane.classList.add('mode-hidden');
            previewPane.classList.add('mode-full');
        }}
        showToast(
            viewMode === 0 ? 'Split view' : viewMode === 1 ? 'Edit mode' : 'Preview mode',
            'info'
        );
    }}

    // ── Attachments panel ────────────────────────────────────
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const attachmentsGrid = document.getElementById('attachmentsGrid');
    const attachmentsCount = document.getElementById('attachmentsCount');

    function formatSize(bytes) {{
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }}

    function loadAttachments() {{
        fetch('/api/attachments')
            .then(function(r) {{ return r.json(); }})
            .then(function(files) {{
                attachmentsCount.textContent = files.length;
                if (files.length === 0) {{
                    attachmentsGrid.innerHTML = '<div class="attachments-empty">No attachments yet</div>';
                    return;
                }}
                var html = '';
                for (var i = 0; i < files.length; i++) {{
                    var f = files[i];
                    html += '<div class="attachment-item">'
                        + '<div class="attachment-thumb">'
                        + '<img src="' + f.url + '" alt="' + f.filename + '" loading="lazy">'
                        + '</div>'
                        + '<div class="attachment-info">'
                        + '<span class="attachment-name" title="' + f.filename + '">' + f.filename + '</span>'
                        + '<span class="attachment-size">' + formatSize(f.size) + '</span>'
                        + '</div>'
                        + '<button class="attachment-delete" onclick="deleteAttachment(\'' + f.filename + '\', this)" title="Delete attachment">&times;</button>'
                        + '</div>';
                }}
                attachmentsGrid.innerHTML = html;
            }});
    }}

    function deleteAttachment(filename, btn) {{
        if (!confirm('Delete "' + filename + '"?')) return;
        btn.disabled = true;
        btn.textContent = '...';
        fetch('/api/upload/' + encodeURIComponent(filename), {{ method: 'DELETE' }})
            .then(function(r) {{ return r.json(); }})
            .then(function(data) {{
                if (data.ok) {{
                    showToast('Attachment deleted', 'success');
                    loadAttachments();
                }} else {{
                    showToast('Delete failed: ' + (data.error || 'unknown'), 'error');
                    btn.disabled = false;
                    btn.textContent = '\u00d7';
                }}
            }})
            .catch(function() {{
                showToast('Delete failed', 'error');
                btn.disabled = false;
                btn.textContent = '\u00d7';
            }});
    }}

    dropZone.addEventListener('click', function() {{
        fileInput.click();
    }});

    dropZone.addEventListener('dragover', function(e) {{
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.add('drop-zone-active');
    }});

    dropZone.addEventListener('dragleave', function(e) {{
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('drop-zone-active');
    }});

    dropZone.addEventListener('drop', function(e) {{
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('drop-zone-active');
        var files = e.dataTransfer.files;
        uploadFiles(files);
    }});

    fileInput.addEventListener('change', function() {{
        uploadFiles(fileInput.files);
        fileInput.value = '';
    }});

    function uploadFiles(files) {{
        var count = files.length;
        var done = 0;
        for (var i = 0; i < files.length; i++) {{
            var file = files[i];
            if (!file.type.startsWith('image/')) {{
                showToast('Skipped: ' + file.name + ' (not an image)', 'info');
                continue;
            }}
            (function(f) {{
                uploadImage(f).then(function() {{
                    done++;
                    if (done >= count) loadAttachments();
                }});
            }})(file);
        }}
        if (count === 0) loadAttachments();
    }}

    // ── Init ────────────────────────────────────────────────────
    initEditor();
    startAutoSave();
    loadAttachments();

    if ('{name}' === 'new') {{
        if (isSourceMode) sourceEditor.focus();
        else wysiwygEditor.focus();
    }}
    </script>
    """
    return render_page(f"Edit: {name}", body)


@app.get("/graph", response_class=HTMLResponse)
async def graph_page():
    body = """
    <div class="page-header">
        <h1 class="page-title">Graph</h1>
    </div>
    <div class="graph-container" id="graph">
      <div class="graph-loading" id="graphLoading">Loading graph…</div>
    </div>
    <script src="https://d3js.org/d3.v7.min.js" onerror="document.getElementById('graphLoading').textContent='Failed to load D3.js — check internet connection'"></script>
    <script>
    (function() {
      function renderGraph() {
        if (typeof d3 === 'undefined') { setTimeout(renderGraph, 200); return; }
        var loading = document.getElementById('graphLoading');
        if (loading) loading.remove();
        fetch('/api/graph')
          .then(function(r) { return r.json(); })
          .then(function(data) {
            var container = document.getElementById('graph');
            var width = container.clientWidth || 800;
            var height = container.clientHeight || 600;
            if (width < 10 || height < 10) { width = 800; height = 600; }
            var svg = d3.select('#graph').append('svg').attr('width', width).attr('height', height);
            var g = svg.append('g');
            var zoom = d3.zoom().scaleExtent([0.1, 4]).on('zoom', function(e) { g.attr('transform', e.transform); });
            svg.call(zoom);
            var simulation = d3.forceSimulation(data.nodes)
              .force('link', d3.forceLink(data.links).id(function(d) { return d.id; }).distance(120))
              .force('charge', d3.forceManyBody().strength(-250))
              .force('center', d3.forceCenter(width / 2, height / 2));
            var link = g.append('g').selectAll('line')
              .data(data.links).enter().append('line')
              .attr('stroke', 'var(--border)').attr('stroke-width', 1.5);
            var node = g.append('g').selectAll('g')
              .data(data.nodes).enter().append('g')
              .style('cursor', 'pointer')
              .call(d3.drag()
                .on('start', function(e, d) { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                .on('drag', function(e, d) { d.fx = e.x; d.fy = e.y; })
                .on('end', function(e, d) { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));
            node.append('circle').attr('r', 6).attr('fill', 'var(--accent)').attr('stroke', 'var(--bg-primary)').attr('stroke-width', 2);
            node.append('text').text(function(d) { return d.id; })
              .attr('x', 12).attr('y', 4)
              .attr('fill', 'var(--text-secondary)').attr('font-size', '12px')
              .attr('font-family', "'Inter', system-ui, sans-serif");
            node.on('click', function(e, d) { window.location = '/note/' + d.id; });
            node.on('mouseenter', function(e, d) {
              d3.select(this).select('circle').attr('r', 9).attr('fill', 'var(--accent-hover)');
              d3.select(this).select('text').attr('fill', 'var(--text-primary)').attr('font-weight', '600');
            }).on('mouseleave', function(e, d) {
              d3.select(this).select('circle').attr('r', 6).attr('fill', 'var(--accent)');
              d3.select(this).select('text').attr('fill', 'var(--text-secondary)').attr('font-weight', '400');
            });
            simulation.on('tick', function() {
              link.attr('x1', function(d) { return d.source.x; }).attr('y1', function(d) { return d.source.y; })
                  .attr('x2', function(d) { return d.target.x; }).attr('y2', function(d) { return d.target.y; });
              node.attr('transform', function(d) { return 'translate(' + d.x + ',' + d.y + ')'; });
            });
          })
          .catch(function(err) {
            var el = document.getElementById('graph');
            if (el) el.innerHTML = '<div class="graph-error">Graph error: ' + err.message + '</div>';
          });
      }
      if (document.readyState === 'complete') { renderGraph(); }
      else { document.addEventListener('DOMContentLoaded', renderGraph); }
    })();
    </script>
    """
    return render_page("Graph", body, active="graph")


# ── API ───────────────────────────────────────────────────────

@app.get("/api/folders")
def api_folders():
    folders = get_all_folders_with_counts()
    return JSONResponse(folders)


@app.get("/api/search")
def api_search(q: str = "", tag: str = "", folder: str = ""):
    return JSONResponse(search_notes(q, tag=tag or None, folder=folder or None))


@app.get("/api/graph")
def api_graph():
    links = get_all_links()
    names = set(get_all_note_names())
    nodes = [{"id": n} for n in names]
    link_data = []
    for l in links:
        src = l["source_note"]
        tgt = l["target_note"]
        if src in names and tgt in names:
            link_data.append({"source": src, "target": tgt})
    return JSONResponse({"nodes": nodes, "links": link_data})


@app.get("/api/backlinks/{name:path}")
def api_backlinks(name: str):
    return JSONResponse(get_backlinks(name))


@app.post("/api/note")
async def api_save_note(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    content = data.get("content", "")
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)

    note = write_note(name, content)
    folder = note.get("folder", "")
    tags = parse_tags(content)
    links = parse_wikilinks(content)
    index_note(name, content, note["created_at"], note["updated_at"], tags, links, folder=folder)
    return JSONResponse({"name": name, "folder": folder, "updated_at": note["updated_at"]})


@app.delete("/api/note/{name:path}")
def api_delete_note(name: str):
    delete_note(name)
    remove_note(name)
    return JSONResponse({"ok": True})


# ── Image upload ──────────────────────────────────────────

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            {"error": f"File type '{ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"},
            status_code=400,
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        return JSONResponse({"error": "File too large. Maximum size is 5 MB."}, status_code=400)

    upload_dir = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, "wb") as f:
        f.write(contents)

    return JSONResponse({"url": f"/static/uploads/{filename}"})


@app.get("/api/attachments")
async def api_attachments():
    upload_dir = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    files = []
    for fname in sorted(os.listdir(upload_dir), reverse=True):
        fpath = os.path.join(upload_dir, fname)
        if os.path.isfile(fpath):
            ext = os.path.splitext(fname)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                stat = os.stat(fpath)
                files.append({
                    "filename": fname,
                    "url": f"/static/uploads/{fname}",
                    "size": stat.st_size,
                    "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                })
    return JSONResponse(files)


@app.delete("/api/upload/{filename}")
async def api_delete_upload(filename: str):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"File type '{ext}' not allowed"}, status_code=400)
    upload_dir = os.path.join(os.path.dirname(__file__), "static", "uploads")
    fpath = os.path.join(upload_dir, filename)
    if not os.path.isfile(fpath):
        return JSONResponse({"error": "File not found"}, status_code=404)
    os.remove(fpath)
    return JSONResponse({"ok": True})


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Export ─────────────────────────────────────────────────

@app.get("/api/export-html/{name:path}")
async def export_html(name: str):
    note = read_note(name)
    if not note:
        return JSONResponse({"error": "Note not found"}, status_code=404)

    content = strip_frontmatter(note["content"])
    html_content = md_to_html(content)
    tags = parse_tags(note["content"])

    html = render_export_html(name, html_content, tags)
    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{name}.html"'},
    )


@app.get("/api/export-pdf/{name:path}")
async def export_pdf(name: str):
    note = read_note(name)
    if not note:
        return JSONResponse({"error": "Note not found"}, status_code=404)

    if not HAS_WEASYPRINT:
        return JSONResponse({"error": "PDF generation unavailable - weasyprint not installed"}, status_code=500)

    content = strip_frontmatter(note["content"])
    html_content = md_to_html(content)
    tags = parse_tags(note["content"])

    html = render_export_html(name, html_content, tags)
    try:
        pdf_bytes = WeasyPrintHTML(string=html).write_pdf()
    except Exception as e:
        return JSONResponse({"error": f"PDF generation failed: {e}"}, status_code=500)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'},
    )

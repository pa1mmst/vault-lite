import os
import json
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from database import init_db, index_note, remove_note, search_notes, get_all_tags, get_all_links, get_all_note_names, get_backlinks
from vault import (
    read_note, write_note, delete_note, list_notes,
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
    for note in list_notes():
        tags = parse_tags(note["content"])
        links = parse_wikilinks(note["content"])
        index_note(note["name"], note["content"], note["created_at"], note["updated_at"], tags, links)
    yield

app = FastAPI(title="vault-lite", lifespan=lifespan)

# ── Static files ──────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ── Markdown → HTML (minimal, no deps) ───────────────────────
def md_to_html(text):
    """Minimal Markdown → HTML converter."""
    lines = text.split("\n")
    html_lines = []
    in_code = False
    in_list = False

    for line in lines:
        # Code block
        stripped = line.strip()
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

        # Close list if needed
        if not stripped.startswith("- ") and not stripped.startswith("* ") and in_list:
            html_lines.append("</ul>")
            in_list = False

        # Headings
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{inline(stripped[2:])}</h1>")
        # List item
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{inline(line.strip()[2:])}</li>")
        # Empty line
        elif stripped == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{inline(line)}</p>")

    if in_list:
        html_lines.append("</ul>")
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
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; min-height: 100vh; }
a { color: #7c83fd; text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
.header { display: flex; align-items: center; justify-content: space-between; padding: 16px 0; border-bottom: 1px solid #333; margin-bottom: 24px; }
.header h1 { font-size: 1.4rem; }
.header h1 a { color: #e0e0e0; }
.nav a { margin-left: 16px; color: #7c83fd; }
.search-bar { display: flex; gap: 12px; margin-bottom: 24px; }
.search-bar input { flex: 1; padding: 10px 16px; border: 1px solid #333; border-radius: 8px; background: #16213e; color: #e0e0e0; font-size: 1rem; }
.search-bar select { padding: 10px; border: 1px solid #333; border-radius: 8px; background: #16213e; color: #e0e0e0; }
.btn { padding: 10px 20px; border: none; border-radius: 8px; background: #7c83fd; color: #fff; cursor: pointer; font-size: 0.9rem; }
.btn:hover { background: #6a73e0; }
.btn-danger { background: #e74c3c; }
.btn-danger:hover { background: #c0392b; }
.note-list { display: grid; gap: 12px; }
.note-card { padding: 16px; background: #16213e; border-radius: 8px; border: 1px solid #333; }
.note-card h3 { margin-bottom: 8px; }
.note-card h3 a { color: #e0e0e0; }
.note-card .meta { font-size: 0.8rem; color: #888; }
.note-card .tags { margin-top: 8px; }
.tag { display: inline-block; padding: 2px 8px; background: #2a2a4a; border-radius: 4px; font-size: 0.8rem; color: #7c83fd; margin-right: 4px; }
.tag:hover { background: #3a3a5a; }
.editor-layout { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; height: calc(100vh - 160px); }
.editor-pane textarea { width: 100%; height: 100%; padding: 16px; border: 1px solid #333; border-radius: 8px; background: #16213e; color: #e0e0e0; font-family: 'Fira Code', monospace; font-size: 0.95rem; resize: none; line-height: 1.6; }
.preview-pane { padding: 16px; background: #16213e; border-radius: 8px; border: 1px solid #333; overflow-y: auto; }
.preview-pane h1, .preview-pane h2, .preview-pane h3 { margin: 16px 0 8px; }
.preview-pane p { margin: 8px 0; line-height: 1.6; }
.preview-pane ul { margin: 8px 0; padding-left: 24px; }
.preview-pane code { background: #2a2a4a; padding: 2px 6px; border-radius: 4px; font-size: 0.9rem; }
.preview-pane pre { background: #2a2a4a; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 12px 0; }
.preview-pane img { max-width: 100%; height: auto; border-radius: 4px; margin: 8px 0; }
.preview-pane .wikilink { color: #7c83fd; font-weight: 500; }
.editor-actions { display: flex; gap: 12px; margin-top: 12px; }
.note-view { max-width: 800px; }
.note-view .content { margin-top: 24px; }
.note-view .content h1, .note-view .content h2, .note-view .content h3 { margin: 20px 0 10px; }
.note-view .content p { margin: 10px 0; line-height: 1.7; }
.note-view .content ul { margin: 10px 0; padding-left: 24px; }
.note-view .content code { background: #2a2a4a; padding: 2px 6px; border-radius: 4px; }
.note-view .content pre { background: #2a2a4a; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 12px 0; }
.note-view .content .wikilink { color: #7c83fd; font-weight: 500; }
.back-link { display: inline-block; margin-bottom: 16px; color: #888; }
.graph-container { width: 100%; height: calc(100vh - 160px); background: #16213e; border-radius: 8px; border: 1px solid #333; }
.empty { text-align: center; padding: 60px; color: #666; }
.backlinks-panel { margin-top: 32px; padding: 16px; background: #16213e; border-radius: 8px; border: 1px solid #333; }
.backlinks-panel h3 { font-size: 0.95rem; color: #888; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.backlinks-panel .backlink-item { display: block; padding: 8px 12px; margin-bottom: 4px; border-radius: 4px; color: #7c83fd; font-size: 0.9rem; }
.backlinks-panel .backlink-item:hover { background: #2a2a4a; text-decoration: none; }
.backlinks-panel .backlink-empty { color: #666; font-size: 0.85rem; font-style: italic; }
.dropdown { position: relative; display: inline-block; }
.dropdown-menu { display: none; position: absolute; top: 100%; left: 0; background: #16213e; border: 1px solid #333; border-radius: 8px; margin-top: 4px; min-width: 160px; z-index: 100; overflow: hidden; }
.dropdown-menu.show { display: block; }
.dropdown-item { display: block; padding: 10px 16px; color: #e0e0e0; text-decoration: none; font-size: 0.9rem; }
.dropdown-item:hover { background: #2a2a4a; text-decoration: none; }
"""


def render_page(title, body):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — vault-lite</title>
    <style>{BASE_STYLE}</style>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="container">
        {body}
    </div>
</body>
</html>"""


def header(active="notes"):
    items = {
        "notes": '<a href="/">📄 Notes</a>',
        "graph": '<a href="/graph">🕸 Graph</a>',
    }
    nav = "".join(v for k, v in items.items() if k != active)
    return f'<div class="header"><h1><a href="/">🗄 vault-lite</a></h1><div class="nav">{nav}</div></div>'


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
async def home(request: Request, q: str = "", tag: str = ""):
    all_tags = get_all_tags()
    tag_filter = f'&tag={tag}' if tag else ""

    if q or tag:
        notes = search_notes(q, tag=tag if tag else None)
    else:
        notes = []
        for n in list_notes():
            notes.append({"name": n["name"], "updated_at": n["updated_at"]})

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
        note_cards += f"""
        <div class="note-card">
            <h3><a href="/note/{n['name']}">{n['name']}</a></h3>
            <div class="meta">{updated}</div>
            <div class="tags">{tags_html}</div>
        </div>"""

    if not note_cards:
        note_cards = '<div class="empty"><p>No notes yet. Create your first one!</p></div>'

    body = f"""
    {header("notes")}
    <div class="search-bar">
        <input type="text" name="q" placeholder="Search notes..." value="{q}" id="searchInput"
               onkeydown="if(event.key==='Enter')window.location='/?q='+encodeURIComponent(this.value)+'{tag_filter}'">
        <select onchange="window.location='/?tag='+encodeURIComponent(this.value)+'&q={q}'">
            {tag_options}
        </select>
        <a href="/edit/new" class="btn">+ New Note</a>
    </div>
    <div class="note-list">{note_cards}</div>
    """
    return render_page("Notes", body)


@app.get("/note/{name}", response_class=HTMLResponse)
async def view_note(name: str):
    note = read_note(name)
    if not note:
        return render_page("Not found", f'{header("notes")}<div class="empty"><p>Note not found.</p><a href="/">← Back</a></div>')

    content = strip_frontmatter(note["content"])
    html_content = md_to_html(content)
    tags = parse_tags(note["content"])
    tags_html = "".join(f'<a href="/?tag={t}" class="tag">#{t}</a>' for t in tags)

    backlinks = get_backlinks(name)
    if backlinks:
        backlinks_items = "".join(
            f'<a href="/note/{bl["source_note"]}" class="backlink-item">→ {bl["source_note"]}</a>'
            for bl in backlinks
        )
        backlinks_html = f'<div class="backlinks-panel"><h3>Backlinks</h3>{backlinks_items}</div>'
    else:
        backlinks_html = '<div class="backlinks-panel"><h3>Backlinks</h3><div class="backlink-empty">No backlinks</div></div>'

    body = f"""
    {header("notes")}
    <div class="note-view">
        <a href="/" class="back-link">← Back to notes</a>
        <h2>{name}</h2>
        <div class="tags">{tags_html}</div>
        <div class="content">{html_content}</div>
        <div style="margin-top:24px; display:flex; gap:12px; align-items:center;">
            <a href="/edit/{name}" class="btn">✏️ Edit</a>
            <div class="dropdown">
                <button class="btn" style="background:#555;" onclick="toggleExport(event)">📥 Export ▾</button>
                <div class="dropdown-menu" id="exportMenu">
                    <a href="/api/export-pdf/{name}" class="dropdown-item" target="_blank">📕 Export PDF</a>
                    <a href="/api/export-html/{name}" class="dropdown-item" target="_blank">🌐 Export HTML</a>
                </div>
            </div>
            <button class="btn btn-danger" onclick="if(confirm('Delete?'))fetch('/api/note/{name}',{{method:'DELETE'}}).then(()=>window.location='/')">🗑 Delete</button>
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
        {backlinks_html}
    </div>
    """
    return render_page(name, body)


@app.get("/edit/{name}", response_class=HTMLResponse)
async def edit_note(name: str):
    note = read_note(name)
    content = note["content"] if note else f"# {name}\n\nStart writing...\n"
    is_new = " (new)" if not note else ""

    body = f"""
    {header("notes")}
    <h2>Edit: {name}{is_new}</h2>
    <div class="editor-layout">
        <div class="editor-pane">
            <div class="toolbar" id="toolbar">
                <button type="button" data-cmd="bold" title="Bold (Ctrl+B)"><strong>B</strong></button>
                <button type="button" data-cmd="italic" title="Italic (Ctrl+I)"><em>I</em></button>
                <span class="separator"></span>
                <button type="button" data-cmd="h1" title="Heading 1">H1</button>
                <button type="button" data-cmd="h2" title="Heading 2">H2</button>
                <button type="button" data-cmd="h3" title="Heading 3">H3</button>
                <span class="separator"></span>
                <button type="button" data-cmd="link" title="Link">🔗</button>
                <button type="button" data-cmd="image" title="Image">🖼</button>
                <button type="button" data-cmd="code" title="Code">&lt;/&gt;</button>
                <button type="button" data-cmd="list" title="List">•</button>
                <button type="button" data-cmd="quote" title="Quote">❝</button>
            </div>
            <textarea id="editor">{content}</textarea>
        </div>
        <div class="preview-pane" id="preview"></div>
    </div>
    <div class="editor-actions">
        <button class="btn" onclick="saveNote()">💾 Save</button>
        <a href="/note/{name}" class="btn" style="background:#555;">Cancel</a>
    </div>
    <script>
    const editor = document.getElementById('editor');
    const preview = document.getElementById('preview');

    function getLineStart(text, pos) {{
        return text.lastIndexOf('\\n', pos - 1) + 1;
    }}

    function insertMarkdown(cmd) {{
        const ta = editor;
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        const text = ta.value;
        const sel = text.substring(start, end);
        const lineStart = getLineStart(text, start);
        const lineEnd = text.indexOf('\\n', start);
        const line = text.substring(lineStart, lineEnd === -1 ? text.length : lineEnd);
        const lineSelStart = start - lineStart;
        const lineSelEnd = end - lineStart;

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
                insert(ta, `\\`${{wrap}}\\``, 1);
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

    document.getElementById('toolbar').addEventListener('click', function(e) {{
        const btn = e.target.closest('button');
        if (!btn) return;
        e.preventDefault();
        insertMarkdown(btn.dataset.cmd);
    }});

    // Keyboard shortcuts
    editor.addEventListener('keydown', function(e) {{
        const mod = e.ctrlKey || e.metaKey;
        if (!mod) return;
        const map = {{b:'bold', i:'italic'}};
        const cmd = map[e.key];
        if (cmd) {{
            e.preventDefault();
            insertMarkdown(cmd);
        }}
    }});

    // ── Image upload ─────────────────────────────────────
    async function uploadImage(file) {{
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/api/upload', {{ method: 'POST', body: formData }});
        const data = await resp.json();
        if (data.url) {{
            const ta = editor;
            const pos = ta.selectionStart;
            const before = ta.value.substring(0, pos);
            const after = ta.value.substring(ta.selectionEnd);
            const imgMd = `![](${{data.url}})\n`;
            ta.value = before + imgMd + after;
            const newPos = pos + imgMd.length;
            ta.setSelectionRange(newPos, newPos);
            ta.dispatchEvent(new Event('input'));
            schedulePreview();
        }}
    }}

    // Drag and drop
    editor.addEventListener('dragover', function(e) {{
        e.preventDefault();
        editor.style.outline = '2px dashed #7c83fd';
    }});
    editor.addEventListener('dragleave', function(e) {{
        e.preventDefault();
        editor.style.outline = '';
    }});
    editor.addEventListener('drop', async function(e) {{
        e.preventDefault();
        editor.style.outline = '';
        const files = e.dataTransfer.files;
        for (const file of files) {{
            if (file.type.startsWith('image/')) {{
                await uploadImage(file);
            }}
        }}
    }});

    // Paste from clipboard
    editor.addEventListener('paste', async function(e) {{
        const items = e.clipboardData.items;
        for (const item of items) {{
            if (item.type.startsWith('image/')) {{
                e.preventDefault();
                const file = item.getAsFile();
                if (file) await uploadImage(file);
            }}
        }}
    }});

    function updatePreview() {{
        preview.innerHTML = markdownToHtml(editor.value);
    }}

    let debounceTimer;
    function schedulePreview() {{
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(updatePreview, 300);
    }}

    editor.addEventListener('input', schedulePreview);
    updatePreview();

    function markdownToHtml(text) {{
        let lines = text.split('\\n');
        let html = [];
        let inCode = false;
        let inList = false;

        for (let line of lines) {{
            let s = line.trim();

            if (s.startsWith('```')) {{
                if (inCode) {{ html.push('</code></pre>'); inCode = false; }}
                else {{ html.push('<pre><code>'); inCode = true; }}
                continue;
            }}
            if (inCode) {{
                html.push(line.replace(/&/g,'&amp;').replace(/</g,'&lt;'));
                continue;
            }}

            // Blockquote
            if (s.startsWith('> ')) {{
                html.push('<blockquote>'+inline(s.slice(2))+'</blockquote>');
                continue;
            }}

            if (!s.startsWith('- ') && !s.startsWith('* ') && inList) {{
                html.push('</ul>');
                inList = false;
            }}

            if (s.startsWith('### ')) {{ html.push('<h3>'+inline(s.slice(4))+'</h3>'); }}
            else if (s.startsWith('## ')) {{ html.push('<h2>'+inline(s.slice(3))+'</h2>'); }}
            else if (s.startsWith('# ')) {{ html.push('<h1>'+inline(s.slice(2))+'</h1>'); }}
            else if (s.startsWith('- ') || s.startsWith('* ')) {{
                if (!inList) {{ html.push('<ul>'); inList = true; }}
                html.push('<li>'+inline(s.slice(2))+'</li>');
            }}
            else if (s === '') {{ html.push('<br>'); }}
            else {{ html.push('<p>'+inline(line)+'</p>'); }}
        }}
        if (inList) html.push('</ul>');
        if (inCode) html.push('</code></pre>');
        return html.join('\\n');
    }}

    function inline(text) {{
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
        text = text.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
        text = text.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
        text = text.replace(/!\\[([^\\]]*)\\]\\(([^)]+)\\)/g, '<img src="$2" alt="$1">');
        text = text.replace(/\\[\\[([^\\]]+)\\]\\]/g, '<a href="/note/$1" class="wikilink">$1</a>');
        text = text.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank">$1</a>');
        text = text.replace(/(?<!\\w)#([a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_\\-/]*)/g, '<a href="/?tag=$1" class="tag">#$1</a>');
        return text;
    }}

    function saveNote() {{
        const content = editor.value;
        const name = '{name}' === 'new' ? prompt('Note name:') : '{name}';
        if (!name) return;
        fetch('/api/note', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{name, content}})
        }}).then(r => r.json()).then(d => {{
            window.location = '/note/' + d.name;
        }});
    }}
    </script>
    """
    return render_page(f"Edit: {name}", body)


@app.get("/graph", response_class=HTMLResponse)
async def graph_page():
    body = f"""
    {header("graph")}
    <div class="graph-container" id="graph"></div>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <script>
    fetch('/api/graph')
        .then(r => r.json())
        .then(data => {{
            const width = document.getElementById('graph').clientWidth;
            const height = document.getElementById('graph').clientHeight;
            const svg = d3.select('#graph').append('svg')
                .attr('width', width).attr('height', height);
            const simulation = d3.forceSimulation(data.nodes)
                .force('link', d3.forceLink(data.links).id(d => d.id).distance(100))
                .force('charge', d3.forceManyBody().strength(-200))
                .force('center', d3.forceCenter(width / 2, height / 2));
            const link = svg.append('g').selectAll('line')
                .data(data.links).enter().append('line')
                .attr('stroke', '#444').attr('stroke-width', 1.5);
            const node = svg.append('g').selectAll('g')
                .data(data.nodes).enter().append('g')
                .call(d3.drag()
                    .on('start', (e, d) => {{ if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
                    .on('drag', (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
                    .on('end', (e, d) => {{ if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }}));
            node.append('circle').attr('r', 8).attr('fill', '#7c83fd');
            node.append('text').text(d => d.id)
                .attr('x', 12).attr('y', 4)
                .attr('fill', '#e0e0e0').attr('font-size', '12px');
            node.on('click', (e, d) => {{ window.location = '/note/' + d.id; }});
            simulation.on('tick', () => {{
                link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
                node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
            }});
        }});
    </script>
    """
    return render_page("Graph", body)


# ── API ───────────────────────────────────────────────────────

@app.get("/api/search")
def api_search(q: str = "", tag: str = ""):
    return JSONResponse(search_notes(q, tag=tag or None))


@app.get("/api/graph")
def api_graph():
    links = get_all_links()
    names = get_all_note_names()
    nodes = [{"id": n} for n in names]
    link_data = []
    for l in links:
        link_data.append({"source": l["source_note"], "target": l["target_note"]})
    return JSONResponse({"nodes": nodes, "links": link_data})


@app.get("/api/backlinks/{name}")
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
    tags = parse_tags(content)
    links = parse_wikilinks(content)
    index_note(name, content, note["created_at"], note["updated_at"], tags, links)
    return JSONResponse({"name": name, "updated_at": note["updated_at"]})


@app.delete("/api/note/{name}")
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


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Export ─────────────────────────────────────────────────

@app.get("/api/export-html/{name}")
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


@app.get("/api/export-pdf/{name}")
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

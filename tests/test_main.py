import os
import sys
import pytest

os.environ["VAULT_DIR"] = "/tmp/vault-test-api"

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from main import app
from database import init_db as _init_db, migrate_db as _migrate_db


@pytest.fixture(autouse=True)
def _setup_db():
    _init_db()
    _migrate_db()
    yield


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestNoteAPI:
    def test_create_note(self, client):
        r = client.post("/api/note", json={"name": "test-api", "content": "# Test\nHello"})
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "test-api"
        assert data["folder"] == ""

    def test_get_note_page(self, client):
        client.post("/api/note", json={"name": "test-view", "content": "**Bold** and #tag"})
        r = client.get("/note/test-view")
        assert r.status_code == 200

    def test_search(self, client):
        client.post("/api/note", json={"name": "search-me", "content": "unique phrase xyz123"})
        r = client.get("/api/search?q=xyz123")
        assert r.status_code == 200
        results = r.json()
        assert any(n["name"] == "search-me" for n in results)

    def test_delete_note(self, client):
        client.post("/api/note", json={"name": "to-del", "content": "del"})
        r = client.delete("/api/note/to-del")
        assert r.status_code == 200

    def test_graph_api(self, client):
        r = client.get("/api/graph")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert "links" in data

    def test_404_page(self, client):
        r = client.get("/note/nonexistent-note-abc")
        assert r.status_code == 200


class TestFolders:
    def test_create_note_in_folder(self, client):
        r = client.post("/api/note", json={"name": "projects/idea", "content": "# Idea"})
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "projects/idea"
        assert data["folder"] == "projects"

    def test_view_note_in_folder(self, client):
        client.post("/api/note", json={"name": "projects/note-a", "content": "In folder"})
        r = client.get("/note/projects/note-a")
        assert r.status_code == 200
        assert "folder-breadcrumb" in r.text
        assert "projects" in r.text

    def test_folder_api(self, client):
        client.post("/api/note", json={"name": "folder-test/n1", "content": "n1"})
        client.post("/api/note", json={"name": "folder-test/n2", "content": "n2"})
        r = client.get("/api/folders")
        assert r.status_code == 200
        data = r.json()
        assert any(f["folder"] == "folder-test" for f in data)

    def test_home_page_filters_by_folder(self, client):
        client.post("/api/note", json={"name": "filter-me/n1", "content": "n1"})
        client.post("/api/note", json={"name": "other/n2", "content": "n2"})
        r = client.get("/?folder=filter-me")
        assert r.status_code == 200
        assert "filter-me" in r.text
        # The sidebar should show the folder tree
        assert "sidebar-folder-link" in r.text

    def test_nested_folder(self, client):
        client.post("/api/note", json={"name": "a/b/c/deep-note", "content": "deep"})
        r = client.get("/note/a/b/c/deep-note")
        assert r.status_code == 200
        assert "folder-breadcrumb" in r.text
        assert "a" in r.text
        assert "c" in r.text

    def test_delete_note_in_folder(self, client):
        client.post("/api/note", json={"name": "del-folder/to-delete", "content": "bye"})
        r = client.delete("/api/note/del-folder/to-delete")
        assert r.status_code == 200

    def test_backlinks_note_in_folder(self, client):
        client.post("/api/note", json={"name": "fl/source", "content": "Links to [[fl/target]]"})
        client.post("/api/note", json={"name": "fl/target", "content": "Target"})
        r = client.get("/api/backlinks/fl/target")
        assert r.status_code == 200
        data = r.json()
        assert any(d["source_note"] == "fl/source" for d in data)

    def test_editor_for_folder_note(self, client):
        client.post("/api/note", json={"name": "ed/folder-note", "content": "# Edit me"})
        r = client.get("/edit/ed/folder-note")
        assert r.status_code == 200
        assert "Edit" in r.text

    def test_export_note_in_folder(self, client):
        client.post("/api/note", json={"name": "export-folder/test", "content": "# Export"})
        r = client.get("/api/export-html/export-folder/test")
        assert r.status_code == 200
        assert "Export" in r.text

    def test_search_with_folder_filter(self, client):
        client.post("/api/note", json={"name": "search-folder/note-a", "content": "secret phrase"})
        client.post("/api/note", json={"name": "other-folder/note-b", "content": "secret phrase"})
        r = client.get("/api/search?q=secret&folder=search-folder")
        assert r.status_code == 200
        data = r.json()
        names = [n["name"] for n in data]
        assert "search-folder/note-a" in names
        assert "other-folder/note-b" not in names

    def test_folder_new_from_editor(self, client):
        r = client.get("/edit/new")
        assert r.status_code == 200
        assert "editor-layout" in r.text

    def test_folder_only_shows_existing_notes(self, client):
        """Stale DB entries (notes deleted from disk but still in index)
        should not appear in folder filter results."""
        client.post("/api/note", json={"name": "existing-folder/note-a", "content": "exists"})
        # Manually delete the file to create a stale DB entry
        vault = os.environ.get("VAULT_DIR", "/tmp/vault-test-api")
        fpath = os.path.join(vault, "existing-folder", "note-a.md")
        if os.path.exists(fpath):
            os.remove(fpath)
        r = client.get("/?folder=existing-folder")
        assert r.status_code == 200
        assert "note-a" not in r.text, "Stale note should not appear in folder filter"

    def test_folder_tree_excludes_empty_folders(self, client):
        """Folders whose notes have been removed from disk should not appear
        in the sidebar folder tree."""
        client.post("/api/note", json={"name": "ghost-folder/n1", "content": "ghost"})
        vault = os.environ.get("VAULT_DIR", "/tmp/vault-test-api")
        fpath = os.path.join(vault, "ghost-folder", "n1.md")
        if os.path.exists(fpath):
            os.remove(fpath)
        r = client.get("/")
        assert r.status_code == 200
        assert "ghost-folder" not in r.text, "Empty folder should not appear in sidebar tree"


class TestBacklinks:
    def test_backlinks_api_empty(self, client):
        r = client.get("/api/backlinks/nonexistent")
        assert r.status_code == 200
        assert r.json() == []

    def test_backlinks_api(self, client):
        client.post("/api/note", json={"name": "note-a", "content": "Links to [[note-b]] and [[note-c]]"})
        client.post("/api/note", json={"name": "note-d", "content": "Also links to [[note-b]]"})
        r = client.get("/api/backlinks/note-b")
        assert r.status_code == 200
        data = r.json()
        names = [d["source_note"] for d in data]
        assert "note-a" in names
        assert "note-d" in names
        assert len(names) == 2

    def test_backlinks_note_page_shows_panel(self, client):
        client.post("/api/note", json={"name": "linker", "content": "See [[target-note]]"})
        client.post("/api/note", json={"name": "target-note", "content": "Target content"})
        r = client.get("/note/target-note")
        assert r.status_code == 200
        assert "sidebar-backlink-item" in r.text
        assert "linker" in r.text

    def test_backlinks_note_page_no_backlinks(self, client):
        client.post("/api/note", json={"name": "orphan", "content": "No links to me"})
        r = client.get("/note/orphan")
        assert r.status_code == 200
        assert "sidebar-backlinks" in r.text
        assert "No backlinks" in r.text


class TestEditor:
    def test_editor_page_renders(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "editor-layout" in r.text
        assert "toolbar" in r.text
        assert "preview-pane" in r.text
        assert 'id="preview"' in r.text
        assert 'id="wysiwygEditor"' in r.text
        assert 'contenteditable=' in r.text or 'contenteditable' in r.text

    def test_editor_has_toolbar_buttons(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert 'data-cmd="bold"' in r.text
        assert 'data-cmd="italic"' in r.text
        assert 'data-cmd="h1"' in r.text
        assert 'data-cmd="h2"' in r.text
        assert 'data-cmd="h3"' in r.text
        assert 'data-cmd="link"' in r.text
        assert 'data-cmd="code"' in r.text
        assert 'data-cmd="list"' in r.text
        assert 'data-cmd="quote"' in r.text
        assert 'data-cmd="undo"' in r.text
        assert 'data-cmd="redo"' in r.text
        assert 'data-cmd="sourceToggle"' in r.text

    def test_editor_has_debounce(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "debounceTimer" in r.text
        assert "200" in r.text and "setTimeout(updatePreview" in r.text

    def test_editor_has_save_function(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "saveNote" in r.text
        assert "/api/note" in r.text

    def test_editor_has_keyboard_shortcuts(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "ctrlKey" in r.text or "metaKey" in r.text

    def test_editor_new_note_has_default_content(self, client):
        r = client.get("/edit/new")
        assert r.status_code == 200
        assert "Start writing" in r.text

    def test_editor_existing_note_has_content(self, client):
        client.post("/api/note", json={"name": "test-editor-content", "content": "# Existing\n\nNote body"})
        r = client.get("/edit/test-editor-content")
        assert r.status_code == 200
        assert "Existing" in r.text
        assert "Note body" in r.text

    def test_markdown_to_html_via_api(self, client):
        r = client.post("/api/note", json={"name": "md-test", "content": "# Heading\n\n**Bold** and *italic*\n\n- Item 1\n- Item 2\n\n> A quote\n\n`code` here\n\n[[wiki-link]]\n\n#tag"})
        assert r.status_code == 200
        r2 = client.get("/note/md-test")
        assert r2.status_code == 200
        assert "Heading" in r2.text
        assert "wiki-link" in r2.text

    def test_editor_has_cancel_link(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "Cancel" in r.text
        assert "/note/test-editor" in r.text or "href=" in r.text

    def test_editor_links_static_css(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "style.css" in r.text or "/static/" in r.text

    def test_static_css_served(self, client):
        r = client.get("/static/style.css")
        assert r.status_code == 200
        assert "toolbar" in r.text


class TestImageUpload:
    def test_upload_png(self, client):
        r = client.post("/api/upload", files={"file": ("test.png", b"fake-png-content", "image/png")})
        assert r.status_code == 200
        data = r.json()
        assert "url" in data
        assert data["url"].startswith("/static/uploads/")
        assert data["url"].endswith(".png")

    def test_upload_jpg(self, client):
        r = client.post("/api/upload", files={"file": ("photo.jpg", b"fake-jpg-content", "image/jpeg")})
        assert r.status_code == 200
        data = r.json()
        assert data["url"].endswith(".jpg")

    def test_upload_gif(self, client):
        r = client.post("/api/upload", files={"file": ("anim.gif", b"fake-gif-content", "image/gif")})
        assert r.status_code == 200
        data = r.json()
        assert data["url"].endswith(".gif")

    def test_upload_webp(self, client):
        r = client.post("/api/upload", files={"file": ("img.webp", b"fake-webp-content", "image/webp")})
        assert r.status_code == 200
        data = r.json()
        assert data["url"].endswith(".webp")

    def test_upload_svg(self, client):
        r = client.post("/api/upload", files={"file": ("vector.svg", b"<svg></svg>", "image/svg+xml")})
        assert r.status_code == 200
        data = r.json()
        assert data["url"].endswith(".svg")

    def test_upload_rejects_unsupported_type(self, client):
        r = client.post("/api/upload", files={"file": ("document.pdf", b"pdf-content", "application/pdf")})
        assert r.status_code == 400
        assert "not allowed" in r.text

    def test_upload_rejects_too_large(self, client):
        large_data = b"x" * (5 * 1024 * 1024 + 1)
        r = client.post("/api/upload", files={"file": ("large.png", large_data, "image/png")})
        assert r.status_code == 400
        assert "too large" in r.text.lower() or "5 MB" in r.text

    def test_upload_file_accessible_via_static(self, client):
        r = client.post("/api/upload", files={"file": ("serve-test.png", b"content", "image/png")})
        assert r.status_code == 200
        url = r.json()["url"]
        r2 = client.get(url)
        assert r2.status_code == 200
        assert r2.content == b"content"

    def test_upload_creates_uploads_dir(self, client):
        import shutil
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
        shutil.rmtree(upload_dir, ignore_errors=True)
        r = client.post("/api/upload", files={"file": ("fresh.png", b"data", "image/png")})
        assert r.status_code == 200
        assert os.path.isdir(upload_dir)

    def test_editor_has_image_button(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert 'data-cmd="image"' in r.text

    def test_editor_has_drag_drop_handlers(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "dragover" in r.text
        assert "drop" in r.text
        assert "uploadImage" in r.text

    def test_editor_has_clipboard_paste_handler(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "paste" in r.text
        assert "clipboardData" in r.text or "getAsFile" in r.text


class TestAttachments:
    def test_attachments_list_empty(self, client):
        import shutil
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        for f in os.listdir(upload_dir):
            os.remove(os.path.join(upload_dir, f))
        r = client.get("/api/attachments")
        assert r.status_code == 200
        assert r.json() == []

    def test_attachments_list_after_upload(self, client):
        r = client.post("/api/upload", files={"file": ("test.png", b"fake-png-content", "image/png")})
        assert r.status_code == 200
        filename = r.json()["url"].split("/")[-1]
        r2 = client.get("/api/attachments")
        assert r2.status_code == 200
        data = r2.json()
        assert any(f["filename"] == filename for f in data)

    def test_attachments_delete(self, client):
        r = client.post("/api/upload", files={"file": ("del-test.png", b"delete-me", "image/png")})
        assert r.status_code == 200
        filename = r.json()["url"].split("/")[-1]
        r2 = client.delete(f"/api/upload/{filename}")
        assert r2.status_code == 200
        assert r2.json()["ok"] is True

    def test_attachments_delete_not_found(self, client):
        r = client.delete("/api/upload/nonexistent.png")
        assert r.status_code == 404
        assert "not found" in r.text.lower()

    def test_attachments_delete_unsupported_type(self, client):
        r = client.delete("/api/upload/file.txt")
        assert r.status_code == 400
        assert "not allowed" in r.text.lower()

    def test_editor_has_attachments_panel(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "attachments-panel" in r.text
        assert "dropZone" in r.text
        assert "attachmentsGrid" in r.text
        assert "loadAttachments" in r.text

    def test_editor_has_drop_zone(self, client):
        r = client.get("/edit/test-editor")
        assert r.status_code == 200
        assert "drop-zone" in r.text
        assert "fileInput" in r.text
        assert "uploadFiles" in r.text


class TestExport:
    def test_export_html(self, client):
        client.post("/api/note", json={"name": "export-test", "content": "# Hello\n\nThis is **bold** and *italic*."})
        r = client.get("/api/export-html/export-test")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "export-test.html" in r.headers.get("content-disposition", "")
        assert "Hello" in r.text
        assert "<strong>bold</strong>" in r.text

    def test_export_html_404(self, client):
        r = client.get("/api/export-html/nonexistent")
        assert r.status_code == 404

    def test_export_html_has_tags(self, client):
        client.post("/api/note", json={"name": "tagged-note", "content": "# Tagged\n\n#tag1 and #tag2"})
        r = client.get("/api/export-html/tagged-note")
        assert r.status_code == 200
        assert "#tag1" in r.text
        assert "#tag2" in r.text

    def test_export_pdf(self, client):
        pytest.importorskip("weasyprint")
        client.post("/api/note", json={"name": "pdf-test", "content": "# PDF Title\n\nExport content."})
        r = client.get("/api/export-pdf/pdf-test")
        assert r.status_code == 200
        assert "application/pdf" in r.headers["content-type"]
        assert "pdf-test.pdf" in r.headers.get("content-disposition", "")

    def test_export_pdf_404(self, client):
        r = client.get("/api/export-pdf/nonexistent")
        assert r.status_code == 404

    def test_export_dropdown_on_note_page(self, client):
        client.post("/api/note", json={"name": "export-page", "content": "# Test"})
        r = client.get("/note/export-page")
        assert r.status_code == 200
        assert "Export" in r.text
        assert "export-pdf" in r.text
        assert "export-html" in r.text
        assert "toggleExport" in r.text

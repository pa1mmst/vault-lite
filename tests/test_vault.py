import os
import sys
import pytest

# Ensure we use test DB
os.environ["VAULT_DIR"] = "/tmp/vault-test"

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vault import parse_tags, parse_wikilinks, strip_frontmatter, write_note, read_note, delete_note, note_exists


class TestParseTags:
    def test_single_tag(self):
        assert parse_tags("Hello #world") == ["world"]

    def test_multiple_tags(self):
        tags = parse_tags("#python #web #api")
        assert set(tags) == {"python", "web", "api"}

    def test_cyrillic_tags(self):
        tags = parse_tags("привет #мир #python")
        assert set(tags) == {"мир", "python"}

    def test_no_tags(self):
        assert parse_tags("Hello world") == []

    def test_tag_with_numbers(self):
        tags = parse_tags("#web2 #ai3")
        assert set(tags) == {"web2", "ai3"}

    def test_tags_deduplicated(self):
        tags = parse_tags("#web #web #web")
        assert tags == ["web"]


class TestParseWikilinks:
    def test_single_link(self):
        assert parse_tags("See [[Other Note]]") is not None  # tags won't catch this
        assert parse_wikilinks("See [[Other Note]]") == ["Other Note"]

    def test_multiple_links(self):
        links = parse_wikilinks("See [[A]] and [[B]]")
        assert set(links) == {"A", "B"}

    def test_no_links(self):
        assert parse_wikilinks("No links here") == []


class TestStripFrontmatter:
    def test_with_frontmatter(self):
        text = "---\ntitle: Test\n---\n# Hello"
        assert strip_frontmatter(text) == "# Hello"

    def test_without_frontmatter(self):
        text = "# Hello"
        assert strip_frontmatter(text) == "# Hello"


class TestVaultFiles:
    def setup_method(self):
        os.makedirs("/tmp/vault-test", exist_ok=True)

    def test_write_and_read(self):
        note = write_note("test-note", "# Hello\nContent here")
        assert note["name"] == "test-note"
        assert note["content"] == "# Hello\nContent here"
        read = read_note("test-note")
        assert read is not None
        assert read["content"] == "# Hello\nContent here"

    def test_read_nonexistent(self):
        assert read_note("does-not-exist") is None

    def test_delete(self):
        write_note("to-delete", "content")
        assert note_exists("to-delete")
        delete_note("to-delete")
        assert not note_exists("to-delete")

    def test_note_exists_false(self):
        assert not note_exists("ghost-note")

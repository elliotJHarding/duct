"""Tests for duct.markdown utilities."""

import re
from pathlib import Path

import pytest

from duct.markdown import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    TICKET_KEY_PATTERN,
    ManagedBlockReseeded,
    atomic_write,
    extract_table,
    generate_frontmatter,
    parse_frontmatter,
    update_managed_block,
    write_if_changed,
)


def _managed(body: str) -> str:
    """Wrap *body* in start/end markers with surrounding newlines."""
    return f"{MANAGED_BLOCK_START}\n{body}\n{MANAGED_BLOCK_END}\n"


class TestTicketKeyPattern:
    def test_matches_standard_key(self):
        assert TICKET_KEY_PATTERN.search("ERSC-1278")

    def test_matches_short_project(self):
        assert TICKET_KEY_PATTERN.search("AB-1")

    def test_no_match_lowercase(self):
        assert not TICKET_KEY_PATTERN.search("ersc-123")

    def test_no_match_missing_digits(self):
        assert not TICKET_KEY_PATTERN.search("ERSC-")


class TestGenerateFrontmatter:
    def test_custom_synced_at(self):
        result = generate_frontmatter(source="manual", synced_at="2026-03-16T10:30:00Z")
        assert result == "---\nsource: manual\nsyncedAt: 2026-03-16T10:30:00Z\n---\n"

    def test_default_source(self):
        result = generate_frontmatter(synced_at="2026-01-01T00:00:00Z")
        assert "source: sync" in result

    def test_auto_timestamp(self):
        result = generate_frontmatter()
        assert "syncedAt:" in result
        assert result.endswith("---\n")


class TestParseFrontmatter:
    def test_round_trip(self):
        fm = generate_frontmatter(source="sync", synced_at="2026-03-16T10:30:00Z")
        body = "# Title\n\nSome content.\n"
        content = fm + body
        meta, parsed_body = parse_frontmatter(content)
        assert meta["source"] == "sync"
        assert meta["syncedAt"] == "2026-03-16T10:30:00Z"
        assert parsed_body == body

    def test_no_frontmatter(self):
        content = "# Just a heading\n\nNo frontmatter here.\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_partial_frontmatter_not_matched(self):
        content = "---\nincomplete\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content


class TestAtomicWrite:
    def test_creates_file_and_parents(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "file.md"
        atomic_write(target, "hello")
        assert target.read_text() == "hello"
        # tmp file should not be left behind
        assert not target.with_suffix(".tmp").exists()

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "file.md"
        target.write_text("old")
        atomic_write(target, "new")
        assert target.read_text() == "new"


class TestWriteIfChanged:
    def test_writes_new_file(self, tmp_path: Path):
        target = tmp_path / "file.md"
        assert write_if_changed(target, "content") is True
        assert target.read_text() == "content"

    def test_returns_false_when_unchanged(self, tmp_path: Path):
        target = tmp_path / "file.md"
        target.write_text("same", encoding="utf-8")
        assert write_if_changed(target, "same") is False

    def test_returns_true_when_changed(self, tmp_path: Path):
        target = tmp_path / "file.md"
        target.write_text("old", encoding="utf-8")
        assert write_if_changed(target, "new") is True
        assert target.read_text() == "new"


class TestUpdateManagedBlock:
    SEED_TAIL = "\n<!-- user notes below -->\n"

    def test_creates_file_when_missing(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        result = update_managed_block(target, _managed("hello"), seed_tail=self.SEED_TAIL)
        assert result == "created"
        assert target.read_text() == _managed("hello") + self.SEED_TAIL

    def test_updates_managed_region_preserving_tail(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        target.write_text(_managed("v1") + "\n## My notes\n\nWIP.\n", encoding="utf-8")

        result = update_managed_block(target, _managed("v2"))

        assert result == "updated"
        assert target.read_text() == _managed("v2") + "\n## My notes\n\nWIP.\n"

    def test_preserves_prefix_before_start_marker(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        target.write_text("preface line\n" + _managed("v1") + "after\n", encoding="utf-8")

        update_managed_block(target, _managed("v2"))

        assert target.read_text() == "preface line\n" + _managed("v2") + "after\n"

    def test_idempotent_when_managed_unchanged(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        managed = _managed("stable")
        target.write_text(managed + "tail\n", encoding="utf-8")

        result = update_managed_block(target, managed)

        assert result == "updated"
        assert target.read_text() == managed + "tail\n"

    def test_reseeds_when_end_marker_missing(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        original = "# manual override\n\nno markers here\n"
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ManagedBlockReseeded) as exc_info:
            update_managed_block(target, _managed("fresh"), seed_tail=self.SEED_TAIL)

        backup = exc_info.value.backup
        assert exc_info.value.original == target
        assert backup.exists()
        assert backup.read_text() == original
        assert re.fullmatch(r"CLAUDE\.md\.\d{8}T\d{6}Z\.bak", backup.name)
        assert target.read_text() == _managed("fresh") + self.SEED_TAIL

    def test_reseeds_when_only_end_marker_present(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        target.write_text(f"orphan\n{MANAGED_BLOCK_END}\nother\n", encoding="utf-8")

        with pytest.raises(ManagedBlockReseeded):
            update_managed_block(target, _managed("fresh"))


class TestExtractTable:
    def test_parses_simple_table(self):
        table = (
            "| Key   | Summary       |\n"
            "|-------|---------------|\n"
            "| ERSC-1 | Fix login bug |\n"
            "| ERSC-2 | Add feature   |\n"
        )
        rows = extract_table(table)
        assert len(rows) == 2
        assert rows[0] == {"Key": "ERSC-1", "Summary": "Fix login bug"}
        assert rows[1] == {"Key": "ERSC-2", "Summary": "Add feature"}

    def test_empty_body(self):
        assert extract_table("") == []

    def test_table_embedded_in_prose(self):
        body = (
            "Some intro text.\n\n"
            "| Name  | Value |\n"
            "| ----- | ----- |\n"
            "| alpha | 1     |\n"
            "\nMore text.\n"
        )
        rows = extract_table(body)
        assert len(rows) == 1
        assert rows[0]["Name"] == "alpha"

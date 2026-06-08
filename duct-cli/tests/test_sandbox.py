"""Tests for duct.sandbox module."""

import json
from pathlib import Path

from duct.config import SandboxConfig
from duct.sandbox import (
    build_settings,
    load_settings_template,
    merge_settings_template,
    write_settings,
)


class TestBuildSettings:
    def test_defaults(self):
        result = build_settings(SandboxConfig())

        assert result["sandbox"]["enabled"] is True
        assert result["sandbox"]["autoAllowBashIfSandboxed"] is True
        assert "." in result["sandbox"]["filesystem"]["allowWrite"]
        assert "~/.m2" in result["sandbox"]["filesystem"]["allowWrite"]
        assert "~/.ssh" in result["sandbox"]["filesystem"]["denyRead"]
        # No network key when allowedDomains is empty.
        assert "network" not in result["sandbox"]

    def test_custom_deny_read(self):
        cfg = SandboxConfig(deny_read=("~/.ssh", "~/.secrets"))
        result = build_settings(cfg)

        assert result["sandbox"]["filesystem"]["denyRead"] == ["~/.ssh", "~/.secrets"]

    def test_with_domains(self):
        cfg = SandboxConfig(allowed_domains=("api.example.com", "cdn.example.com"))
        result = build_settings(cfg)

        assert result["sandbox"]["network"]["allowedDomains"] == [
            "api.example.com",
            "cdn.example.com",
        ]


class TestWriteSettings:
    def test_creates_file(self, tmp_path: Path):
        path = write_settings(tmp_path, SandboxConfig())

        assert path == tmp_path / ".claude" / "settings.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sandbox"]["enabled"] is True
        assert "." in data["sandbox"]["filesystem"]["allowWrite"]

    def test_merges_existing(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"env": {"FOO": "bar"}, "sandbox": {"enabled": False}}
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        write_settings(tmp_path, SandboxConfig())

        data = json.loads((claude_dir / "settings.json").read_text())
        # Sandbox key replaced with new config.
        assert data["sandbox"]["enabled"] is True
        # Other keys preserved.
        assert data["env"] == {"FOO": "bar"}

    def test_idempotent(self, tmp_path: Path):
        write_settings(tmp_path, SandboxConfig())
        first = (tmp_path / ".claude" / "settings.json").read_text()

        write_settings(tmp_path, SandboxConfig())
        second = (tmp_path / ".claude" / "settings.json").read_text()

        assert first == second


class TestLoadSettingsTemplate:
    def test_returns_none_when_missing(self, tmp_path: Path):
        assert load_settings_template(tmp_path) is None

    def test_returns_parsed_dict(self, tmp_path: Path):
        template = {"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}}
        (tmp_path / "settings.template.json").write_text(json.dumps(template))

        assert load_settings_template(tmp_path) == template

    def test_returns_none_when_malformed(self, tmp_path: Path):
        (tmp_path / "settings.template.json").write_text("{not valid json")

        assert load_settings_template(tmp_path) is None

    def test_returns_none_when_not_object(self, tmp_path: Path):
        (tmp_path / "settings.template.json").write_text("[1, 2, 3]")

        assert load_settings_template(tmp_path) is None


class TestMergeSettingsTemplate:
    def test_creates_file_and_claude_dir(self, tmp_path: Path):
        ticket = tmp_path / "ERSC-1-task"
        ticket.mkdir()

        merge_settings_template(ticket, {"env": {"X": "1"}})

        settings_path = ticket / ".claude" / "settings.json"
        assert settings_path.exists()
        assert json.loads(settings_path.read_text()) == {"env": {"X": "1"}}

    def test_preserves_sandbox_key_in_existing_file(self, tmp_path: Path):
        write_settings(tmp_path, SandboxConfig())
        original = json.loads((tmp_path / ".claude" / "settings.json").read_text())

        merge_settings_template(tmp_path, {"env": {"X": "1"}})

        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["sandbox"] == original["sandbox"]
        assert data["env"] == {"X": "1"}

    def test_replaces_overlapping_top_level_key(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps({"env": {"OLD": "value"}, "other": True})
        )

        merge_settings_template(tmp_path, {"env": {"NEW": "value"}})

        data = json.loads((claude_dir / "settings.json").read_text())
        assert data["env"] == {"NEW": "value"}
        assert data["other"] is True

    def test_ignores_sandbox_key_in_template(self, tmp_path: Path):
        write_settings(tmp_path, SandboxConfig())
        original = json.loads((tmp_path / ".claude" / "settings.json").read_text())

        merge_settings_template(
            tmp_path,
            {"sandbox": {"enabled": False}, "env": {"X": "1"}},
        )

        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["sandbox"] == original["sandbox"]
        assert data["env"] == {"X": "1"}

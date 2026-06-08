"""Tests for duct.notifier."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from duct.notifier import MacNotifier


def test_disabled_notifier_does_not_invoke_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = MagicMock()
    monkeypatch.setattr("duct.notifier.subprocess.run", spy)

    notifier = MacNotifier(enabled=False)

    assert notifier.notify("title", "body") is False
    spy.assert_not_called()


def test_non_darwin_disables_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("duct.notifier.sys.platform", "linux")
    spy = MagicMock()
    monkeypatch.setattr("duct.notifier.subprocess.run", spy)

    notifier = MacNotifier(enabled=True)

    assert notifier.enabled is False
    assert notifier.notify("title", "body") is False
    spy.assert_not_called()


def test_missing_binary_disables_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("duct.notifier.sys.platform", "darwin")
    monkeypatch.setattr("duct.notifier.shutil.which", lambda _: None)
    # No Homebrew fallback dirs either — binary genuinely absent.
    monkeypatch.setattr("duct.notifier._BIN_FALLBACK_DIRS", ())

    notifier = MacNotifier(enabled=True)

    assert notifier.enabled is False


def test_path_fallback_resolves_binary_off_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """When `which` fails (e.g. launchd minimal PATH), the absolute fallback wins."""
    (tmp_path / "terminal-notifier").write_text("#!/bin/sh\n")
    monkeypatch.setattr("duct.notifier.sys.platform", "darwin")
    monkeypatch.setattr("duct.notifier.shutil.which", lambda _: None)
    monkeypatch.setattr("duct.notifier._BIN_FALLBACK_DIRS", (str(tmp_path),))
    monkeypatch.setattr("duct.notifier._default_icon", lambda: None)

    notifier = MacNotifier(enabled=True)

    assert notifier.enabled is True


def _make_notifier(
    monkeypatch: pytest.MonkeyPatch,
    icon: str | None = None,
    orchestrator_icon: str | None = None,
) -> tuple[MacNotifier, dict]:
    monkeypatch.setattr("duct.notifier.sys.platform", "darwin")
    monkeypatch.setattr("duct.notifier.shutil.which", lambda _: "/usr/local/bin/terminal-notifier")
    monkeypatch.setattr("duct.notifier._default_icon", lambda: None)
    monkeypatch.setattr("duct.notifier._conductor_icon", lambda: orchestrator_icon)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr("duct.notifier.subprocess.run", fake_run)
    return MacNotifier(enabled=True, icon=icon), captured


def test_basic_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    assert notifier.notify("Action needed: ABC-123", "2 actions pending") is True

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/local/bin/terminal-notifier"
    assert cmd[cmd.index("-title") + 1] == "Action needed: ABC-123"
    assert cmd[cmd.index("-message") + 1] == "2 actions pending"


def test_subtitle(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body", subtitle="sub")

    assert captured["cmd"][captured["cmd"].index("-subtitle") + 1] == "sub"


def test_group_deduplication(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body", group="pending-action:PS-100")

    assert captured["cmd"][captured["cmd"].index("-group") + 1] == "pending-action:PS-100"


def test_sound(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body", sound="default")

    assert captured["cmd"][captured["cmd"].index("-sound") + 1] == "default"


def test_open_url(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body", open_url="https://example.com/browse/PS-100")

    assert captured["cmd"][captured["cmd"].index("-open") + 1] == "https://example.com/browse/PS-100"


def test_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body", execute="/usr/local/bin/duct session jump abc")

    cmd = captured["cmd"]
    assert cmd[cmd.index("-execute") + 1] == "/usr/local/bin/duct session jump abc"


def test_execute_takes_precedence_over_open_url(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("t", "b", open_url="https://x", execute="run me")

    cmd = captured["cmd"]
    assert "-execute" in cmd
    assert "-open" not in cmd


def test_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body", sender="com.github.wez.wezterm")

    assert captured["cmd"][captured["cmd"].index("-sender") + 1] == "com.github.wez.wezterm"


def test_content_image_argument_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch, icon="/path/to/clawd.png")

    notifier.notify("title", "body", content_image="/cache/avatars/alice.png")

    assert captured["cmd"][captured["cmd"].index("-contentImage") + 1] == "/cache/avatars/alice.png"


def test_content_image_falls_back_to_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch, icon="/path/to/clawd.png")

    notifier.notify("title", "body")

    assert captured["cmd"][captured["cmd"].index("-contentImage") + 1] == "/path/to/clawd.png"


def test_orchestrator_kind_uses_conductor_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(
        monkeypatch, icon="/path/to/clawd.png", orchestrator_icon="/path/to/conductor.png"
    )

    for kind in ("orchestrator", "pending-action", "orchestrator-action"):
        notifier.notify("title", "body", kind=kind)
        assert captured["cmd"][captured["cmd"].index("-contentImage") + 1] == "/path/to/conductor.png"


def test_session_kind_keeps_brand_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(
        monkeypatch, icon="/path/to/clawd.png", orchestrator_icon="/path/to/conductor.png"
    )

    notifier.notify("title", "body", kind="done")

    assert captured["cmd"][captured["cmd"].index("-contentImage") + 1] == "/path/to/clawd.png"


def test_explicit_content_image_wins_over_conductor(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(
        monkeypatch, icon="/path/to/clawd.png", orchestrator_icon="/path/to/conductor.png"
    )

    notifier.notify("title", "body", kind="orchestrator", content_image="/cache/custom.png")

    assert captured["cmd"][captured["cmd"].index("-contentImage") + 1] == "/cache/custom.png"


def test_no_content_image_without_icon_or_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body")

    assert "-contentImage" not in captured["cmd"]


def test_optional_flags_omitted_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, captured = _make_notifier(monkeypatch)

    notifier.notify("title", "body")

    cmd = captured["cmd"]
    for flag in ("-subtitle", "-group", "-sound", "-open", "-execute", "-sender", "-contentImage"):
        assert flag not in cmd


def test_swallows_subprocess_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("duct.notifier.sys.platform", "darwin")
    monkeypatch.setattr("duct.notifier.shutil.which", lambda _: "/usr/local/bin/terminal-notifier")
    monkeypatch.setattr("duct.notifier._default_icon", lambda: None)
    monkeypatch.setattr(
        "duct.notifier.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("kaboom")),
    )

    assert MacNotifier(enabled=True).notify("t", "b") is False


def test_returns_false_on_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("duct.notifier.sys.platform", "darwin")
    monkeypatch.setattr("duct.notifier.shutil.which", lambda _: "/usr/local/bin/terminal-notifier")
    monkeypatch.setattr("duct.notifier._default_icon", lambda: None)
    result = MagicMock()
    result.returncode = 1
    monkeypatch.setattr("duct.notifier.subprocess.run", lambda *a, **kw: result)

    assert MacNotifier(enabled=True).notify("t", "b") is False

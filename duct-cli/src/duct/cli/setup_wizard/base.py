"""Phase framework for the setup wizard.

A phase is one full-screen step: a title, a body the subclass composes,
and navigation messages the app listens for. Phases never talk to each
other — they read shared state (workspace root, config) from the app and
post ``Advance`` / ``GoBack`` when the user moves on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.containers import Vertical
from textual.message import Message

if TYPE_CHECKING:
    from pathlib import Path

    from duct.cli.setup_wizard.app import SetupApp
    from duct.config import WorkspaceConfig


class Phase(Vertical):
    """One wizard step. Subclasses compose the body and post navigation."""

    phase_id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    rail_label: ClassVar[str] = ""
    group: ClassVar[str] = "Setup"
    skippable: ClassVar[bool] = False

    class Advance(Message):
        """The user completed (or deliberately skipped) this phase."""

    class GoBack(Message):
        """The user wants the previous phase."""

    @classmethod
    def is_complete(cls, app: "SetupApp") -> bool:
        """Cheap, local check used for resume and the progress rail."""
        return False

    # ------------------------------------------------------------------
    # Conveniences for subclasses.
    # ------------------------------------------------------------------

    @property
    def wizard(self) -> "SetupApp":
        from duct.cli.setup_wizard.app import SetupApp

        app = self.app
        assert isinstance(app, SetupApp)
        return app

    @property
    def root(self) -> "Path | None":
        return self.wizard.root

    @property
    def cfg(self) -> "WorkspaceConfig | None":
        return self.wizard.cfg

    def advance(self) -> None:
        self.post_message(self.Advance())

    def go_back(self) -> None:
        self.post_message(self.GoBack())

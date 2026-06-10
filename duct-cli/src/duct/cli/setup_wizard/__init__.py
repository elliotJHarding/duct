"""Full-screen Textual setup wizard for duct.

``duct setup`` (and bare ``duct`` on a fresh machine) launches
:func:`duct.cli.setup_wizard.app.run_wizard`. The wizard walks the setup
phases with live previews, runs a mandatory first sync, then offers a
workflow tutorial built from the user's own synced data. All probe and
write logic lives in :mod:`duct.cli.setup_core`, shared with the
``--plain`` prompt flow.
"""

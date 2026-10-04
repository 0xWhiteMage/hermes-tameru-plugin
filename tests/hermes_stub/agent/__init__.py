"""Minimal stand-in for Hermes Agent's ``agent`` package (tests only).

This stub is put on ``sys.path`` by ``tests/conftest.py`` only when a real Hermes checkout is
absent (``HERMES_REPO_ROOT`` unset). It reproduces the *calling conventions* the plugin relies
on, not Hermes' behaviour; the real-Hermes CI job is the source of truth. Signatures and the
skip rules of the pruning seam are mirrored from Hermes Agent (MIT, Nous Research); see
``THIRD_PARTY_NOTICES.md``.
"""

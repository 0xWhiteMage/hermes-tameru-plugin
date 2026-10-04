"""Session-replay harness package marker.

Makes pytest import ``tests/eval/conftest.py`` as ``eval.conftest`` instead of a second top-level
``conftest`` module, which would shadow ``tests/conftest.py`` for ``from conftest import ...`` users.
The harness modules themselves (``replay``, ``scenario``, ``engines``) are imported by plain name; the
eval conftest puts this directory on ``sys.path``.
"""

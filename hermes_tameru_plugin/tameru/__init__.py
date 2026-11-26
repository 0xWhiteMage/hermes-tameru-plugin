"""Vendored Tameru modules (harness-owned package marker).

The sibling ``*.py`` files are synced byte-for-byte from
``src/tameru/`` in https://github.com/0xWhiteMage/tameru-compaction-system
via ``scripts/sync_to_harness.py``. This ``__init__.py`` is owned by this
plugin repo — upstream's own ``__init__.py`` uses absolute ``tameru.*``
imports that do not resolve inside a nested package.
"""

from .compress_context import compress_context  # noqa: F401

# Upstream commit this directory was synced from (scripts/sync_to_harness.py).
VENDORED_FROM = "a0af40c7195f73ca0e0fa59cbe76f1311e6bd079"  # Tameru 1.10.0 source

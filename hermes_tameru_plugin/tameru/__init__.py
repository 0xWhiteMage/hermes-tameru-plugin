"""Vendored Tameru modules (harness-owned package marker).

The sibling ``*.py`` files are synced byte-for-byte from
``src/tameru/`` in https://github.com/0xWhiteMage/tameru-compaction-system
via ``scripts/sync_to_harness.py``. This ``__init__.py`` is owned by this
plugin repo — upstream's own ``__init__.py`` uses absolute ``tameru.*``
imports that do not resolve inside a nested package.
"""

from .compress_context import compress_context  # noqa: F401

# Upstream commit this directory was synced from (scripts/sync_to_harness.py).
VENDORED_FROM = "386a70c1aad7ac300ae661627093a7f992552e30"  # Tameru 1.6.0 source (release commit)

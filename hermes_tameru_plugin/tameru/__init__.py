"""Vendored Tameru modules (harness-owned package marker).

The sibling ``*.py`` files are synced byte-for-byte from
``src/tameru/`` in https://github.com/0xWhiteMage/tameru-compaction-system
via ``scripts/sync_to_harness.py``. This ``__init__.py`` is owned by this
plugin repo — upstream's own ``__init__.py`` uses absolute ``tameru.*``
imports that do not resolve inside a nested package.
"""

from .compress_context import compress_context  # noqa: F401

# Upstream commit this directory was synced from (scripts/sync_to_harness.py).
VENDORED_FROM = "fd8c5b0db23bd570615d5929bf21c02fadbb247c"  # tameru-compaction-system v1.4.0

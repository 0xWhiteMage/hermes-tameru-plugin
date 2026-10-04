"""Deterministic generators for Hermes-shaped tool payloads (used by ``tests/eval``).

Nothing here needs Hermes: the generators only build strings in the shapes Hermes' tools put in
``role: "tool"`` messages. Every generator takes a seeded ``random.Random`` so a given seed always
yields byte-identical output.
"""

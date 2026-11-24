def test_build_index_rejects_malformed_arguments():
    """build_index read ``msg.get`` (via Hermes' tool_calls_by_id or its own walk)
    — a non-dict row crashed with AttributeError deep inside the seam."""
    import pytest
    from hermes_tameru_plugin.supersede import build_index

    with pytest.raises(TypeError, match="sequence of dicts"):
        build_index([42])
    with pytest.raises(TypeError, match="call_id_to_tool"):
        build_index([], 42)
    assert build_index([]).pending.stop == 0

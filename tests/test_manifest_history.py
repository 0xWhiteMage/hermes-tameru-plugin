from hermes_tameru_plugin.manifest import written_files


def test_a_malformed_tool_call_row_does_not_hide_valid_writes():
    def write(path):
        return {"role": "assistant", "tool_calls": [
            {"id": path, "function": {"name": "write_file", "arguments": {"path": path}}},
        ]}

    history = [write("app.py"), {"role": "assistant", "tool_calls": 7}, write("tests.py")]
    assert written_files(history) == ["app.py", "tests.py"]

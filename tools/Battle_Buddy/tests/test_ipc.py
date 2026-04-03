"""Tests for core.ipc — JSONL file-based IPC."""

import json
import os

import pytest

from core.ipc import ipc_write, ipc_read_and_clear


@pytest.fixture
def cmd_file(tmp_path):
    path = str(tmp_path / "test_cmd.jsonl")
    with open(path, "w"):
        pass
    return path


class TestIpcWrite:
    def test_write_creates_content(self, cmd_file):
        assert ipc_write(cmd_file, {"type": "show"})
        with open(cmd_file) as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"type": "show"}

    def test_multiple_writes_accumulate(self, cmd_file):
        ipc_write(cmd_file, {"type": "show"})
        ipc_write(cmd_file, {"type": "hide"})
        ipc_write(cmd_file, {"type": "quit"})
        with open(cmd_file) as f:
            lines = f.readlines()
        assert len(lines) == 3


class TestIpcReadAndClear:
    def test_read_returns_written_payload(self, cmd_file):
        ipc_write(cmd_file, {"type": "toggle"})
        result = ipc_read_and_clear(cmd_file)
        assert result == [{"type": "toggle"}]

    def test_read_clears_file(self, cmd_file):
        ipc_write(cmd_file, {"type": "show"})
        ipc_read_and_clear(cmd_file)
        # Second read should be empty
        assert ipc_read_and_clear(cmd_file) == []

    def test_empty_file_returns_empty_list(self, cmd_file):
        assert ipc_read_and_clear(cmd_file) == []

    def test_malformed_json_skipped(self, cmd_file):
        with open(cmd_file, "w") as f:
            f.write("not json\n")
            f.write('{"type": "show"}\n')
            f.write("also bad\n")
        result = ipc_read_and_clear(cmd_file)
        assert result == [{"type": "show"}]

    def test_unicode_preserved(self, cmd_file):
        payload = {"message": "Hello \u2603 \u26a1"}
        ipc_write(cmd_file, payload)
        result = ipc_read_and_clear(cmd_file)
        assert result == [payload]

    def test_nonexistent_file_returns_empty(self, tmp_path):
        fake = str(tmp_path / "does_not_exist.jsonl")
        assert ipc_read_and_clear(fake) == []

    def test_multiple_payloads_roundtrip(self, cmd_file):
        payloads = [
            {"type": "show"},
            {"type": "report"},
            {"type": "quit"},
        ]
        for p in payloads:
            ipc_write(cmd_file, p)
        result = ipc_read_and_clear(cmd_file)
        assert result == payloads

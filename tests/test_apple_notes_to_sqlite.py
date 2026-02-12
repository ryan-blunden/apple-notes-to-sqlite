from click.testing import CliRunner
from apple_notes_to_sqlite.cli import cli, COUNT_SCRIPT, FOLDERS_SCRIPT, topological_sort
import sqlite_utils
import json
import os
from unittest.mock import patch
import pytest


@pytest.fixture(autouse=True)
def force_osascript(monkeypatch):
    monkeypatch.setenv("APPLE_NOTES_TO_SQLITE_USE_NOTESTORE", "0")


def assert_cli_success(result):
    if result.exit_code != 0:
        raise AssertionError(
            "exit={exit} output={output!r} exception={exc!r} env={env!r}".format(
                exit=result.exit_code,
                output=result.output,
                exc=result.exception,
                env=os.environ.get("APPLE_NOTES_TO_SQLITE_USE_NOTESTORE"),
            )
        )

FOLDER_OUTPUT = b"""
long_id: folder-1
name: Folder 1
parent: 
===
long_id: folder-2
name: Folder 2
parent: folder-1
===
"""

FAKE_OUTPUT = b"""
abcdefg-id: note-1
abcdefg-created: 2023-03-08T16:36:41
abcdefg-updated: 2023-03-08T15:36:41
abcdefg-folder: folder-1
abcdefg-title: Title 1

This is the content of note 1 #Alpha #beta
abcdefgabcdefg
abcdefg-id: note-2
abcdefg-created: 2023-03-08T16:36:41
abcdefg-updated: 2023-03-08T15:36:41
abcdefg-folder: folder-2
abcdefg-title: Title 2

This is the content of note 2 #beta #Gamma
abcdefgabcdefg
""".strip()

EXPECTED_NOTES = [
    {
        "id": "note-1",
        "created": "2023-03-08T16:36:41",
        "updated": "2023-03-08T15:36:41",
        "folder": 1,
        "title": "Title 1",
        "body": "This is the content of note 1 #Alpha #beta",
    },
    {
        "id": "note-2",
        "created": "2023-03-08T16:36:41",
        "updated": "2023-03-08T15:36:41",
        "folder": 2,
        "title": "Title 2",
        "body": "This is the content of note 2 #beta #Gamma",
    },
]
EXPECTED_DUMP_NOTES = [
    dict(note, folder=f"folder-{note['folder']}") for note in EXPECTED_NOTES
]
EXPECTED_DUMP_NOTES_FOLDER_2 = [
    dict(EXPECTED_DUMP_NOTES[1])
]
EXPECTED_NOTES_FOLDER_2 = [
    EXPECTED_NOTES[1]
]


@patch("secrets.token_hex")
def test_apple_notes_to_sqlite(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        assert not os.path.exists("notes.db")
        result = runner.invoke(cli, ["notes.db"])
        assert_cli_success(result)
        # Check that the database was created
        assert os.path.exists("notes.db")
        db = sqlite_utils.Database("notes.db")
        # Check tables were created
        assert set(db.table_names()) == {"notes", "folders", "sync_state"}
        # Check that the notes were inserted
        assert list(db["notes"].rows) == EXPECTED_NOTES
        assert db["sync_state"].get("last_sync")["value"] == "2023-03-08T15:36:41"


@patch("secrets.token_hex")
def test_apple_notes_to_sqlite_dump(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        assert not os.path.exists("notes.db")
        result = runner.invoke(cli, ["--dump"])
        # Check the output
        assert_cli_success(result)
        # Should still be no database
        assert not os.path.exists("notes.db")
        # Output should be newline-delimited JSON
        notes = []
        for line in result.output.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            notes.append(json.loads(line))
        assert notes == EXPECTED_DUMP_NOTES


@patch("secrets.token_hex")
def test_apple_notes_to_sqlite_dump_folder_filter(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["--dump", "--folder", "Folder 2"])
        assert_cli_success(result)
        notes = []
        for line in result.output.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            notes.append(json.loads(line))
        assert notes == EXPECTED_DUMP_NOTES_FOLDER_2


@patch("secrets.token_hex")
def test_folders(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        assert not os.path.exists("folders.db")
        result = runner.invoke(cli, ["folders.db"])
        assert_cli_success(result)
        assert os.path.exists("folders.db")
        db = sqlite_utils.Database("folders.db")
        assert db["sync_state"].get("last_sync")["value"] == "2023-03-08T15:36:41"
        columns = [
            row[1] for row in db.conn.execute("PRAGMA table_info(folders)")
        ]
        assert columns == ["id", "long_id", "name", "parent"]
        foreign_keys = list(db.conn.execute("PRAGMA foreign_key_list(folders)"))
        assert any(
            fk[2] == "folders" and fk[3] == "parent" and fk[4] == "id"
            for fk in foreign_keys
        )
        assert list(db["folders"].rows) == [
            {"id": 1, "long_id": "folder-1", "name": "Folder 1", "parent": None},
            {"id": 2, "long_id": "folder-2", "name": "Folder 2", "parent": 1},
        ]


@patch("secrets.token_hex")
def test_apple_notes_to_sqlite_folder_filter(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["notes.db", "--folder", "Folder 2"])
        assert_cli_success(result)
        db = sqlite_utils.Database("notes.db")
        assert list(db["notes"].rows) == EXPECTED_NOTES_FOLDER_2


@patch("secrets.token_hex")
def test_apple_notes_to_sqlite_folder_path_filter(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["notes.db", "--folder", "Folder 1/Folder 2"])
        assert result.exit_code == 0
        db = sqlite_utils.Database("notes.db")
        assert list(db["notes"].rows) == EXPECTED_NOTES_FOLDER_2


def test_topological_sort_includes_orphans():
    nodes = [
        {"long_id": "a", "name": "A", "parent": "missing"},
        {"long_id": "b", "name": "B", "parent": "a"},
        {"long_id": "c", "name": "C", "parent": "missing"},
    ]
    sorted_nodes = topological_sort(nodes)
    sorted_ids = [node["long_id"] for node in sorted_nodes]
    assert set(sorted_ids) == {"a", "b", "c"}
    assert sorted_ids.index("a") < sorted_ids.index("b")


@patch("secrets.token_hex")
def test_recreate_alias_forces_full_scan(mock_token_hex, fp):
    fp.register_subprocess(["osascript", "-e", COUNT_SCRIPT], stdout=b"2")
    fp.register_subprocess(["osascript", "-e", FOLDERS_SCRIPT], stdout=FOLDER_OUTPUT)
    fp.register_subprocess(["osascript", "-e", fp.any()], stdout=FAKE_OUTPUT)
    mock_token_hex.return_value = "abcdefg"
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["notes.db", "--recreate"])
        assert_cli_success(result)
        db = sqlite_utils.Database("notes.db")
        assert list(db["notes"].rows) == EXPECTED_NOTES

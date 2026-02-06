# apple-notes-to-sqlite

Export Apple Notes to SQLite.

This tool extracts notes and folders from the macOS Notes app using AppleScript and writes them to a SQLite database. It supports full exports, incremental sync, filtered exports, and optional delete-missing behavior.

## Requirements

- macOS with the Notes app
- Python 3.7+
- `uv` (recommended for development and testing)

## Install

From source:

```bash
pip install -e .
```

Or with `uv`:

```bash
uv pip install -e .
```

## Quick Start

Export all notes to `notes.db`:

```bash
apple-notes-to-sqlite notes.db
```

Print notes as newline-delimited JSON to stdout:

```bash
apple-notes-to-sqlite --dump
```

Create the database schema only:

```bash
apple-notes-to-sqlite notes.db --schema
```

## What It Writes

Two tables are created (if missing):

- `folders`: `id`, `long_id`, `name`, `parent`
- `notes`: `id`, `created`, `updated`, `folder`, `title`, `body`

`folder` in `notes` is a foreign key to `folders.id`.

## CLI Options

```
--stop-after INTEGER   Stop after this many notes
--dump                 Output notes to standard output
--schema               Create database schema and exit
--sync                 Only update notes whose 'updated' timestamp has changed
--sync-delete-missing  With --sync, delete notes missing from this run (scope aware of --folder)
--folder TEXT          Only export notes from this folder (by path, name, or long_id)
--help                 Show this message and exit
```

### `--dump`

Outputs notes as newline-delimited JSON. No database is created or modified.

### `--schema`

Creates the `folders` and `notes` tables and exits. This is useful when you want to inspect the schema or pre-create the DB before a later run.

### `--folder`

Limits the export to a specific folder. You can pass:

- A folder name (exact match)
- A folder path like `Parent/Child`
- A folder long_id

Notes in the selected folder and its descendants are included. The folder table includes the required ancestry so foreign keys can be maintained.

### `--sync`

Enables incremental sync behavior:

- Notes are still extracted from Notes, but DB updates are skipped for any note whose `updated` timestamp has not changed.
- After a successful full sync, the tool records a `last_sync` timestamp in the database.
- On subsequent runs, only notes modified after `last_sync` are fetched from Notes, which makes repeated runs much faster.

### `--sync-delete-missing`

Only valid with `--sync`.

Deletes notes from the target DB that were not seen in the current run.

Important behavior:

- When `--sync-delete-missing` is set, the tool disables incremental fetching and performs a full scan to avoid deleting unchanged notes.
- If `--folder` is provided, deletions are limited to notes within that folder subtree.
- This flag cannot be used with `--stop-after`.

## Performance Notes

- The first run of `--sync` is a full scan and can take a long time on large note sets.
- After `last_sync` is recorded, subsequent `--sync` runs only fetch notes modified after that timestamp.
- If a `--sync` run is interrupted before completion, `last_sync` is not updated. The next run will still perform a full scan.

## Safety Notes

- If you interrupt a run, any notes already inserted remain in the DB (partial results are saved).
- `--sync-delete-missing` is powerful; use it only if you expect the target DB to mirror Notes exactly for the selected scope.

## Development

Create a virtual environment and install dependencies with `uv`:

```bash
cd apple-notes-to-sqlite
uv venv
source .venv/bin/activate
uv pip install -e '.[test]'
```

Run tests:

```bash
uv run pytest
```

## Incremental Sync Internals

When `--sync` is enabled:

- The tool stores `last_sync` in a `sync_state` table.
- On subsequent runs it fetches only notes with `modification date > last_sync`.
- Updates are applied only when the stored `updated` value has changed.

If you need to force a full resync, delete the `sync_state` table or the `last_sync` row.

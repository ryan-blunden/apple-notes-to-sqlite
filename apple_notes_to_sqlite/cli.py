import click
import json
import os
import re
import secrets
import sqlite3
import sqlite_utils
import subprocess
from pathlib import Path

COUNT_SCRIPT = """
tell application "Notes"
    set noteCount to count of notes
end tell
log noteCount
"""

FOLDERS_SCRIPT = """
tell application "Notes"
    set allFolders to folders
    repeat with aFolder in allFolders
        set folderId to id of aFolder
        set folderName to name of aFolder
        set folderContainer to container of aFolder
        if class of folderContainer is folder then
            set folderContainerId to id of folderContainer
        else
            set folderContainerId to ""
        end if
        log "long_id: " & folderId
        log "name: " & folderName
        log "parent: " & folderContainerId
        log "==="
    end repeat
end tell
"""

EXTRACT_SCRIPT = """
tell application "Notes"
   repeat with eachNote in every note
      set noteId to the id of eachNote
      set noteTitle to the name of eachNote
      set noteBody to the body of eachNote
      set noteCreatedDate to the creation date of eachNote
      set noteCreated to (noteCreatedDate as «class isot» as string)
      set noteUpdatedDate to the modification date of eachNote
      set noteUpdated to (noteUpdatedDate as «class isot» as string)
      set noteContainer to container of eachNote
      set noteFolderId to the id of noteContainer
      log "{split}-id: " & noteId & "\n"
      log "{split}-created: " & noteCreated & "\n"
      log "{split}-updated: " & noteUpdated & "\n"
      log "{split}-folder: " & noteFolderId & "\n"
      log "{split}-title: " & noteTitle & "\n\n"
      log noteBody & "\n"
      log "{split}{split}" & "\n"
   end repeat
end tell
""".strip()

EXTRACT_SCRIPT_SINCE = """
tell application "Notes"
   set cutoffDate to date "{since}"
   repeat with eachNote in (every note whose modification date > cutoffDate)
      set noteId to the id of eachNote
      set noteTitle to the name of eachNote
      set noteBody to the body of eachNote
      set noteCreatedDate to the creation date of eachNote
      set noteCreated to (noteCreatedDate as «class isot» as string)
      set noteUpdatedDate to the modification date of eachNote
      set noteUpdated to (noteUpdatedDate as «class isot» as string)
      set noteContainer to container of eachNote
      set noteFolderId to the id of noteContainer
      log "{split}-id: " & noteId & "\n"
      log "{split}-created: " & noteCreated & "\n"
      log "{split}-updated: " & noteUpdated & "\n"
      log "{split}-folder: " & noteFolderId & "\n"
      log "{split}-title: " & noteTitle & "\n\n"
      log noteBody & "\n"
      log "{split}{split}" & "\n"
   end repeat
end tell
""".strip()

FOLDER_EXTRACT_SCRIPT = """
tell application "Notes"
   set folderIds to {{{folder_ids}}}
   repeat with folderId in folderIds
      set targetFolder to folder id folderId
      repeat with eachNote in every note of targetFolder
         set noteId to the id of eachNote
         set noteTitle to the name of eachNote
         set noteBody to the body of eachNote
         set noteCreatedDate to the creation date of eachNote
         set noteCreated to (noteCreatedDate as «class isot» as string)
         set noteUpdatedDate to the modification date of eachNote
         set noteUpdated to (noteUpdatedDate as «class isot» as string)
         set noteContainer to container of eachNote
         set noteFolderId to the id of noteContainer
         log "{split}-id: " & noteId & "\n"
         log "{split}-created: " & noteCreated & "\n"
         log "{split}-updated: " & noteUpdated & "\n"
         log "{split}-folder: " & noteFolderId & "\n"
         log "{split}-title: " & noteTitle & "\n\n"
         log noteBody & "\n"
         log "{split}{split}" & "\n"
      end repeat
   end repeat
end tell
""".strip()

FOLDER_EXTRACT_SCRIPT_SINCE = """
tell application "Notes"
   set cutoffDate to date "{since}"
   set folderIds to {{{folder_ids}}}
   repeat with folderId in folderIds
      set targetFolder to folder id folderId
      repeat with eachNote in (every note of targetFolder whose modification date > cutoffDate)
         set noteId to the id of eachNote
         set noteTitle to the name of eachNote
         set noteBody to the body of eachNote
         set noteCreatedDate to the creation date of eachNote
         set noteCreated to (noteCreatedDate as «class isot» as string)
         set noteUpdatedDate to the modification date of eachNote
         set noteUpdated to (noteUpdatedDate as «class isot» as string)
         set noteContainer to container of eachNote
         set noteFolderId to the id of noteContainer
         log "{split}-id: " & noteId & "\n"
         log "{split}-created: " & noteCreated & "\n"
         log "{split}-updated: " & noteUpdated & "\n"
         log "{split}-folder: " & noteFolderId & "\n"
         log "{split}-title: " & noteTitle & "\n\n"
         log noteBody & "\n"
         log "{split}{split}" & "\n"
      end repeat
   end repeat
end tell
""".strip()
DEFAULT_NOTESTORE_PATH = Path(
    "~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"
).expanduser()


@click.command()
@click.version_option()
@click.argument(
    "db_path",
    type=click.Path(file_okay=True, dir_okay=False, allow_dash=False),
    required=False,
)
@click.option("--stop-after", type=int, help="Stop after this many notes")
@click.option("--dump", is_flag=True, help="Output notes to standard output")
@click.option("--schema", is_flag=True, help="Create database schema and exit")
@click.option(
    "--sync",
    is_flag=True,
    help="Only update notes whose 'updated' timestamp has changed",
)
@click.option(
    "--sync-delete-missing",
    is_flag=True,
    help="With --sync, delete notes missing from this run (scope aware of --folder)",
)
@click.option(
    "--folder",
    "folder_filter",
    help="Only export notes from this folder (by path, name, or long_id)",
)
def cli(
    db_path,
    stop_after,
    dump,
    schema,
    sync,
    sync_delete_missing,
    folder_filter,
):
    """
    Export Apple Notes to SQLite

    Example usage:

        apple-notes-to-sqlite notes.db

    This will populate notes.db with 'notes' and 'folders' tables containing
    all of your notes.
    """
    if not db_path and not dump:
        raise click.UsageError(
            "Please specify a path to a database file, or use --dump to see the output",
        )
    if sync_delete_missing and not sync:
        raise click.UsageError("--sync-delete-missing requires --sync")
    if sync_delete_missing and stop_after:
        raise click.UsageError("--sync-delete-missing cannot be used with --stop-after")
    # Use click progressbar
    i = 0
    allowed_note_long_ids = None
    allowed_folder_long_ids = None
    allowed_folder_pks = None
    folder_filter_long_id = None
    folder_long_ids_to_pk = {}
    if dump:
        if folder_filter:
            click.echo("Fetching folders from Notes…", err=True)
            folders = extract_folders()
            (
                folder_filter_long_id,
                allowed_note_long_ids,
                allowed_folder_long_ids,
            ) = resolve_folder_filter(folder_filter, folders)
            folder_long_ids_to_pk = {
                folder.get("long_id"): folder.get("pk")
                for folder in folders
                if folder.get("pk") is not None
            }
            if folder_long_ids_to_pk:
                allowed_folder_pks = [
                    folder_long_ids_to_pk.get(folder_id)
                    for folder_id in allowed_note_long_ids
                    if folder_long_ids_to_pk.get(folder_id) is not None
                ]
        click.echo("Fetching notes from Notes…", err=True)
        if allowed_folder_pks:
            folder_coredata_ids = [
                f"{get_coredata_base()}/ICFolder/p{pk}" for pk in allowed_folder_pks
            ]
            notes_iter = extract_notes_for_folders(folder_coredata_ids)
        else:
            notes_iter = extract_notes()
        for note in notes_iter:
            if (
                allowed_note_long_ids is not None
                and note.get("folder") not in allowed_note_long_ids
            ):
                continue
            click.echo(json.dumps(note))
            i += 1
            if stop_after and i >= stop_after:
                break
    else:
        db = sqlite_utils.Database(db_path)
        existing_updates = None
        seen_note_ids = set() if sync_delete_missing else None
        latest_updated = None
        last_sync = None
        # Create schema
        folder_long_ids_to_id = {}
        if not db["folders"].exists():
            db["folders"].create(
                {
                    "id": int,
                    "long_id": str,
                    "name": str,
                    "parent": int,
                },
                pk="id",
            )
            db["folders"].create_index(["long_id"], unique=True)
            db["folders"].add_foreign_key("parent", "folders", "id")
        if not db["notes"].exists():
            db["notes"].create(
                {
                    "id": str,
                    "created": str,
                    "updated": str,
                    "folder": int,
                    "title": str,
                    "body": str,
                },
                pk="id",
            )
            db["notes"].add_foreign_key("folder", "folders", "id")
        if schema:
            # Our work is done
            return
        if sync:
            if not db["sync_state"].exists():
                db["sync_state"].create({"key": str, "value": str}, pk="key")
            try:
                row = db["sync_state"].get("last_sync")
            except Exception:
                row = None
            if row:
                last_sync = row["value"]
            if db["notes"].exists():
                existing_updates = {
                    row["id"]: row["updated"]
                    for row in db.query("select id, updated from notes")
                }
        if sync_delete_missing:
            # Deletion requires a full scan to avoid removing unchanged notes.
            last_sync = None

        click.echo("Fetching folders from Notes…", err=True)
        folders = extract_folders()
        if folder_filter:
            (
                folder_filter_long_id,
                allowed_note_long_ids,
                allowed_folder_long_ids,
            ) = resolve_folder_filter(folder_filter, folders)
            folder_long_ids_to_pk = {
                folder.get("long_id"): folder.get("pk")
                for folder in folders
                if folder.get("pk") is not None
            }
            if folder_long_ids_to_pk:
                allowed_folder_pks = [
                    folder_long_ids_to_pk.get(folder_id)
                    for folder_id in allowed_note_long_ids
                    if folder_long_ids_to_pk.get(folder_id) is not None
                ]
            folders = [
                folder
                for folder in folders
                if folder.get("long_id") in allowed_folder_long_ids
            ]
        for folder in topological_sort(folders):
            if (
                allowed_folder_long_ids is not None
                and folder.get("parent") not in allowed_folder_long_ids
            ):
                folder["parent"] = None
            folder["parent"] = folder_long_ids_to_id.get(folder["parent"])
            folder_db = {k: v for k, v in folder.items() if k != "pk"}
            id = db["folders"].insert(folder_db, pk="id", replace=True).last_pk
            folder_long_ids_to_id[folder["long_id"]] = id

        expected_count = stop_after
        if not expected_count and allowed_folder_pks:
            expected_count = count_notes_for_folders(allowed_folder_pks)
        if not expected_count and not folder_filter_long_id:
            click.echo("Counting notes…", err=True)
            expected_count = count_notes()

        click.echo("Exporting notes…", err=True)
        if expected_count:
            with click.progressbar(
                length=expected_count,
                label="Exporting notes",
                show_eta=True,
                show_pos=True,
            ) as bar:
                if allowed_folder_pks:
                    folder_coredata_ids = [
                        f"{get_coredata_base()}/ICFolder/p{pk}"
                        for pk in allowed_folder_pks
                    ]
                    notes_iter = extract_notes_for_folders(
                        folder_coredata_ids, since=last_sync
                    )
                else:
                    notes_iter = extract_notes(since=last_sync)
                for note in notes_iter:
                    if (
                        allowed_note_long_ids is not None
                        and note.get("folder") not in allowed_note_long_ids
                    ):
                        continue
                    if seen_note_ids is not None:
                        seen_note_ids.add(note["id"])
                    if existing_updates is not None:
                        if existing_updates.get(note["id"]) == note.get("updated"):
                            bar.update(1)
                            i += 1
                            if stop_after and i >= stop_after:
                                break
                            continue
                    if latest_updated is None or note.get("updated") > latest_updated:
                        latest_updated = note.get("updated")
                    # Fix the folder
                    note["folder"] = folder_long_ids_to_id.get(note["folder"])
                    db["notes"].insert(
                        note,
                        replace=True,
                        alter=True,
                    )
                    bar.update(1)
                    i += 1
                    if stop_after and i >= stop_after:
                        break
        else:
            if allowed_folder_pks:
                folder_coredata_ids = [
                    f"{get_coredata_base()}/ICFolder/p{pk}"
                    for pk in allowed_folder_pks
                ]
                notes_iter = extract_notes_for_folders(
                    folder_coredata_ids, since=last_sync
                )
            else:
                notes_iter = extract_notes(since=last_sync)
            with click.progressbar(
                notes_iter,
                label="Exporting notes",
                show_eta=False,
                show_pos=True,
            ) as bar:
                for note in bar:
                    if (
                        allowed_note_long_ids is not None
                        and note.get("folder") not in allowed_note_long_ids
                    ):
                        continue
                    if seen_note_ids is not None:
                        seen_note_ids.add(note["id"])
                    if existing_updates is not None:
                        if existing_updates.get(note["id"]) == note.get("updated"):
                            i += 1
                            if stop_after and i >= stop_after:
                                break
                            continue
                    if latest_updated is None or note.get("updated") > latest_updated:
                        latest_updated = note.get("updated")
                    # Fix the folder
                    note["folder"] = folder_long_ids_to_id.get(note["folder"])
                    db["notes"].insert(
                        note,
                        replace=True,
                        alter=True,
                    )
                    i += 1
                    if stop_after and i >= stop_after:
                        break

        if sync_delete_missing:
            if seen_note_ids is None:
                return
            if allowed_note_long_ids is not None:
                allowed_folder_ids = [
                    folder_long_ids_to_id.get(folder_id)
                    for folder_id in allowed_note_long_ids
                    if folder_long_ids_to_id.get(folder_id) is not None
                ]
                if allowed_folder_ids:
                    placeholders = ", ".join("?" for _ in allowed_folder_ids)
                    db.execute(
                        f"delete from notes where folder in ({placeholders}) and id not in (select value from json_each(?))",
                        tuple(allowed_folder_ids) + (json.dumps(sorted(seen_note_ids)),),
                    )
            else:
                db.execute(
                    "delete from notes where id not in (select value from json_each(?))",
                    (json.dumps(sorted(seen_note_ids)),),
                )
        if sync and latest_updated and not stop_after:
            db["sync_state"].insert(
                {"key": "last_sync", "value": latest_updated},
                pk="key",
                replace=True,
            )


def count_notes():
    return int(
        subprocess.check_output(
            ["osascript", "-e", COUNT_SCRIPT], stderr=subprocess.STDOUT
        )
        .decode("utf8")
        .strip()
    )


def should_use_notestore():
    env = os.environ.get("APPLE_NOTES_TO_SQLITE_USE_NOTESTORE")
    if env is not None and env.lower() in {"0", "false", "no"}:
        return False
    return DEFAULT_NOTESTORE_PATH.exists()


def count_notes_for_folders(folder_pks):
    if not folder_pks or not should_use_notestore():
        return None
    placeholders = ",".join("?" for _ in folder_pks)
    con = sqlite3.connect(str(DEFAULT_NOTESTORE_PATH))
    row = con.execute(
        "SELECT count(*) FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT=12 AND ZFOLDER IN ({})".format(
            placeholders
        ),
        folder_pks,
    ).fetchone()
    con.close()
    return row[0]


def iter_process_lines(process):
    if process.stdout is None:
        return
    for line in process.stdout:
        yield line
    process.wait()


def extract_notes(since=None):
    split = secrets.token_hex(8)
    if since:
        since = since.replace("T", " ")
        script = EXTRACT_SCRIPT_SINCE.format(split=split, since=since)
    else:
        script = EXTRACT_SCRIPT.format(split=split)
    process = subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Read line by line
    note = {}
    body = []
    for line in iter_process_lines(process):
        line = line.decode("mac_roman").strip()
        if line == f"{split}{split}":
            if note.get("id"):
                note["body"] = "\n".join(body).strip()
                yield note
            note = {}
            body = []
            continue
        found_key = False
        for key in ("id", "title", "folder", "created", "updated"):
            if line.startswith(f"{split}-{key}: "):
                note[key] = line[len(f"{split}-{key}: ") :]
                found_key = True
                continue
        if not found_key:
            body.append(line)


def get_coredata_base():
    coredata_id = (
        subprocess.check_output(
            ["osascript", "-e", 'tell application "Notes" to get id of folder 1'],
            stderr=subprocess.STDOUT,
        )
        .decode("utf8")
        .strip()
    )
    match = re.match(r"(x-coredata://[^/]+)/", coredata_id)
    if not match:
        raise click.ClickException("Could not determine coredata store identifier")
    return match.group(1)


def extract_notes_for_folders(folder_coredata_ids, since=None):
    if not folder_coredata_ids:
        return []
    split = secrets.token_hex(8)
    folder_ids_literal = ", ".join(
        f'"{folder_id}"' for folder_id in folder_coredata_ids
    )
    if since:
        since = since.replace("T", " ")
        script = FOLDER_EXTRACT_SCRIPT_SINCE.format(
            split=split, folder_ids=folder_ids_literal, since=since
        )
    else:
        script = FOLDER_EXTRACT_SCRIPT.format(
            split=split, folder_ids=folder_ids_literal
        )
    process = subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    note = {}
    body = []
    for line in iter_process_lines(process):
        line = line.decode("mac_roman").strip()
        if line == f"{split}{split}":
            if note.get("id"):
                note["body"] = "\n".join(body).strip()
                yield note
            note = {}
            body = []
            continue
        found_key = False
        for key in ("id", "title", "folder", "created", "updated"):
            if line.startswith(f"{split}-{key}: "):
                note[key] = line[len(f"{split}-{key}: ") :]
                found_key = True
                continue
        if not found_key:
            body.append(line)


def extract_folders():
    if should_use_notestore():
        return extract_folders_from_notestore(DEFAULT_NOTESTORE_PATH)
    return extract_folders_from_osascript()


def extract_folders_from_osascript():
    process = subprocess.Popen(
        ["osascript", "-e", FOLDERS_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    folders = []
    folder = {}
    for line in iter_process_lines(process):
        for key in ("long_id", "name", "parent"):
            if line.startswith(f"{key}: ".encode("utf8")):
                folder[key] = line[len(f"{key}: ") :].decode("macroman").strip() or None
                continue
        if line == b"===\n":
            folders.append(folder)
            folder = {}
    return folders


def extract_folders_from_notestore(db_path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME='ICFolder'"
    ).fetchone()
    if not row:
        raise click.ClickException("Could not find ICFolder entity in NoteStore.sqlite")
    folder_ent = row["Z_ENT"]
    rows = con.execute(
        """
        SELECT
            f.Z_PK AS pk,
            COALESCE(f.ZNAME, f.ZTITLE, f.ZTITLE1, f.ZTITLE2, f.ZUSERTITLE) AS name,
            p.Z_PK AS parent_pk
        FROM ZICCLOUDSYNCINGOBJECT f
        LEFT JOIN ZICCLOUDSYNCINGOBJECT p ON f.ZPARENT = p.Z_PK
        WHERE f.Z_ENT = ?
        """,
        (folder_ent,),
    ).fetchall()
    con.close()
    base = get_coredata_base()
    return [
        {
            "pk": row["pk"],
            "long_id": f"{base}/ICFolder/p{row['pk']}",
            "name": row["name"],
            "parent": f"{base}/ICFolder/p{row['parent_pk']}"
            if row["parent_pk"]
            else None,
        }
        for row in rows
    ]


def topological_sort(nodes):
    nodes = list(nodes)
    children = {}
    nodes_by_long_id = {}
    for node in nodes:
        long_id = node.get("long_id")
        if long_id is not None:
            nodes_by_long_id[long_id] = node
        parent_id = node.get("parent")
        if parent_id is not None:
            children.setdefault(parent_id, []).append(node)

    visited = set()

    def traverse(node, result):
        long_id = node.get("long_id")
        if long_id in visited:
            return
        visited.add(long_id)
        result.append(node)
        if long_id in children:
            for child in children[long_id]:
                traverse(child, result)

    sorted_data = []
    for node in nodes:
        parent_id = node.get("parent")
        if parent_id is None or parent_id not in nodes_by_long_id:
            traverse(node, sorted_data)

    for node in nodes:
        if node.get("long_id") not in visited:
            traverse(node, sorted_data)

    return sorted_data


def resolve_folder_filter(folder_filter, folders):
    folder_paths = build_folder_paths(folders)
    nodes_by_long_id = {
        folder.get("long_id"): folder for folder in folders if folder.get("long_id")
    }
    exact_long_id_matches = [
        folder for folder in folders if folder.get("long_id") == folder_filter
    ]
    if exact_long_id_matches:
        target = exact_long_id_matches[0]
    elif "/" in folder_filter:
        path = [part for part in folder_filter.split("/") if part]
        if not path:
            raise click.UsageError("Folder path cannot be empty.")
        parents_by_id = {
            folder.get("long_id"): folder.get("parent") for folder in folders
        }
        candidates = [folder for folder in folders if folder.get("name") == path[-1]]
        matches = []
        for candidate in candidates:
            current = candidate
            ok = True
            for part in reversed(path[:-1]):
                parent_id = parents_by_id.get(current.get("long_id"))
                if not parent_id:
                    ok = False
                    break
                parent = nodes_by_long_id.get(parent_id)
                if not parent or parent.get("name") != part:
                    ok = False
                    break
                current = parent
            if ok:
                matches.append(candidate)
        if not matches:
            raise click.UsageError(
                f'No folder found matching path "{folder_filter}".\n'
                "Available folder paths:\n"
                + "\n".join(folder_paths)
            )
        if len(matches) > 1:
            match_lines = []
            for folder in matches:
                match_lines.append(
                    f'- long_id="{folder.get("long_id")}", name="{folder.get("name")}", '
                    f'parent="{folder.get("parent")}"'
                )
            raise click.UsageError(
                "Multiple folders matched that path. Use --folder with the long_id.\n"
                + "\n".join(match_lines)
            )
        target = matches[0]
    else:
        name_matches = [folder for folder in folders if folder.get("name") == folder_filter]
        if not name_matches:
            available_names = sorted(
                {folder.get("name") for folder in folders if folder.get("name")}
            )
            raise click.UsageError(
                f'No folder found matching "{folder_filter}". '
                "Use the folder name, path, or long_id.\n"
                "Available folder names:\n"
                + "\n".join(available_names)
            )
        if len(name_matches) > 1:
            match_lines = []
            for folder in name_matches:
                match_lines.append(
                    f'- long_id="{folder.get("long_id")}", name="{folder.get("name")}", '
                    f'parent="{folder.get("parent")}"'
                )
            raise click.UsageError(
                "Multiple folders matched that name. Use --folder with the long_id.\n"
                + "\n".join(match_lines)
            )
        target = name_matches[0]

    target_long_id = target.get("long_id")
    children = {}
    parents_by_id = {}
    for folder in folders:
        parent_id = folder.get("parent")
        parents_by_id[folder.get("long_id")] = parent_id
        if parent_id is not None:
            children.setdefault(parent_id, []).append(folder.get("long_id"))

    allowed = set()
    queue = [target_long_id]
    while queue:
        current = queue.pop(0)
        if current in allowed:
            continue
        allowed.add(current)
        for child in children.get(current, []):
            queue.append(child)

    note_folders = set(allowed)
    ancestor = parents_by_id.get(target_long_id)
    while ancestor:
        if ancestor in allowed:
            break
        allowed.add(ancestor)
        ancestor = parents_by_id.get(ancestor)

    return target_long_id, note_folders, allowed


def build_folder_paths(folders):
    nodes_by_long_id = {
        folder.get("long_id"): folder for folder in folders if folder.get("long_id")
    }
    parents_by_id = {
        folder.get("long_id"): folder.get("parent")
        for folder in folders
        if folder.get("long_id")
    }
    paths = []
    for folder in folders:
        long_id = folder.get("long_id")
        name = folder.get("name")
        if not long_id or not name:
            continue
        parts = [name]
        seen = {long_id}
        parent_id = parents_by_id.get(long_id)
        while parent_id:
            if parent_id in seen:
                break
            seen.add(parent_id)
            parent = nodes_by_long_id.get(parent_id)
            if not parent or not parent.get("name"):
                break
            parts.append(parent["name"])
            parent_id = parents_by_id.get(parent_id)
        path = "/".join(reversed(parts))
        paths.append(path)
    return sorted(set(paths))

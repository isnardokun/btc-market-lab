"""Validate that externally supplied SQLite read transactions belong to the DB path.

Used only for existing query-only audit connections. Never creates, modifies,
opens or attaches any other database. Conservative: fail closed on attached DBs.
"""
from pathlib import Path
import sqlite3


def verify_read_snapshot(connection, requested_path):
    """Require query-only BEGIN on the *same* SQLite main database.

    A query-only transaction alone is insufficient: the caller could supply
    an active read transaction for another file, or attach additional databases
    which make audit provenance ambiguous. Validate PRAGMA database_list.
    """
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("Shared snapshot must be an SQLite Connection")
    if not connection.in_transaction:
        raise ValueError("Shared snapshot requires active transaction")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise ValueError("Shared snapshot requires PRAGMA query_only=ON")

    expected = Path(requested_path).resolve(strict=True)
    databases = connection.execute("PRAGMA database_list").fetchall()
    # SQLite may create its own unnamed "temp" schema as a SIDE EFFECT of
    # PRAGMA integrity_check, even with query_only=ON. Reject external ATTACH
    # aliases, but do not mistake SQLite's empty-path temp schema for one.
    main_entries = [file for _, name, file in databases if name == "main"]
    extra = [(name, file) for _, name, file in databases
             if name != "main" and not (name == "temp" and not file)]
    if len(main_entries) != 1 or extra:
        raise ValueError("Shared snapshot cannot have attached databases")
    actual = main_entries[0]
    if not actual or Path(actual).resolve(strict=True) != expected:
        raise ValueError("Shared snapshot SQLite main path mismatch")

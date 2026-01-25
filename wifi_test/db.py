"""SQLite database operations for wifi-test."""

import sqlite3
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from .config import get_config


def _get_db_path() -> Path:
    cfg = get_config()
    db_path_str = cfg.db_path or "wifi_data.db"
    p = Path(db_path_str).expanduser()
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _ensure_created_at(conn: sqlite3.Connection, table: str, ts_col: str) -> None:
    """Ensure created_at column exists and is populated.

    - Adds column if missing, with DEFAULT CURRENT_TIMESTAMP for new rows
    - Backfills existing rows: created_at := ts_col if present else CURRENT_TIMESTAMP
    """
    if not _column_exists(conn, table, "created_at"):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        )
        conn.commit()

    # Backfill any NULL created_at values
    conn.execute(
        f"""
        UPDATE {table}
        SET created_at = COALESCE({ts_col}, CURRENT_TIMESTAMP)
        WHERE created_at IS NULL
        """
    )
    conn.commit()


def _ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
		CREATE TABLE IF NOT EXISTS scan_results (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			timestamp TEXT,
			ssid TEXT,
            bssid TEXT UNIQUE,
			frequency REAL,
			band TEXT,
			signal REAL,
			channel INTEGER,
            security TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
		);
		"""
    )
    cur.execute(
        """
		CREATE TABLE IF NOT EXISTS speedtest_results (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			timestamp TEXT,
			tool TEXT,
			ssid TEXT,
			download_mbps REAL,
			upload_mbps REAL,
			ping_ms REAL,
			jitter_ms REAL,
			server TEXT,
			isp TEXT,
			packet_loss REAL,
            result_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
		);
		"""
    )
    conn.commit()

    # Ensure created_at exists and is backfilled for existing tables
    _ensure_created_at(conn, "scan_results", "timestamp")
    _ensure_created_at(conn, "speedtest_results", "timestamp")


def _connect() -> sqlite3.Connection:
    path = _get_db_path()
    conn = sqlite3.connect(str(path))
    _ensure_tables(conn)
    return conn


def insert_scan_results(results: Iterable[Mapping]) -> int:
    """Insert multiple WiFi scan results.

    Each mapping should contain keys: timestamp, ssid, bssid, frequency,
    band, signal, channel, security.
    Returns number of rows inserted.
    """
    rows = [
        (
            r.get("timestamp"),
            r.get("ssid"),
            r.get("bssid"),
            float(r.get("frequency") or 0),
            r.get("band"),
            float(r.get("signal") or 0),
            r.get("channel"),
            r.get("security"),
        )
        for r in results
    ]
    if not rows:
        return 0

    # Use UPSERT to ensure one row per bssid, keeping the latest values
    conn = _connect()
    try:
        conn.executemany(
            """
            INSERT INTO scan_results
                (timestamp, ssid, bssid, frequency, band, signal, channel, security)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bssid) DO UPDATE SET
                timestamp = excluded.timestamp,
                ssid = excluded.ssid,
                frequency = excluded.frequency,
                band = excluded.band,
                signal = excluded.signal,
                channel = excluded.channel,
                security = excluded.security,
                created_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def insert_speedtest_result(result: Mapping) -> int:
    """Insert a single speedtest result mapping.

    Keys: timestamp, tool, ssid, download_mbps, upload_mbps, ping_ms, jitter_ms,
    server, isp, packet_loss, result_url.
    Returns 1 if inserted, 0 otherwise.
    """
    conn = _connect()
    try:
        conn.execute(
            """
			INSERT INTO speedtest_results
				(timestamp, tool, ssid, download_mbps, upload_mbps, ping_ms, jitter_ms,
				 server, isp, packet_loss, result_url)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
            (
                result.get("timestamp"),
                result.get("tool"),
                result.get("ssid"),
                float(result.get("download_mbps") or 0),
                float(result.get("upload_mbps") or 0),
                float(result.get("ping_ms") or 0),
                float(result.get("jitter_ms") or 0),
                result.get("server"),
                result.get("isp"),
                float(result.get("packet_loss") or 0),
                result.get("result_url"),
            ),
        )
        conn.commit()
        return 1
    finally:
        conn.close()


def get_all_scans(limit: Optional[int] = None) -> List[Mapping]:
    conn = _connect()
    try:
        cur = conn.cursor()
        if limit and limit > 0:
            cur.execute(
                "SELECT * FROM scan_results ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        else:
            cur.execute("SELECT * FROM scan_results ORDER BY id DESC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_all_speedtests(limit: Optional[int] = None) -> List[Mapping]:
    conn = _connect()
    try:
        cur = conn.cursor()
        if limit and limit > 0:
            cur.execute(
                "SELECT * FROM speedtest_results ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        else:
            cur.execute("SELECT * FROM speedtest_results ORDER BY id DESC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()

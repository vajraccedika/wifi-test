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


def _ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
		CREATE TABLE IF NOT EXISTS network_results (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			tool TEXT,
			ssid TEXT,
            bssid TEXT UNIQUE,
			frequency REAL,
			band TEXT,
			signal REAL,
			channel INTEGER,
            security TEXT,
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

    # Ensure created_at column exists for older databases
    if not _column_exists(conn, "network_results", "created_at"):
        conn.execute(
            "ALTER TABLE network_results ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        )
        conn.commit()


def _connect() -> sqlite3.Connection:
    path = _get_db_path()
    conn = sqlite3.connect(str(path))
    _ensure_tables(conn)
    return conn


def insert_scan_results(results: Iterable[Mapping]) -> int:
    """Insert multiple network result mappings.

    Each mapping should contain keys: ssid, bssid, frequency,
    band, signal, channel, security.
    Returns number of rows inserted.
    """
    rows = [
        (
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
            INSERT INTO network_results
                (ssid, bssid, frequency, band, signal, channel, security)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bssid) DO UPDATE SET
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
    """Insert a single network result mapping.

    Keys: tool, ssid, bssid, download_mbps, upload_mbps, ping_ms, jitter_ms,
    server, isp, packet_loss, result_url.
    Returns 1 if inserted, 0 otherwise.
    """
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO network_results
                (tool, ssid, bssid, download_mbps, upload_mbps, ping_ms, jitter_ms,
                 server, isp, packet_loss, result_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bssid) DO UPDATE SET
                tool = excluded.tool,
                ssid = COALESCE(excluded.ssid, network_results.ssid),
                download_mbps = excluded.download_mbps,
                upload_mbps = excluded.upload_mbps,
                ping_ms = excluded.ping_ms,
                jitter_ms = excluded.jitter_ms,
                server = excluded.server,
                isp = excluded.isp,
                packet_loss = excluded.packet_loss,
                result_url = excluded.result_url,
                created_at = CURRENT_TIMESTAMP
			""",
            (
                result.get("tool"),
                result.get("ssid"),
                result.get("bssid"),
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


def get_all_results(limit: Optional[int] = None) -> List[Mapping]:
    """Fetch network results ordered by bssid ascending."""
    conn = _connect()
    try:
        cur = conn.cursor()
        if limit and limit > 0:
            cur.execute(
                "SELECT * FROM network_results ORDER BY bssid ASC LIMIT ?",
                (limit,),
            )
        else:
            cur.execute("SELECT * FROM network_results ORDER BY bssid ASC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()

"""SQLite database operations for wifi-test."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, List, Mapping, Optional

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


@contextmanager
def _get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _to_float(value: Any) -> float:
    """Convert value to float, defaulting to 0.0 if None/empty."""
    return float(value) if value else 0.0


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
            _to_float(r.get("frequency")),
            r.get("band"),
            _to_float(r.get("signal")),
            r.get("channel"),
            r.get("security"),
        )
        for r in results
    ]
    if not rows:
        return 0

    # Use UPSERT to ensure one row per bssid, keeping the latest values
    with _get_connection() as conn:
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


def insert_speedtest_result(result: Mapping) -> int:
    """Insert a single network result mapping.

    Keys: tool, ssid, bssid, download_mbps, upload_mbps, ping_ms, jitter_ms,
    server, isp, packet_loss, result_url.
    Returns 1 if inserted, 0 otherwise.
    """
    with _get_connection() as conn:
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
                _to_float(result.get("download_mbps")),
                _to_float(result.get("upload_mbps")),
                _to_float(result.get("ping_ms")),
                _to_float(result.get("jitter_ms")),
                result.get("server"),
                result.get("isp"),
                _to_float(result.get("packet_loss")),
                result.get("result_url"),
            ),
        )
        conn.commit()
        return 1


def get_all_results(limit: Optional[int] = None) -> List[Mapping]:
    """Fetch network results ordered by bssid ascending."""
    with _get_connection() as conn:
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

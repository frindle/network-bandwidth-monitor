import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get('DB_PATH', '/data/netmon.db')


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bw_raw (
                ts      INTEGER NOT NULL,
                iface   TEXT    NOT NULL,
                rx_rate REAL    NOT NULL,
                tx_rate REAL    NOT NULL,
                PRIMARY KEY (ts, iface)
            );
            CREATE TABLE IF NOT EXISTS bw_hourly (
                hour_ts      INTEGER NOT NULL,
                iface        TEXT    NOT NULL,
                rx_bytes     INTEGER NOT NULL DEFAULT 0,
                tx_bytes     INTEGER NOT NULL DEFAULT 0,
                peak_rx_rate REAL    NOT NULL DEFAULT 0,
                peak_tx_rate REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, iface)
            );
            CREATE TABLE IF NOT EXISTS conn_hourly (
                hour_ts     INTEGER NOT NULL,
                iface       TEXT    NOT NULL,
                protocol    TEXT    NOT NULL,
                remote_ip   TEXT    NOT NULL,
                remote_port INTEGER NOT NULL,
                tx_bytes    INTEGER NOT NULL DEFAULT 0,
                rx_bytes    INTEGER NOT NULL DEFAULT 0,
                hit_count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, iface, protocol, remote_ip, remote_port)
            );
            CREATE TABLE IF NOT EXISTS container_bw_raw (
                ts             INTEGER NOT NULL,
                container_id   TEXT    NOT NULL,
                container_name TEXT    NOT NULL,
                rx_rate        REAL    NOT NULL,
                tx_rate        REAL    NOT NULL,
                PRIMARY KEY (ts, container_id)
            );
            CREATE TABLE IF NOT EXISTS container_bw_hourly (
                hour_ts        INTEGER NOT NULL,
                container_id   TEXT    NOT NULL,
                container_name TEXT    NOT NULL,
                rx_bytes       INTEGER NOT NULL DEFAULT 0,
                tx_bytes       INTEGER NOT NULL DEFAULT 0,
                peak_rx_rate   REAL    NOT NULL DEFAULT 0,
                peak_tx_rate   REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, container_id)
            );
            CREATE TABLE IF NOT EXISTS dns_cache (
                ip          TEXT    PRIMARY KEY,
                hostname    TEXT,
                resolved_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bw_raw_ts       ON bw_raw(ts);
            CREATE INDEX IF NOT EXISTS idx_bw_hourly_ts    ON bw_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_conn_ht_ts      ON conn_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_cbw_raw_ts      ON container_bw_raw(ts);
            CREATE INDEX IF NOT EXISTS idx_cbw_hourly_ts   ON container_bw_hourly(hour_ts);
        """)


# ── interface bandwidth ────────────────────────────────────────────────────────

def insert_bw_raw(samples):
    with _db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO bw_raw VALUES (?,?,?,?)", samples
        )


def query_bw_raw(iface, since):
    with _db() as conn:
        return conn.execute(
            "SELECT ts, rx_rate, tx_rate FROM bw_raw WHERE iface=? AND ts>=? ORDER BY ts",
            (iface, since)
        ).fetchall()


def query_bw_hourly(iface, since):
    with _db() as conn:
        return conn.execute(
            "SELECT hour_ts, rx_bytes, tx_bytes FROM bw_hourly "
            "WHERE iface=? AND hour_ts>=? ORDER BY hour_ts",
            (iface, since)
        ).fetchall()


def query_totals_by_iface(since_hour):
    with _db() as conn:
        return conn.execute("""
            SELECT iface,
                   SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes + tx_bytes) AS total_bytes
            FROM bw_hourly
            WHERE hour_ts >= ?
            GROUP BY iface
            ORDER BY total_bytes DESC
        """, (since_hour,)).fetchall()


def query_totals_by_container(since_hour):
    with _db() as conn:
        return conn.execute("""
            SELECT container_id, container_name,
                   SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes + tx_bytes) AS total_bytes
            FROM container_bw_hourly
            WHERE hour_ts >= ?
            GROUP BY container_id
            ORDER BY total_bytes DESC
        """, (since_hour,)).fetchall()


def known_interfaces():
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT iface FROM bw_raw "
            "UNION SELECT DISTINCT iface FROM bw_hourly"
        ).fetchall()
        return [r['iface'] for r in rows]


# ── connections ────────────────────────────────────────────────────────────────

def upsert_conn_delta(hour_ts, iface, protocol, remote_ip, remote_port, tx_delta, rx_delta):
    with _db() as conn:
        conn.execute("""
            INSERT INTO conn_hourly
                (hour_ts, iface, protocol, remote_ip, remote_port, tx_bytes, rx_bytes, hit_count)
            VALUES (?,?,?,?,?,?,?,1)
            ON CONFLICT(hour_ts, iface, protocol, remote_ip, remote_port) DO UPDATE SET
                tx_bytes  = tx_bytes  + excluded.tx_bytes,
                rx_bytes  = rx_bytes  + excluded.rx_bytes,
                hit_count = hit_count + 1
        """, (hour_ts, iface, protocol, remote_ip, remote_port, tx_delta, rx_delta))


def query_connections(iface, since_hour, limit=100):
    with _db() as conn:
        if iface == 'all':
            return conn.execute("""
                SELECT remote_ip, remote_port, protocol,
                       SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                       SUM(tx_bytes + rx_bytes) AS total_bytes,
                       SUM(hit_count) AS hit_count
                FROM conn_hourly
                WHERE hour_ts >= ?
                GROUP BY remote_ip, remote_port, protocol
                ORDER BY total_bytes DESC
                LIMIT ?
            """, (since_hour, limit)).fetchall()
        return conn.execute("""
            SELECT remote_ip, remote_port, protocol,
                   SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes + rx_bytes) AS total_bytes,
                   SUM(hit_count) AS hit_count
            FROM conn_hourly
            WHERE iface=? AND hour_ts >= ?
            GROUP BY remote_ip, remote_port, protocol
            ORDER BY total_bytes DESC
            LIMIT ?
        """, (iface, since_hour, limit)).fetchall()


# ── container bandwidth ────────────────────────────────────────────────────────

def insert_container_bw_raw(samples):
    """samples: [(ts, container_id, container_name, rx_rate, tx_rate)]"""
    with _db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO container_bw_raw VALUES (?,?,?,?,?)", samples
        )


def query_container_bw_raw(container_id, since):
    with _db() as conn:
        return conn.execute(
            "SELECT ts, rx_rate, tx_rate FROM container_bw_raw "
            "WHERE container_id=? AND ts>=? ORDER BY ts",
            (container_id, since)
        ).fetchall()


def query_container_bw_hourly(container_id, since):
    with _db() as conn:
        return conn.execute(
            "SELECT hour_ts, rx_bytes, tx_bytes FROM container_bw_hourly "
            "WHERE container_id=? AND hour_ts>=? ORDER BY hour_ts",
            (container_id, since)
        ).fetchall()


def known_containers():
    with _db() as conn:
        rows = conn.execute("""
            SELECT container_id, container_name FROM container_bw_raw
            GROUP BY container_id
            HAVING MAX(ts) > ?
            UNION
            SELECT container_id, container_name FROM container_bw_hourly
            GROUP BY container_id
        """, (int(time.time()) - 7 * 86400,)).fetchall()
        return [{'id': r['container_id'], 'name': r['container_name']} for r in rows]


# ── DNS cache ──────────────────────────────────────────────────────────────────

def get_dns(ip):
    with _db() as conn:
        row = conn.execute(
            "SELECT hostname FROM dns_cache WHERE ip=?", (ip,)
        ).fetchone()
        return row['hostname'] if row else None


def cache_dns(ip, hostname):
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dns_cache VALUES (?,?,?)",
            (ip, hostname, int(time.time()))
        )


def stale_ips(ips):
    ttl = int(time.time()) - 86400
    with _db() as conn:
        result = []
        for ip in ips:
            row = conn.execute(
                "SELECT ip FROM dns_cache WHERE ip=? AND resolved_at > ?", (ip, ttl)
            ).fetchone()
            if not row:
                result.append(ip)
        return result


# ── hourly aggregation ─────────────────────────────────────────────────────────

def aggregate_hourly():
    """Roll *_raw tables into *_hourly, prune raw rows older than 7 days."""
    cutoff   = int(time.time()) - 7 * 86400
    cur_hour = (int(time.time()) // 3600) * 3600
    with _db() as conn:
        conn.execute("""
            INSERT INTO bw_hourly (hour_ts, iface, rx_bytes, tx_bytes, peak_rx_rate, peak_tx_rate)
            SELECT (ts/3600)*3600 AS h, iface,
                   CAST(SUM(rx_rate*10) AS INTEGER),
                   CAST(SUM(tx_rate*10) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM bw_raw WHERE ts < ?
            GROUP BY h, iface
            ON CONFLICT(hour_ts, iface) DO UPDATE SET
                rx_bytes     = MAX(bw_hourly.rx_bytes,     excluded.rx_bytes),
                tx_bytes     = MAX(bw_hourly.tx_bytes,     excluded.tx_bytes),
                peak_rx_rate = MAX(bw_hourly.peak_rx_rate, excluded.peak_rx_rate),
                peak_tx_rate = MAX(bw_hourly.peak_tx_rate, excluded.peak_tx_rate)
        """, (cur_hour,))

        conn.execute("""
            INSERT INTO container_bw_hourly
                (hour_ts, container_id, container_name, rx_bytes, tx_bytes, peak_rx_rate, peak_tx_rate)
            SELECT (ts/3600)*3600 AS h, container_id, container_name,
                   CAST(SUM(rx_rate*10) AS INTEGER),
                   CAST(SUM(tx_rate*10) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM container_bw_raw WHERE ts < ?
            GROUP BY h, container_id
            ON CONFLICT(hour_ts, container_id) DO UPDATE SET
                container_name = excluded.container_name,
                rx_bytes       = MAX(container_bw_hourly.rx_bytes,     excluded.rx_bytes),
                tx_bytes       = MAX(container_bw_hourly.tx_bytes,     excluded.tx_bytes),
                peak_rx_rate   = MAX(container_bw_hourly.peak_rx_rate, excluded.peak_rx_rate),
                peak_tx_rate   = MAX(container_bw_hourly.peak_tx_rate, excluded.peak_tx_rate)
        """, (cur_hour,))

        conn.execute("DELETE FROM bw_raw WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM container_bw_raw WHERE ts < ?", (cutoff,))

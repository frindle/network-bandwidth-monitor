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
                source_ip   TEXT    NOT NULL DEFAULT '',
                protocol    TEXT    NOT NULL,
                remote_ip   TEXT    NOT NULL,
                remote_port INTEGER NOT NULL,
                tx_bytes    INTEGER NOT NULL DEFAULT 0,
                rx_bytes    INTEGER NOT NULL DEFAULT 0,
                hit_count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, iface, source_ip, protocol, remote_ip, remote_port)
            );
            CREATE TABLE IF NOT EXISTS cf_tunnel_hourly (
                hour_ts      INTEGER NOT NULL,
                service_ip   TEXT    NOT NULL,
                service_port INTEGER NOT NULL,
                protocol     TEXT    NOT NULL,
                tx_bytes     INTEGER NOT NULL DEFAULT 0,
                rx_bytes     INTEGER NOT NULL DEFAULT 0,
                hit_count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, service_ip, service_port, protocol)
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
            CREATE TABLE IF NOT EXISTS device_labels (
                ip         TEXT    PRIMARY KEY,
                label      TEXT    NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT    PRIMARY KEY,
                value      TEXT    NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS starlink_bw_raw (
                ts      INTEGER NOT NULL PRIMARY KEY,
                rx_rate REAL    NOT NULL,
                tx_rate REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS starlink_bw_hourly (
                hour_ts      INTEGER NOT NULL PRIMARY KEY,
                rx_bytes     INTEGER NOT NULL DEFAULT 0,
                tx_bytes     INTEGER NOT NULL DEFAULT 0,
                peak_rx_rate REAL    NOT NULL DEFAULT 0,
                peak_tx_rate REAL    NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS fw_devices (
                mac         TEXT    PRIMARY KEY,
                ip          TEXT    NOT NULL DEFAULT '',
                name        TEXT    NOT NULL DEFAULT '',
                mac_vendor  TEXT    NOT NULL DEFAULT '',
                group_name  TEXT    NOT NULL DEFAULT '',
                fw_rx_bytes INTEGER NOT NULL DEFAULT 0,
                fw_tx_bytes INTEGER NOT NULL DEFAULT 0,
                last_active INTEGER NOT NULL DEFAULT 0,
                updated_at  INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_bw_raw_ts       ON bw_raw(ts);
            CREATE INDEX IF NOT EXISTS idx_bw_hourly_ts    ON bw_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_conn_ht_ts      ON conn_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_cf_tunnel_ts    ON cf_tunnel_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_fw_devices_ip   ON fw_devices(ip);
            CREATE INDEX IF NOT EXISTS idx_cbw_raw_ts      ON container_bw_raw(ts);
            CREATE INDEX IF NOT EXISTS idx_cbw_hourly_ts   ON container_bw_hourly(hour_ts);
        """)
    _migrate()
    # idx_conn_source requires source_ip which may not exist until after _migrate runs
    with _db() as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conn_source ON conn_hourly(source_ip)")


def _migrate():
    """Apply schema upgrades to existing databases."""
    with _db() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        # v0.4 → v0.5: add starlink tables
        if 'starlink_bw_raw' not in tables:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS starlink_bw_raw (
                    ts      INTEGER NOT NULL PRIMARY KEY,
                    rx_rate REAL    NOT NULL,
                    tx_rate REAL    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS starlink_bw_hourly (
                    hour_ts      INTEGER NOT NULL PRIMARY KEY,
                    rx_bytes     INTEGER NOT NULL DEFAULT 0,
                    tx_bytes     INTEGER NOT NULL DEFAULT 0,
                    peak_rx_rate REAL    NOT NULL DEFAULT 0,
                    peak_tx_rate REAL    NOT NULL DEFAULT 0
                );
            """)

        # v0.3 → v0.4: add fw_devices table
        if 'fw_devices' not in tables:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fw_devices (
                    mac        TEXT    PRIMARY KEY,
                    ip         TEXT    NOT NULL DEFAULT '',
                    name       TEXT    NOT NULL DEFAULT '',
                    mac_vendor TEXT    NOT NULL DEFAULT '',
                    last_active INTEGER NOT NULL DEFAULT 0,
                    updated_at  INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_fw_devices_ip ON fw_devices(ip);
            """)

        # v0.5 → v0.6: add group_name, fw_rx_bytes, fw_tx_bytes to fw_devices
        fw_cols = {r[1] for r in conn.execute("PRAGMA table_info(fw_devices)").fetchall()}
        if 'group_name' not in fw_cols:
            conn.execute("ALTER TABLE fw_devices ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
        if 'fw_rx_bytes' not in fw_cols:
            conn.execute("ALTER TABLE fw_devices ADD COLUMN fw_rx_bytes INTEGER NOT NULL DEFAULT 0")
        if 'fw_tx_bytes' not in fw_cols:
            conn.execute("ALTER TABLE fw_devices ADD COLUMN fw_tx_bytes INTEGER NOT NULL DEFAULT 0")

        cols = {r[1] for r in conn.execute("PRAGMA table_info(conn_hourly)").fetchall()}
        if 'source_ip' not in cols:
            # v0.2 → v0.3: add source_ip to PRIMARY KEY
            conn.executescript("""
                ALTER TABLE conn_hourly RENAME TO _conn_hourly_v2;
                CREATE TABLE conn_hourly (
                    hour_ts     INTEGER NOT NULL,
                    iface       TEXT    NOT NULL,
                    source_ip   TEXT    NOT NULL DEFAULT '',
                    protocol    TEXT    NOT NULL,
                    remote_ip   TEXT    NOT NULL,
                    remote_port INTEGER NOT NULL,
                    tx_bytes    INTEGER NOT NULL DEFAULT 0,
                    rx_bytes    INTEGER NOT NULL DEFAULT 0,
                    hit_count   INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (hour_ts, iface, source_ip, protocol, remote_ip, remote_port)
                );
                INSERT INTO conn_hourly
                    SELECT hour_ts, iface, '', protocol, remote_ip, remote_port,
                           tx_bytes, rx_bytes, hit_count
                    FROM _conn_hourly_v2;
                DROP TABLE _conn_hourly_v2;
                CREATE INDEX IF NOT EXISTS idx_conn_ht_ts  ON conn_hourly(hour_ts);
                CREATE INDEX IF NOT EXISTS idx_conn_source ON conn_hourly(source_ip);
            """)


# ── interface bandwidth ────────────────────────────────────────────────────────

def insert_bw_raw(samples):
    with _db() as conn:
        conn.executemany("INSERT OR REPLACE INTO bw_raw VALUES (?,?,?,?)", samples)


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


def known_interfaces():
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT iface FROM bw_raw "
            "UNION SELECT DISTINCT iface FROM bw_hourly"
        ).fetchall()
        return [r['iface'] for r in rows]


def query_totals_by_iface(since_hour):
    with _db() as conn:
        return conn.execute("""
            SELECT iface,
                   SUM(rx_bytes) AS rx_bytes, SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes + tx_bytes) AS total_bytes
            FROM bw_hourly WHERE hour_ts >= ?
            GROUP BY iface ORDER BY total_bytes DESC
        """, (since_hour,)).fetchall()


# ── connections ────────────────────────────────────────────────────────────────

def upsert_conn_delta(hour_ts, iface, source_ip, protocol, remote_ip, remote_port, tx_delta, rx_delta):
    with _db() as conn:
        conn.execute("""
            INSERT INTO conn_hourly
                (hour_ts, iface, source_ip, protocol, remote_ip, remote_port,
                 tx_bytes, rx_bytes, hit_count)
            VALUES (?,?,?,?,?,?,?,?,1)
            ON CONFLICT(hour_ts, iface, source_ip, protocol, remote_ip, remote_port) DO UPDATE SET
                tx_bytes  = tx_bytes  + excluded.tx_bytes,
                rx_bytes  = rx_bytes  + excluded.rx_bytes,
                hit_count = hit_count + 1
        """, (hour_ts, iface, source_ip, protocol, remote_ip, remote_port, tx_delta, rx_delta))


def query_connections(iface, since_hour, source_ip=None, limit=100):
    with _db() as conn:
        if source_ip:
            return conn.execute("""
                SELECT remote_ip, remote_port, protocol,
                       SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                       SUM(tx_bytes + rx_bytes) AS total_bytes, SUM(hit_count) AS hit_count
                FROM conn_hourly
                WHERE source_ip=? AND hour_ts>=?
                GROUP BY remote_ip, remote_port, protocol
                ORDER BY total_bytes DESC LIMIT ?
            """, (source_ip, since_hour, limit)).fetchall()
        if iface == 'all':
            return conn.execute("""
                SELECT remote_ip, remote_port, protocol,
                       SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                       SUM(tx_bytes + rx_bytes) AS total_bytes, SUM(hit_count) AS hit_count
                FROM conn_hourly WHERE hour_ts>=?
                GROUP BY remote_ip, remote_port, protocol
                ORDER BY total_bytes DESC LIMIT ?
            """, (since_hour, limit)).fetchall()
        return conn.execute("""
            SELECT remote_ip, remote_port, protocol,
                   SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes + rx_bytes) AS total_bytes, SUM(hit_count) AS hit_count
            FROM conn_hourly WHERE iface=? AND hour_ts>=?
            GROUP BY remote_ip, remote_port, protocol
            ORDER BY total_bytes DESC LIMIT ?
        """, (iface, since_hour, limit)).fetchall()


# ── devices ────────────────────────────────────────────────────────────────────

def query_devices(since_hour):
    with _db() as conn:
        return conn.execute("""
            SELECT source_ip,
                   SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes + rx_bytes) AS total_bytes,
                   SUM(hit_count) AS hit_count,
                   MAX(hour_ts) AS last_seen
            FROM conn_hourly
            WHERE hour_ts>=? AND source_ip != ''
            GROUP BY source_ip
            ORDER BY total_bytes DESC
        """, (since_hour,)).fetchall()


def query_device_hourly(source_ip, since_hour):
    """Hourly tx/rx totals for a specific device IP, for charting."""
    with _db() as conn:
        return conn.execute("""
            SELECT hour_ts,
                   SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes) AS rx_bytes
            FROM conn_hourly
            WHERE source_ip=? AND hour_ts>=?
            GROUP BY hour_ts ORDER BY hour_ts
        """, (source_ip, since_hour)).fetchall()


# ── CF tunnel ─────────────────────────────────────────────────────────────────

def upsert_cf_tunnel(hour_ts, service_ip, service_port, protocol, tx_delta, rx_delta):
    with _db() as conn:
        conn.execute("""
            INSERT INTO cf_tunnel_hourly
                (hour_ts, service_ip, service_port, protocol, tx_bytes, rx_bytes, hit_count)
            VALUES (?,?,?,?,?,?,1)
            ON CONFLICT(hour_ts, service_ip, service_port, protocol) DO UPDATE SET
                tx_bytes  = tx_bytes  + excluded.tx_bytes,
                rx_bytes  = rx_bytes  + excluded.rx_bytes,
                hit_count = hit_count + 1
        """, (hour_ts, service_ip, service_port, protocol, tx_delta, rx_delta))


def query_cf_tunnel(since_hour, limit=100):
    with _db() as conn:
        return conn.execute("""
            SELECT service_ip, service_port, protocol,
                   SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes + rx_bytes) AS total_bytes, SUM(hit_count) AS hit_count
            FROM cf_tunnel_hourly WHERE hour_ts>=?
            GROUP BY service_ip, service_port, protocol
            ORDER BY total_bytes DESC LIMIT ?
        """, (since_hour, limit)).fetchall()


def query_cf_tunnel_hourly(since_hour):
    with _db() as conn:
        return conn.execute("""
            SELECT hour_ts,
                   SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes
            FROM cf_tunnel_hourly WHERE hour_ts>=?
            GROUP BY hour_ts ORDER BY hour_ts
        """, (since_hour,)).fetchall()


# ── container bandwidth ────────────────────────────────────────────────────────

def insert_container_bw_raw(samples):
    with _db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO container_bw_raw VALUES (?,?,?,?,?)", samples
        )


def query_container_bw_raw(container_name, since):
    with _db() as conn:
        return conn.execute(
            "SELECT ts, rx_rate, tx_rate FROM container_bw_raw "
            "WHERE container_name=? AND ts>=? ORDER BY ts",
            (container_name, since)
        ).fetchall()


def query_container_bw_hourly(container_name, since):
    with _db() as conn:
        return conn.execute(
            "SELECT hour_ts, SUM(rx_bytes) AS rx_bytes, SUM(tx_bytes) AS tx_bytes "
            "FROM container_bw_hourly "
            "WHERE container_name=? AND hour_ts>=? GROUP BY hour_ts ORDER BY hour_ts",
            (container_name, since)
        ).fetchall()


def purge_container(container_id: str):
    with _db() as conn:
        conn.execute("DELETE FROM container_bw_raw WHERE container_id=?", (container_id,))
        conn.execute("DELETE FROM container_bw_hourly WHERE container_id=?", (container_id,))


def known_containers():
    with _db() as conn:
        rows = conn.execute("""
            SELECT container_id, container_name, MAX(ts) AS last_ts
            FROM container_bw_raw WHERE ts > ?
            GROUP BY container_id
            UNION
            SELECT container_id, container_name, MAX(hour_ts) AS last_ts
            FROM container_bw_hourly
            GROUP BY container_id
            ORDER BY last_ts DESC
        """, (int(time.time()) - 7 * 86400,)).fetchall()
        # Keep only the most recent container ID per name to avoid rebuild duplicates
        seen = set()
        result = []
        for r in rows:
            n = r['container_name']
            if n not in seen:
                seen.add(n)
                result.append({'id': r['container_id'], 'name': n})
        return result


def purge_all_inactive_containers(active_ids: list):
    """Delete history for every container ID not currently running and not the most recent per name."""
    known = known_containers()
    keep = set(active_ids) | {c['id'] for c in known}
    with _db() as conn:
        all_ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT container_id FROM container_bw_raw "
            "UNION SELECT DISTINCT container_id FROM container_bw_hourly"
        ).fetchall()}
        for cid in all_ids - keep:
            conn.execute("DELETE FROM container_bw_raw WHERE container_id=?", (cid,))
            conn.execute("DELETE FROM container_bw_hourly WHERE container_id=?", (cid,))


def query_totals_by_container(since_hour):
    with _db() as conn:
        return conn.execute("""
            SELECT container_name,
                   SUM(rx_bytes) AS rx_bytes, SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes + tx_bytes) AS total_bytes
            FROM container_bw_hourly WHERE hour_ts>=?
            GROUP BY container_name ORDER BY total_bytes DESC
        """, (since_hour,)).fetchall()


# ── device labels ──────────────────────────────────────────────────────────────

def get_all_labels() -> dict:
    with _db() as conn:
        rows = conn.execute("SELECT ip, label FROM device_labels").fetchall()
        return {r['ip']: r['label'] for r in rows}


def set_label(ip: str, label: str):
    with _db() as conn:
        if label:
            conn.execute(
                "INSERT OR REPLACE INTO device_labels VALUES (?,?,?)",
                (ip, label, int(time.time()))
            )
        else:
            conn.execute("DELETE FROM device_labels WHERE ip=?", (ip,))


# ── settings ───────────────────────────────────────────────────────────────────

def get_setting(key: str, default=None):
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key: str, value: str):
    with _db() as conn:
        if value is not None:
            conn.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?,?)",
                (key, value, int(time.time()))
            )
        else:
            conn.execute("DELETE FROM settings WHERE key=?", (key,))


def get_all_settings(keys: list) -> dict:
    with _db() as conn:
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({','.join('?'*len(keys))})",
            keys
        ).fetchall()
        return {r['key']: r['value'] for r in rows}


# ── Starlink WAN ──────────────────────────────────────────────────────────────

def insert_starlink_raw(ts: int, rx_rate: float, tx_rate: float):
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO starlink_bw_raw VALUES (?,?,?)",
                     (ts, rx_rate, tx_rate))


def query_starlink_raw(since: int):
    with _db() as conn:
        return conn.execute(
            "SELECT ts, rx_rate, tx_rate FROM starlink_bw_raw WHERE ts>=? ORDER BY ts",
            (since,)
        ).fetchall()


def query_starlink_hourly(since: int):
    with _db() as conn:
        return conn.execute(
            "SELECT hour_ts, rx_bytes, tx_bytes FROM starlink_bw_hourly WHERE hour_ts>=? ORDER BY hour_ts",
            (since,)
        ).fetchall()


# ── Firewalla devices ─────────────────────────────────────────────────────────

def upsert_fw_device(mac: str, ip: str, name: str, mac_vendor: str, last_active: int,
                     group_name: str = '', fw_rx_bytes: int = 0, fw_tx_bytes: int = 0):
    with _db() as conn:
        conn.execute("""
            INSERT INTO fw_devices
                (mac, ip, name, mac_vendor, group_name, fw_rx_bytes, fw_tx_bytes, last_active, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mac) DO UPDATE SET
                ip=excluded.ip, name=excluded.name,
                mac_vendor=excluded.mac_vendor,
                group_name=excluded.group_name,
                fw_rx_bytes=excluded.fw_rx_bytes,
                fw_tx_bytes=excluded.fw_tx_bytes,
                last_active=excluded.last_active,
                updated_at=excluded.updated_at
        """, (mac, ip, name, mac_vendor, group_name, fw_rx_bytes, fw_tx_bytes, last_active, int(time.time())))


def get_fw_device_by_ip(ip: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT mac, ip, name, mac_vendor, group_name, fw_rx_bytes, fw_tx_bytes, last_active "
            "FROM fw_devices WHERE ip=?", (ip,)
        ).fetchone()
        return dict(row) if row else None


def get_all_fw_devices() -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT mac, ip, name, mac_vendor, group_name, fw_rx_bytes, fw_tx_bytes, last_active "
            "FROM fw_devices ORDER BY group_name, name"
        ).fetchall()
        return [dict(r) for r in rows]


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
                "SELECT ip FROM dns_cache WHERE ip=? AND resolved_at>?", (ip, ttl)
            ).fetchone()
            if not row:
                result.append(ip)
        return result


# ── hourly aggregation ─────────────────────────────────────────────────────────

def aggregate_hourly():
    cutoff   = int(time.time()) - 7 * 86400
    cur_hour = (int(time.time()) // 3600) * 3600
    with _db() as conn:
        conn.execute("""
            INSERT INTO bw_hourly (hour_ts,iface,rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate)
            SELECT (ts/3600)*3600,iface,
                   CAST(SUM(rx_rate*10) AS INTEGER), CAST(SUM(tx_rate*10) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM bw_raw WHERE ts<?
            GROUP BY (ts/3600)*3600,iface
            ON CONFLICT(hour_ts,iface) DO UPDATE SET
                rx_bytes=MAX(bw_hourly.rx_bytes,excluded.rx_bytes),
                tx_bytes=MAX(bw_hourly.tx_bytes,excluded.tx_bytes),
                peak_rx_rate=MAX(bw_hourly.peak_rx_rate,excluded.peak_rx_rate),
                peak_tx_rate=MAX(bw_hourly.peak_tx_rate,excluded.peak_tx_rate)
        """, (cur_hour,))

        conn.execute("""
            INSERT INTO container_bw_hourly
                (hour_ts,container_id,container_name,rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate)
            SELECT (ts/3600)*3600,container_id,container_name,
                   CAST(SUM(rx_rate*10) AS INTEGER), CAST(SUM(tx_rate*10) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM container_bw_raw WHERE ts<?
            GROUP BY (ts/3600)*3600,container_id
            ON CONFLICT(hour_ts,container_id) DO UPDATE SET
                container_name=excluded.container_name,
                rx_bytes=MAX(container_bw_hourly.rx_bytes,excluded.rx_bytes),
                tx_bytes=MAX(container_bw_hourly.tx_bytes,excluded.tx_bytes),
                peak_rx_rate=MAX(container_bw_hourly.peak_rx_rate,excluded.peak_rx_rate),
                peak_tx_rate=MAX(container_bw_hourly.peak_tx_rate,excluded.peak_tx_rate)
        """, (cur_hour,))

        conn.execute("""
            INSERT INTO starlink_bw_hourly (hour_ts,rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate)
            SELECT (ts/3600)*3600,
                   CAST(SUM(rx_rate*30) AS INTEGER), CAST(SUM(tx_rate*30) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM starlink_bw_raw WHERE ts<?
            GROUP BY (ts/3600)*3600
            ON CONFLICT(hour_ts) DO UPDATE SET
                rx_bytes=MAX(starlink_bw_hourly.rx_bytes,excluded.rx_bytes),
                tx_bytes=MAX(starlink_bw_hourly.tx_bytes,excluded.tx_bytes),
                peak_rx_rate=MAX(starlink_bw_hourly.peak_rx_rate,excluded.peak_rx_rate),
                peak_tx_rate=MAX(starlink_bw_hourly.peak_tx_rate,excluded.peak_tx_rate)
        """, (cur_hour,))

        conn.execute("DELETE FROM bw_raw WHERE ts<?", (cutoff,))
        conn.execute("DELETE FROM container_bw_raw WHERE ts<?", (cutoff,))
        conn.execute("DELETE FROM starlink_bw_raw WHERE ts<?", (cutoff,))

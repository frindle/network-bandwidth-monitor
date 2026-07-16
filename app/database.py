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
                ts      INTEGER NOT NULL,
                iface   TEXT    NOT NULL DEFAULT 'eth3',
                rx_rate REAL    NOT NULL,
                tx_rate REAL    NOT NULL,
                PRIMARY KEY (ts, iface)
            );
            CREATE TABLE IF NOT EXISTS starlink_bw_hourly (
                hour_ts      INTEGER NOT NULL,
                iface        TEXT    NOT NULL DEFAULT 'eth3',
                rx_bytes     INTEGER NOT NULL DEFAULT 0,
                tx_bytes     INTEGER NOT NULL DEFAULT 0,
                peak_rx_rate REAL    NOT NULL DEFAULT 0,
                peak_tx_rate REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, iface)
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

            CREATE TABLE IF NOT EXISTS fw_conn_hourly (
                hour_ts     INTEGER NOT NULL,
                source_ip   TEXT    NOT NULL DEFAULT '',
                remote_ip   TEXT    NOT NULL,
                domain      TEXT    NOT NULL DEFAULT '',
                protocol    TEXT    NOT NULL DEFAULT 'tcp',
                remote_port INTEGER NOT NULL DEFAULT 0,
                tx_bytes    INTEGER NOT NULL DEFAULT 0,
                rx_bytes    INTEGER NOT NULL DEFAULT 0,
                hit_count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (hour_ts, source_ip, remote_ip, protocol, remote_port)
            );

            CREATE INDEX IF NOT EXISTS idx_bw_raw_ts       ON bw_raw(ts);
            CREATE INDEX IF NOT EXISTS idx_bw_hourly_ts    ON bw_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_conn_ht_ts      ON conn_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_cf_tunnel_ts    ON cf_tunnel_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_fw_devices_ip   ON fw_devices(ip);
            CREATE INDEX IF NOT EXISTS idx_fw_conn_ts      ON fw_conn_hourly(hour_ts);
            CREATE INDEX IF NOT EXISTS idx_fw_conn_src     ON fw_conn_hourly(source_ip);
            CREATE INDEX IF NOT EXISTS idx_fw_poll_ts      ON fw_poll_log(ts);
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
                    ts      INTEGER NOT NULL,
                    iface   TEXT    NOT NULL DEFAULT 'eth3',
                    rx_rate REAL    NOT NULL,
                    tx_rate REAL    NOT NULL,
                    PRIMARY KEY (ts, iface)
                );
                CREATE TABLE IF NOT EXISTS starlink_bw_hourly (
                    hour_ts      INTEGER NOT NULL,
                    iface        TEXT    NOT NULL DEFAULT 'eth3',
                    rx_bytes     INTEGER NOT NULL DEFAULT 0,
                    tx_bytes     INTEGER NOT NULL DEFAULT 0,
                    peak_rx_rate REAL    NOT NULL DEFAULT 0,
                    peak_tx_rate REAL    NOT NULL DEFAULT 0,
                    PRIMARY KEY (hour_ts, iface)
                );
            """)

        # v0.8 → v0.9: add iface column to starlink tables (composite PK)
        sl_cols = {r[1] for r in conn.execute("PRAGMA table_info(starlink_bw_raw)").fetchall()}
        if 'iface' not in sl_cols:
            conn.executescript("""
                CREATE TABLE starlink_bw_raw_new (
                    ts      INTEGER NOT NULL,
                    iface   TEXT    NOT NULL DEFAULT 'eth3',
                    rx_rate REAL    NOT NULL,
                    tx_rate REAL    NOT NULL,
                    PRIMARY KEY (ts, iface)
                );
                INSERT INTO starlink_bw_raw_new SELECT ts,'eth3',rx_rate,tx_rate FROM starlink_bw_raw;
                DROP TABLE starlink_bw_raw;
                ALTER TABLE starlink_bw_raw_new RENAME TO starlink_bw_raw;

                CREATE TABLE starlink_bw_hourly_new (
                    hour_ts      INTEGER NOT NULL,
                    iface        TEXT    NOT NULL DEFAULT 'eth3',
                    rx_bytes     INTEGER NOT NULL DEFAULT 0,
                    tx_bytes     INTEGER NOT NULL DEFAULT 0,
                    peak_rx_rate REAL    NOT NULL DEFAULT 0,
                    peak_tx_rate REAL    NOT NULL DEFAULT 0,
                    PRIMARY KEY (hour_ts, iface)
                );
                INSERT INTO starlink_bw_hourly_new SELECT hour_ts,'eth3',rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate FROM starlink_bw_hourly;
                DROP TABLE starlink_bw_hourly;
                ALTER TABLE starlink_bw_hourly_new RENAME TO starlink_bw_hourly;
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

        # v0.9 → v0.10: add fw_conn_hourly table
        if 'fw_conn_hourly' not in tables:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fw_conn_hourly (
                    hour_ts     INTEGER NOT NULL,
                    source_ip   TEXT    NOT NULL DEFAULT '',
                    remote_ip   TEXT    NOT NULL,
                    domain      TEXT    NOT NULL DEFAULT '',
                    protocol    TEXT    NOT NULL DEFAULT 'tcp',
                    remote_port INTEGER NOT NULL DEFAULT 0,
                    tx_bytes    INTEGER NOT NULL DEFAULT 0,
                    rx_bytes    INTEGER NOT NULL DEFAULT 0,
                    hit_count   INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (hour_ts, source_ip, remote_ip, protocol, remote_port)
                );
                CREATE INDEX IF NOT EXISTS idx_fw_conn_ts  ON fw_conn_hourly(hour_ts);
                CREATE INDEX IF NOT EXISTS idx_fw_conn_src ON fw_conn_hourly(source_ip);
            """)

        # v0.12 → v0.13: add fw_poll_log table (SSH-per-poll reliability tracking)
        if 'fw_poll_log' not in tables:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fw_poll_log (
                    ts         INTEGER NOT NULL,
                    success    INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT    NOT NULL DEFAULT '',
                    error      TEXT    NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_fw_poll_ts ON fw_poll_log(ts);
            """)

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


def query_totals_by_fw_wan(since_hour):
    """Returns per-WAN-interface totals from starlink_bw_hourly, keyed as fw_wan_<iface>."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT iface,
                   CAST(SUM(rx_bytes) AS INTEGER) AS rx_bytes,
                   CAST(SUM(tx_bytes) AS INTEGER) AS tx_bytes,
                   CAST(SUM(rx_bytes + tx_bytes) AS INTEGER) AS total_bytes
            FROM starlink_bw_hourly WHERE hour_ts >= ?
            GROUP BY iface
        """, (since_hour,)).fetchall()
        return [{'iface': f'fw_wan_{r["iface"]}',
                 'rx_bytes': r['rx_bytes'] or 0,
                 'tx_bytes': r['tx_bytes'] or 0,
                 'total_bytes': r['total_bytes'] or 0}
                for r in rows]


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


def query_connections(iface, since_hour, source_ip=None, limit=200):
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


def query_device_hourly_fw(source_ip, since_hour):
    """Hourly tx/rx from fw_conn_hourly for a specific device IP."""
    with _db() as conn:
        return conn.execute("""
            SELECT hour_ts,
                   SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes) AS rx_bytes
            FROM fw_conn_hourly
            WHERE source_ip=? AND hour_ts>=?
            GROUP BY hour_ts ORDER BY hour_ts
        """, (source_ip, since_hour)).fetchall()


def query_all_devices_hourly(since_hour):
    """Aggregated hourly tx/rx across all LAN devices (from conntrack)."""
    with _db() as conn:
        return conn.execute("""
            SELECT hour_ts,
                   SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes) AS rx_bytes
            FROM conn_hourly
            WHERE hour_ts>=? AND source_ip != ''
            GROUP BY hour_ts ORDER BY hour_ts
        """, (since_hour,)).fetchall()


def query_all_devices_hourly_fw(since_hour):
    """Aggregated hourly tx/rx across all LAN devices (from Firewalla flows)."""
    with _db() as conn:
        return conn.execute("""
            SELECT hour_ts,
                   SUM(tx_bytes) AS tx_bytes,
                   SUM(rx_bytes) AS rx_bytes
            FROM fw_conn_hourly
            WHERE hour_ts>=? AND source_ip != ''
            GROUP BY hour_ts ORDER BY hour_ts
        """, (since_hour,)).fetchall()


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

def insert_starlink_raw(ts: int, iface: str, rx_rate: float, tx_rate: float):
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO starlink_bw_raw VALUES (?,?,?,?)",
                     (ts, iface, rx_rate, tx_rate))


def query_starlink_raw(since: int, iface: str | None = None):
    with _db() as conn:
        if iface:
            return conn.execute(
                "SELECT ts, rx_rate, tx_rate FROM starlink_bw_raw WHERE ts>=? AND iface=? ORDER BY ts",
                (since, iface)
            ).fetchall()
        return conn.execute(
            "SELECT ts, iface, rx_rate, tx_rate FROM starlink_bw_raw WHERE ts>=? ORDER BY ts",
            (since,)
        ).fetchall()


def query_starlink_hourly(since: int, iface: str | None = None):
    with _db() as conn:
        if iface:
            return conn.execute(
                "SELECT hour_ts, rx_bytes, tx_bytes FROM starlink_bw_hourly WHERE hour_ts>=? AND iface=? ORDER BY hour_ts",
                (since, iface)
            ).fetchall()
        return conn.execute(
            "SELECT hour_ts, iface, rx_bytes, tx_bytes FROM starlink_bw_hourly WHERE hour_ts>=? ORDER BY hour_ts",
            (since,)
        ).fetchall()


# ── Firewalla connections ─────────────────────────────────────────────────────

def upsert_fw_conn(hour_ts: int, source_ip: str, remote_ip: str, domain: str,
                   protocol: str, remote_port: int, tx_bytes: int, rx_bytes: int):
    with _db() as conn:
        conn.execute("""
            INSERT INTO fw_conn_hourly
                (hour_ts, source_ip, remote_ip, domain, protocol, remote_port,
                 tx_bytes, rx_bytes, hit_count)
            VALUES (?,?,?,?,?,?,?,?,1)
            ON CONFLICT(hour_ts, source_ip, remote_ip, protocol, remote_port) DO UPDATE SET
                domain=CASE WHEN excluded.domain!='' THEN excluded.domain ELSE fw_conn_hourly.domain END,
                tx_bytes=fw_conn_hourly.tx_bytes+excluded.tx_bytes,
                rx_bytes=fw_conn_hourly.rx_bytes+excluded.rx_bytes,
                hit_count=fw_conn_hourly.hit_count+1
        """, (hour_ts, source_ip, remote_ip, domain, protocol, remote_port,
              tx_bytes, rx_bytes))


def query_fw_connections(since_hour: int, source_ip: str | None = None,
                         limit: int = 500) -> list:
    with _db() as conn:
        if source_ip:
            return conn.execute("""
                SELECT remote_ip, domain, protocol, remote_port,
                       SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                       SUM(tx_bytes+rx_bytes) AS total_bytes, SUM(hit_count) AS hit_count,
                       1 AS source_count, source_ip AS source_ips
                FROM fw_conn_hourly
                WHERE hour_ts>=? AND source_ip=?
                GROUP BY remote_ip, protocol, remote_port
                ORDER BY total_bytes DESC LIMIT ?
            """, (since_hour, source_ip, limit)).fetchall()
        return conn.execute("""
            SELECT remote_ip, domain, protocol, remote_port,
                   SUM(tx_bytes) AS tx_bytes, SUM(rx_bytes) AS rx_bytes,
                   SUM(tx_bytes+rx_bytes) AS total_bytes, SUM(hit_count) AS hit_count,
                   COUNT(DISTINCT source_ip) AS source_count,
                   GROUP_CONCAT(DISTINCT source_ip) AS source_ips
            FROM fw_conn_hourly
            WHERE hour_ts>=?
            GROUP BY remote_ip, protocol, remote_port
            ORDER BY total_bytes DESC LIMIT ?
        """, (since_hour, limit)).fetchall()


def has_fw_connections(since_hour: int) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM fw_conn_hourly WHERE hour_ts>=? LIMIT 1", (since_hour,)
        ).fetchone()
        return row is not None


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


# ── Firewalla poll health (SSH-per-poll reliability) ──────────────────────────
# Raw log only — kept 7 days (pruned in aggregate_hourly), same window as
# bw_raw/starlink_bw_raw. No permanent hourly aggregate: nothing here needs
# history past 7d, so there's nothing to roll up.

def insert_fw_poll(ts: int, success: bool, latency_ms: int, error_type: str = '', error: str = ''):
    with _db() as conn:
        conn.execute(
            "INSERT INTO fw_poll_log (ts, success, latency_ms, error_type, error) VALUES (?,?,?,?,?)",
            (ts, 1 if success else 0, latency_ms, error_type, error)
        )


def query_fw_poll_summary(since: int) -> dict:
    with _db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(success) AS successes,
                   AVG(CASE WHEN success=1 THEN latency_ms END) AS avg_latency_ms,
                   MAX(CASE WHEN success=1 THEN latency_ms END) AS max_latency_ms
            FROM fw_poll_log WHERE ts>=?
        """, (since,)).fetchone()
        errors = conn.execute("""
            SELECT error_type, COUNT(*) AS c FROM fw_poll_log
            WHERE ts>=? AND success=0 GROUP BY error_type ORDER BY c DESC
        """, (since,)).fetchall()
        total     = row['total'] or 0
        successes = row['successes'] or 0
        return {
            'total':          total,
            'successes':      successes,
            'failures':       total - successes,
            'success_rate':   round(successes / total * 100, 1) if total else None,
            'avg_latency_ms': round(row['avg_latency_ms']) if row['avg_latency_ms'] else None,
            'max_latency_ms': row['max_latency_ms'],
            'errors': [{'error_type': e['error_type'] or 'unknown', 'count': e['c']} for e in errors],
        }


def query_fw_poll_recent_failures(since: int, limit: int = 10) -> list:
    with _db() as conn:
        rows = conn.execute("""
            SELECT ts, latency_ms, error_type, error FROM fw_poll_log
            WHERE ts>=? AND success=0 ORDER BY ts DESC LIMIT ?
        """, (since, limit)).fetchall()
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
    # Watermark-based: only aggregate raw rows in [last watermark, current
    # hour). Without the lower bound this re-added the same raw rows to the
    # hourly totals on every run (raw is kept 7 days) — up to ~168× inflation.
    cutoff   = int(time.time()) - 7 * 86400
    cur_hour = (int(time.time()) // 3600) * 3600
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='agg_watermark'").fetchone()
        watermark = int(row['value']) if row else 0

        conn.execute("""
            INSERT INTO bw_hourly (hour_ts,iface,rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate)
            SELECT (ts/3600)*3600,iface,
                   CAST(SUM(rx_rate*10) AS INTEGER), CAST(SUM(tx_rate*10) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM bw_raw WHERE ts>=? AND ts<?
            GROUP BY (ts/3600)*3600,iface
            ON CONFLICT(hour_ts,iface) DO UPDATE SET
                rx_bytes=bw_hourly.rx_bytes+excluded.rx_bytes,
                tx_bytes=bw_hourly.tx_bytes+excluded.tx_bytes,
                peak_rx_rate=MAX(bw_hourly.peak_rx_rate,excluded.peak_rx_rate),
                peak_tx_rate=MAX(bw_hourly.peak_tx_rate,excluded.peak_tx_rate)
        """, (watermark, cur_hour))

        conn.execute("""
            INSERT INTO starlink_bw_hourly (hour_ts,iface,rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate)
            SELECT (ts/3600)*3600, iface,
                   CAST(SUM(rx_rate*30) AS INTEGER), CAST(SUM(tx_rate*30) AS INTEGER),
                   MAX(rx_rate), MAX(tx_rate)
            FROM starlink_bw_raw WHERE ts>=? AND ts<?
            GROUP BY (ts/3600)*3600, iface
            ON CONFLICT(hour_ts,iface) DO UPDATE SET
                rx_bytes=starlink_bw_hourly.rx_bytes+excluded.rx_bytes,
                tx_bytes=starlink_bw_hourly.tx_bytes+excluded.tx_bytes,
                peak_rx_rate=MAX(starlink_bw_hourly.peak_rx_rate,excluded.peak_rx_rate),
                peak_tx_rate=MAX(starlink_bw_hourly.peak_tx_rate,excluded.peak_tx_rate)
        """, (watermark, cur_hour))

        conn.execute(
            "INSERT OR REPLACE INTO settings VALUES ('agg_watermark',?,?)",
            (str(cur_hour), int(time.time())),
        )

        conn.execute("DELETE FROM bw_raw WHERE ts<?", (cutoff,))
        conn.execute("DELETE FROM starlink_bw_raw WHERE ts<?", (cutoff,))
        conn.execute("DELETE FROM fw_poll_log WHERE ts<?", (cutoff,))


def rebuild_hourly_from_raw():
    """Repair pass for the pre-watermark double-counting bug: for every hour
    still covered by raw samples, recompute the hourly rows exactly from raw.
    Hours older than the raw retention window can't be recomputed — they stay
    inflated and should be interpreted with that in mind (or deleted by hand).
    Returns the number of hourly rows rebuilt."""
    rebuilt = 0
    specs = [
        ('bw_raw', 'bw_hourly', 'iface', 10),
        ('starlink_bw_raw', 'starlink_bw_hourly', 'iface', 30),
    ]
    cur_hour = (int(time.time()) // 3600) * 3600
    with _db() as conn:
        for raw, hourly, key_col, interval in specs:
            row = conn.execute(f"SELECT MIN(ts) AS m FROM {raw}").fetchone()
            if not row or row['m'] is None:
                continue
            # Only rebuild FULLY-covered hours: when the first raw sample
            # lands mid-hour, start at the next boundary; a sample exactly on
            # the boundary means that hour is fully covered.
            first_ts = row['m']
            start_hour = first_ts if first_ts % 3600 == 0 else ((first_ts // 3600) + 1) * 3600
            if start_hour >= cur_hour:
                continue
            conn.execute(f"DELETE FROM {hourly} WHERE hour_ts>=? AND hour_ts<?", (start_hour, cur_hour))
            conn.execute(f"""
                INSERT INTO {hourly} (hour_ts,{key_col},rx_bytes,tx_bytes,peak_rx_rate,peak_tx_rate)
                SELECT (ts/3600)*3600,{key_col},
                       CAST(SUM(rx_rate*{interval}) AS INTEGER), CAST(SUM(tx_rate*{interval}) AS INTEGER),
                       MAX(rx_rate), MAX(tx_rate)
                FROM {raw} WHERE ts>=? AND ts<?
                GROUP BY (ts/3600)*3600,{key_col}
            """, (start_hour, cur_hour))
            rebuilt += conn.execute(
                f"SELECT COUNT(*) AS c FROM {hourly} WHERE hour_ts>=? AND hour_ts<?",
                (start_hour, cur_hour),
            ).fetchone()['c']
        # Aggregation resumes from the current hour; everything before it in
        # the raw window was just rebuilt exactly.
        conn.execute(
            "INSERT OR REPLACE INTO settings VALUES ('agg_watermark',?,?)",
            (str(cur_hour), int(time.time())),
        )
    return rebuilt

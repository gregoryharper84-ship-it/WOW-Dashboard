"""
pg_session_ledger.py — PostgreSQL-backed session exposure ledger.

Replaces the process-local ExposureLedger when a session_id is present,
ensuring duplicate-exposure enforcement is consistent across workers and
survives gunicorn restarts.

Schema (created by ensure_table_exists()):
    wow_session_exposure (
        session_id   TEXT,
        exposure_key TEXT,    -- "player:{name}", "game:{game}", "arch:{arch}"
        count        INTEGER,
        expires_at   TIMESTAMPTZ,
        PRIMARY KEY (session_id, exposure_key)
    )

Fail-closed: any DB error blocks the row rather than allowing unchecked
exposure. The gate result includes "db_error" so callers can audit.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# Archetype helper (mirrors exposure_gate.py)
# ---------------------------------------------------------------------------

def _archetype(prop_type: str) -> str:
    """Map a prop label to its stable exposure-limit family."""
    pt = prop_type.lower()
    if "point" in pt:                                  return "scoring"
    if "rebound" in pt:                                return "rebound"
    if "assist" in pt:                                 return "assist"
    if "hit" in pt or "rbi" in pt or "home" in pt:    return "mlb_batting"
    if "strikeout" in pt or "pitch" in pt:             return "mlb_pitching"
    if "shot" in pt or "goal" in pt:                   return "soccer"
    return "other"


# ---------------------------------------------------------------------------
# DDL — call once at startup
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS wow_session_exposure (
    session_id   TEXT        NOT NULL,
    exposure_key TEXT        NOT NULL,
    count        INTEGER     NOT NULL DEFAULT 0,
    expires_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, exposure_key)
);
CREATE INDEX IF NOT EXISTS idx_wse_expires ON wow_session_exposure (expires_at);
"""

# DNS resolution is separate from libpq's connect_timeout on some platforms.
# Bound it before connecting, then pass the resolved address as hostaddr while
# retaining the original host name for TLS verification and diagnostics.
DNS_RESOLUTION_TIMEOUT_SECONDS = 2.0
MAX_DNS_HOSTS = 8
# A resolver call may outlive its request-thread deadline. Permit only one
# daemon resolver in flight per process so repeated warmups cannot leak an
# unbounded number of blocked threads when the system resolver is unhealthy.
_DNS_RESOLUTION_SLOT = threading.BoundedSemaphore(1)


def _resolve_hostaddr(conn_string: str, driver: Any) -> dict[str, str] | None:
    """Build bounded libpq host overrides while preserving ordered failover."""
    try:
        parsed = driver.extensions.parse_dsn(conn_string)
    except Exception:
        raise ValueError("SESSION_LEDGER_DSN_INVALID") from None

    raw_host = parsed.get("host")
    if not raw_host:
        # A hostaddr-only DSN or libpq's default local socket needs no DNS.
        return None
    hosts = [item.strip() for item in str(raw_host).split(",")]
    if not hosts or len(hosts) > MAX_DNS_HOSTS:
        raise ValueError("SESSION_LEDGER_DSN_HOST_COUNT_INVALID")

    raw_port = parsed.get("port")
    port_parts = [item.strip() for item in str(raw_port).split(",")] if raw_port else []
    if not port_parts:
        ports = ["5432"] * len(hosts)
    elif len(port_parts) == 1:
        ports = port_parts * len(hosts)
    elif len(port_parts) == len(hosts):
        ports = port_parts
    else:
        raise ValueError("SESSION_LEDGER_DSN_PORT_INVALID")
    port_numbers: list[int] = []
    for value in ports:
        try:
            number = int(value or 5432)
        except (TypeError, ValueError):
            raise ValueError("SESSION_LEDGER_DSN_PORT_INVALID") from None
        if not 1 <= number <= 65535:
            raise ValueError("SESSION_LEDGER_DSN_PORT_INVALID")
        port_numbers.append(number)

    raw_hostaddr = parsed.get("hostaddr")
    if raw_hostaddr:
        hostaddrs: list[str | None] = [
            item.strip() for item in str(raw_hostaddr).split(",")
        ]
        if len(hostaddrs) != len(hosts):
            raise ValueError("SESSION_LEDGER_DSN_HOSTADDR_INVALID")
    else:
        hostaddrs = [None] * len(hosts)

    def is_local_socket(host: str) -> bool:
        """Recognize libpq Unix-domain and abstract socket host forms."""
        return (
            not host
            or host.startswith(("/", "\\", "@"))
            or (len(host) > 2 and host[1] == ":" and host[2] in ("/", "\\"))
        )

    # Each input host maps to one or more addresses. Repeating its hostname
    # alongside each address lets libpq try every address before advancing
    # to the next input host, matching its native ordered failover behavior.
    resolved_by_index: dict[int, list[str]] = {}
    dns_indices: list[int] = []
    needs_hostaddr = False
    for index, host in enumerate(hosts):
        supplied = hostaddrs[index]
        if supplied:
            continue
        if is_local_socket(host):
            continue
        try:
            address = str(ipaddress.ip_address(host))
        except ValueError:
            dns_indices.append(index)
        else:
            resolved_by_index[index] = [address]
            needs_hostaddr = True

    if raw_hostaddr and all(value for value in hostaddrs):
        # A complete explicit hostaddr list already prevents libpq DNS lookup.
        return None

    if dns_indices:
        needs_hostaddr = True
        deadline = time.monotonic() + DNS_RESOLUTION_TIMEOUT_SECONDS
        slot_wait = max(0.0, deadline - time.monotonic())
        if not _DNS_RESOLUTION_SLOT.acquire(timeout=slot_wait):
            raise TimeoutError("SESSION_LEDGER_DNS_BUSY")

        # Resolve each hostname concurrently under one process-wide single-
        # flight slot and one absolute deadline. A slow/unresolvable host does
        # not prevent a later usable host from being attempted.
        outcomes: dict[int, list[str]] = {}
        outcome_lock = threading.Lock()
        remaining = [len(dns_indices)]
        completed = threading.Event()

        def finish(index: int, addresses: list[str]) -> None:
            """Publish one host result and release the slot after the batch."""
            with outcome_lock:
                outcomes[index] = addresses
                remaining[0] -= 1
                if remaining[0] == 0:
                    _DNS_RESOLUTION_SLOT.release()
                    completed.set()

        def resolve_one(index: int) -> None:
            """Resolve one host without blocking the warmup request thread."""
            try:
                records = socket.getaddrinfo(
                    hosts[index], port_numbers[index], type=socket.SOCK_STREAM
                )
                addresses: list[str] = []
                for record in records:
                    candidate = str(record[4][0])
                    try:
                        candidate = str(ipaddress.ip_address(candidate))
                    except ValueError:
                        continue
                    if candidate not in addresses:
                        addresses.append(candidate)
                # Retain every distinct address, preferring IPv4 as before.
                ipv4 = [
                    item for item in addresses
                    if ipaddress.ip_address(item).version == 4
                ]
                ordered = ipv4 + [item for item in addresses if item not in ipv4]
            except Exception:
                ordered = []
            finish(index, ordered)

        for index in dns_indices:
            try:
                worker = threading.Thread(
                    target=resolve_one,
                    args=(index,),
                    name="pg-session-ledger-dns",
                    daemon=True,
                )
                worker.start()
            except Exception:
                finish(index, [])

        remaining_wait = max(0.0, deadline - time.monotonic())
        completed.wait(remaining_wait)
        with outcome_lock:
            snapshot = dict(outcomes)
            pending = {
                index for index in dns_indices if index not in outcomes
            }
        resolved_by_index.update(snapshot)
    else:
        pending = set()

    selected_hosts: list[str] = []
    selected_hostaddrs: list[str] = []
    selected_ports: list[str] = []
    for index, host in enumerate(hosts):
        supplied = hostaddrs[index]
        addresses = [supplied] if supplied else resolved_by_index.get(index)
        if addresses is None and is_local_socket(host):
            addresses = [""]
        if not addresses:
            # An unresolvable host is omitted; the remaining resolved hosts
            # retain their original relative order and libpq failover order.
            continue
        for address in addresses:
            selected_hosts.append(host)
            selected_hostaddrs.append(address)
            selected_ports.append(ports[index])

    if not selected_hosts:
        if pending:
            raise TimeoutError("SESSION_LEDGER_DNS_TIMEOUT")
        raise OSError("SESSION_LEDGER_DNS_FAILED")
    if not needs_hostaddr:
        return None

    overrides = {"hostaddr": ",".join(selected_hostaddrs)}
    if selected_hosts != hosts:
        overrides["host"] = ",".join(selected_hosts)
    if len(port_parts) > 1 and selected_ports != port_parts:
        overrides["port"] = ",".join(selected_ports)
    return overrides


def ensure_table_exists(conn_string: str | None = None) -> None:
    """Create the wow_session_exposure table if it doesn't exist."""
    import psycopg2
    url = conn_string or os.environ.get("DATABASE_URL", "")
    host_overrides = _resolve_hostaddr(url, psycopg2)
    connect_kwargs: dict[str, Any] = {"connect_timeout": 5}
    if host_overrides is not None:
        connect_kwargs.update(host_overrides)
    # DNS, TCP connection and DDL lock/statement waits have separate bounds.
    with psycopg2.connect(url, **connect_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '3000ms'")
            cur.execute("SET LOCAL statement_timeout = '10000ms'")
            cur.execute(_DDL)
        conn.commit()


# ---------------------------------------------------------------------------
# PgSessionLedger
# ---------------------------------------------------------------------------

SESSION_TTL_HOURS = 4
MAX_PLAYER    = 1
MAX_GAME      = 2
MAX_ARCHETYPE = 3


class PgSessionLedger:
    """
    Drop-in replacement for ExposureLedger when cross-worker persistence
    is required.  The same check_and_register() interface is preserved so
    the pipeline and tests can switch between the two transparently.

    Every check_and_register() call opens its own connection, runs a
    SELECT … FOR UPDATE to lock the relevant keys for this session, checks
    counts, and either blocks the row or increments all three keys atomically.

    Fail-closed: any exception during the DB interaction stamps the row with
    a SESSION_LEDGER_UNAVAILABLE blocker and sets passed=False.
    """

    def __init__(
        self,
        session_id: str,
        conn_string: str | None = None,
        max_player: int    = MAX_PLAYER,
        max_game: int      = MAX_GAME,
        max_archetype: int = MAX_ARCHETYPE,
        ttl_hours: int     = SESSION_TTL_HOURS,
    ) -> None:
        """Bind the ledger to one session and its configured exposure limits."""
        self.session_id    = session_id
        self._conn_string  = conn_string or os.environ.get("DATABASE_URL", "")
        self.max_player    = max_player
        self.max_game      = max_game
        self.max_archetype = max_archetype
        self.ttl_hours     = ttl_hours

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_and_register(self, row: dict[str, Any]) -> dict[str, Any]:
        """
        Atomically check exposure limits and register the row if it passes.

        Stamps row["gates"]["exposure_gate"] and (on block) row["blockers"].
        """
        player    = (row.get("player") or "UNKNOWN").lower()
        game      = (row.get("game")   or "UNKNOWN").lower()
        archetype = _archetype(row.get("prop_type") or "")

        player_key    = f"player:{player}"
        game_key      = f"game:{game}"
        arch_key      = f"arch:{archetype}"
        all_keys      = [player_key, game_key, arch_key]

        try:
            blocks, counts = self._atomic_check_and_register(
                all_keys, player_key, game_key, arch_key
            )
        except Exception as exc:
            # Fail-closed: cannot confirm no duplicate → block
            err_tag = f"SESSION_LEDGER_UNAVAILABLE:{type(exc).__name__}:{str(exc)[:80]}"
            row.setdefault("blockers", []).append(err_tag)
            row.setdefault("gates", {})["exposure_gate"] = {
                "passed":     False,
                "blocks":     [err_tag],
                "registered": False,
                "db_error":   str(exc)[:200],
                "session_id": self.session_id,
                "backend":    "postgres",
            }
            return row

        if blocks:
            row.setdefault("blockers", []).extend(blocks)
            row.setdefault("gates", {})["exposure_gate"] = {
                "passed":     False,
                "blocks":     blocks,
                "registered": False,
                "session_id": self.session_id,
                "backend":    "postgres",
                "counts":     counts,
            }
        else:
            row.setdefault("gates", {})["exposure_gate"] = {
                "passed":     True,
                "blocks":     [],
                "registered": True,
                "session_id": self.session_id,
                "backend":    "postgres",
                "counts":     counts,
            }

        return row

    def snapshot(self) -> dict[str, Any]:
        """Return current ledger state from DB (all non-expired keys for session)."""
        import psycopg2
        try:
            with psycopg2.connect(self._conn_string) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT exposure_key, count FROM wow_session_exposure "
                        "WHERE session_id = %s AND expires_at > NOW()",
                        (self.session_id,),
                    )
                    rows = cur.fetchall()
            return {
                "session_id": self.session_id,
                "backend":    "postgres",
                "keys":       {r[0]: r[1] for r in rows},
            }
        except Exception as exc:
            return {"session_id": self.session_id, "error": str(exc)}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _atomic_check_and_register(
        self,
        all_keys: list[str],
        player_key: str,
        game_key: str,
        arch_key: str,
    ) -> tuple[list[str], dict[str, int]]:
        """
        Opens a transaction, locks all rows for this session with
        SELECT … FOR UPDATE, checks limits, and either blocks or increments.

        Returns (blocks, counts_before_increment).

        NOTE: TTL interval is embedded via f-string (ttl_hours is always an
        integer constant, not user input), which is the only safe way to
        parameterise PostgreSQL INTERVAL literals with psycopg2.
        """
        import psycopg2
        ttl = self.ttl_hours  # integer constant

        with psycopg2.connect(self._conn_string) as conn:
            with conn.cursor() as cur:
                # Ensure rows exist first (initialise count=0 if missing)
                for key in all_keys:
                    cur.execute(
                        f"""
                        INSERT INTO wow_session_exposure
                            (session_id, exposure_key, count, expires_at)
                        VALUES
                            (%s, %s, 0, NOW() + INTERVAL '{ttl} hours')
                        ON CONFLICT (session_id, exposure_key) DO NOTHING
                        """,
                        (self.session_id, key),
                    )

                # Prune expired keys for this session (house-keeping)
                cur.execute(
                    "DELETE FROM wow_session_exposure "
                    "WHERE session_id = %s AND expires_at <= NOW()",
                    (self.session_id,),
                )

                # Lock all relevant keys and read current counts atomically
                cur.execute(
                    "SELECT exposure_key, count "
                    "FROM wow_session_exposure "
                    "WHERE session_id = %s AND exposure_key = ANY(%s) "
                    "FOR UPDATE",
                    (self.session_id, all_keys),
                )
                db_rows = cur.fetchall()
                counts: dict[str, int] = {r[0]: r[1] for r in db_rows}

                player_cnt = counts.get(player_key, 0)
                game_cnt   = counts.get(game_key,   0)
                arch_cnt   = counts.get(arch_key,   0)

                blocks: list[str] = []
                if player_cnt >= self.max_player:
                    blocks.append(
                        f"PLAYER_EXPOSURE:{player_key}:{player_cnt + 1}x"
                    )
                if game_cnt >= self.max_game:
                    blocks.append(
                        f"GAME_EXPOSURE:{game_key}:{game_cnt + 1}x"
                    )
                if arch_cnt >= self.max_archetype:
                    blocks.append(
                        f"ARCHETYPE_EXPOSURE:{arch_key}:{arch_cnt + 1}x"
                    )

                if not blocks:
                    # Increment all three keys atomically within this transaction
                    for key in all_keys:
                        cur.execute(
                            f"""
                            UPDATE wow_session_exposure
                               SET count      = count + 1,
                                   expires_at = NOW() + INTERVAL '{ttl} hours'
                             WHERE session_id   = %s
                               AND exposure_key = %s
                            """,
                            (self.session_id, key),
                        )

                conn.commit()

        return blocks, counts

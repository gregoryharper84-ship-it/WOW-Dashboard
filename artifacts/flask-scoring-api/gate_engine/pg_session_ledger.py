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
# A resolver call may outlive its request-thread deadline. Permit only one
# daemon resolver in flight per process so repeated warmups cannot leak an
# unbounded number of blocked threads when the system resolver is unhealthy.
_DNS_RESOLUTION_SLOT = threading.BoundedSemaphore(1)


def _resolve_hostaddr(conn_string: str, driver: Any) -> str | None:
    """Resolve one DSN hostname under a strict deadline, or fail closed."""
    try:
        parsed = driver.extensions.parse_dsn(conn_string)
    except Exception:
        raise ValueError("SESSION_LEDGER_DSN_INVALID") from None
    host = str(parsed.get("host") or "").strip()
    if not host or parsed.get("hostaddr"):
        return None
    # libpq treats a path-valued host as a Unix-domain socket directory.
    # It is not a DNS name and must be left for libpq to resolve locally.
    if "/" in host or "\\" in host:
        return None
    if "," in host:
        raise ValueError("SESSION_LEDGER_DNS_MULTIHOST_UNSUPPORTED")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        port = int(parsed.get("port") or 5432)
    except (TypeError, ValueError):
        raise ValueError("SESSION_LEDGER_DSN_PORT_INVALID") from None

    # Concurrent warmups wait no longer than the same DNS deadline. This
    # bounds request latency while preventing a second stuck resolver thread.
    if not _DNS_RESOLUTION_SLOT.acquire(timeout=DNS_RESOLUTION_TIMEOUT_SECONDS):
        raise TimeoutError("SESSION_LEDGER_DNS_BUSY")
    outcome: dict[str, Any] = {}
    completed = threading.Event()

    def resolve() -> None:
        """Perform the potentially blocking system resolver off the request thread."""
        try:
            outcome["records"] = socket.getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError:
            outcome["failed"] = True
        finally:
            _DNS_RESOLUTION_SLOT.release()
            completed.set()

    worker = threading.Thread(
        target=resolve, name="pg-session-ledger-dns", daemon=True
    )
    try:
        worker.start()
    except Exception:
        _DNS_RESOLUTION_SLOT.release()
        raise RuntimeError("SESSION_LEDGER_DNS_WORKER_UNAVAILABLE") from None
    if not completed.wait(DNS_RESOLUTION_TIMEOUT_SECONDS):
        # The resolver thread may finish later and release the single-flight
        # slot. Until then, subsequent warmups fail fast instead of spawning
        # another thread that could remain blocked on the same system resolver.
        raise TimeoutError("SESSION_LEDGER_DNS_TIMEOUT")
    records = outcome.get("records") or ()
    addresses: list[str] = []
    for family, _socktype, _proto, _canonname, sockaddr in records:
        candidate = str(sockaddr[0])
        if candidate not in addresses:
            addresses.append(candidate)
    if not addresses:
        raise OSError("SESSION_LEDGER_DNS_FAILED")
    # Prefer IPv4 for local service endpoints; otherwise use the resolver's
    # first address. The original hostname remains in the DSN for TLS checks.
    for candidate in addresses:
        try:
            if ipaddress.ip_address(candidate).version == 4:
                return candidate
        except ValueError:
            continue
    return addresses[0]


def ensure_table_exists(conn_string: str | None = None) -> None:
    """Create the wow_session_exposure table if it doesn't exist."""
    import psycopg2
    url = conn_string or os.environ.get("DATABASE_URL", "")
    hostaddr = _resolve_hostaddr(url, psycopg2)
    connect_kwargs: dict[str, Any] = {"connect_timeout": 5}
    if hostaddr is not None:
        connect_kwargs["hostaddr"] = hostaddr
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

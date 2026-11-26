"""WOW Research Knowledge Base: persistence and native search.

``SupabaseResearchStore`` uses the governed service-role client and the
``wow_nre_*`` tables (migration 20261126_v17_native_research_engine).
``MemoryResearchStore`` has the same semantics for tests and local runs.
Attempts, documents, rights records and findings are append-only.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Sequence

TASKS = "wow_nre_research_tasks"
ATTEMPTS = "wow_nre_research_attempts"
DOCUMENTS = "wow_nre_documents"
RIGHTS = "wow_nre_source_rights"
FINDINGS = "wow_nre_findings"
SEARCH_FILTERS = ("sport", "league", "season", "evidence_type", "rights_status", "source_quality",
                  "research_outcome", "team", "player", "event_id", "market", "published_after",
                  "observed_after", "retrieved_after")


class StoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_duplicate(exc: BaseException) -> bool:
    text = f"{getattr(exc, 'code', '')} {exc}"
    return "23505" in text or "duplicate key" in text.lower()


def _code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and len(code) == 5 and code.isalnum():
        return f"NRE_DB_{code.upper()}"
    return f"NRE_DB_{type(exc).__name__.upper()}"


class SupabaseResearchStore:
    def __init__(self, db: Any):
        self.db = db

    def _rows(self, query: Any) -> list[Mapping[str, Any]]:
        try:
            data = getattr(query.execute(), "data", None)
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_code(exc)) from exc
        if not isinstance(data, list):
            raise StoreError("NRE_DB_READ_INVALID")
        return data

    def _insert(self, table: str, row: Mapping[str, Any]) -> bool:
        try:
            self.db.table(table).insert(dict(row)).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_duplicate(exc):
                return False
            raise StoreError(_code(exc)) from exc

    def load_task(self, task_id: str) -> Mapping[str, Any] | None:
        rows = self._rows(self.db.table(TASKS).select("*").eq("task_id", task_id).limit(1))
        return rows[0] if rows else None

    def create_task(self, row: Mapping[str, Any]) -> bool:
        return self._insert(TASKS, row)

    def update_task(self, task_id: str, row: Mapping[str, Any]) -> None:
        self._rows(self.db.table(TASKS).update(dict(row)).eq("task_id", task_id))

    def claim_task(self, task_id: str, owner: str, ttl_seconds: int) -> bool:
        try:
            data = self.db.rpc("wow_nre_claim_task", {"p_task_id": task_id, "p_owner": owner,
                                                       "p_ttl_seconds": ttl_seconds}).execute().data
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_code(exc)) from exc
        return data is True or data == [True] or (isinstance(data, list) and data and data[0] is True)

    def attempts(self, task_id: str) -> list[Mapping[str, Any]]:
        return self._rows(self.db.table(ATTEMPTS).select("*").eq("task_id", task_id).order("finished_at"))

    def insert_attempt(self, row: Mapping[str, Any]) -> bool:
        return self._insert(ATTEMPTS, row)

    def insert_document(self, row: Mapping[str, Any]) -> bool:
        return self._insert(DOCUMENTS, row)

    def latest_document(self, canonical_url: str) -> Mapping[str, Any] | None:
        rows = self._rows(self.db.table(DOCUMENTS).select("document_id,etag,last_modified,content_sha256")
                          .eq("canonical_url", canonical_url).order("retrieved_at", desc=True).limit(1))
        return rows[0] if rows else None

    def insert_rights(self, row: Mapping[str, Any]) -> bool:
        return self._insert(RIGHTS, row)

    def insert_finding(self, row: Mapping[str, Any]) -> bool:
        return self._insert(FINDINGS, row)

    def latest_rights(self, source_id: str) -> Mapping[str, Any] | None:
        rows = self._rows(self.db.table(RIGHTS).select("*").eq("source_id", source_id)
                          .order("created_at", desc=True).limit(1))
        return rows[0] if rows else None

    def findings(self, *, sport: str, source_type: str) -> list[Mapping[str, Any]]:
        return self._rows(self.db.table(FINDINGS).select("*").eq("sport", sport)
                          .eq("required_source_type", source_type).order("created_at", desc=True).limit(200))

    def strategy_history(self, limit: int = 500) -> list[Mapping[str, Any]]:
        return self._rows(self.db.table(ATTEMPTS).select(
            "task_id,required_source_type,outcome,strategies,started_at,finished_at")
            .order("finished_at", desc=True).limit(limit))

    def search(self, query: str, *, limit: int = 20, **filters: Any) -> list[Mapping[str, Any]]:
        unknown = set(filters) - set(SEARCH_FILTERS)
        if unknown:
            raise StoreError(f"NRE_SEARCH_FILTER_UNKNOWN:{sorted(unknown)}")
        params = {"p_query": query, "p_limit": limit, **{f"p_{k}": v for k, v in filters.items()}}
        try:
            data = self.db.rpc("wow_nre_search", params).execute().data
        except Exception as exc:  # noqa: BLE001
            raise StoreError(_code(exc)) from exc
        return list(data or [])


class MemoryResearchStore:
    """In-process store with the same append-only and lease semantics."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in (TASKS, ATTEMPTS, DOCUMENTS,
                                                                                RIGHTS, FINDINGS)}
        self.now: datetime | None = None
        self._keys = {TASKS: "task_id", ATTEMPTS: "attempt_id", DOCUMENTS: "document_id",
                      RIGHTS: "rights_id", FINDINGS: "finding_id"}

    def _insert(self, table: str, row: Mapping[str, Any]) -> bool:
        key = row[self._keys[table]]
        if key in self.tables[table]:
            return False
        self.tables[table][key] = dict(row)
        return True

    def load_task(self, task_id: str):
        row = self.tables[TASKS].get(task_id)
        return dict(row) if row else None

    def create_task(self, row):
        return self._insert(TASKS, row)

    def update_task(self, task_id, row):
        self.tables[TASKS][task_id].update(dict(row))

    def claim_task(self, task_id, owner, ttl_seconds):
        from datetime import timedelta, timezone
        now = self.now or datetime.now(timezone.utc)
        task = self.tables[TASKS][task_id]
        held = task.get("lease_expires_at")
        if task.get("lease_owner") not in (None, owner) and held and datetime.fromisoformat(held) > now:
            return False
        task["lease_owner"] = owner
        task["lease_expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
        return True

    def attempts(self, task_id):
        return sorted((dict(a) for a in self.tables[ATTEMPTS].values() if a["task_id"] == task_id),
                      key=lambda a: a["finished_at"])

    def insert_attempt(self, row):
        return self._insert(ATTEMPTS, row)

    def insert_document(self, row):
        if row.get("body") is not None and row.get("rights_status") != "STORAGE_PERMITTED":
            raise StoreError("NRE_DB_23514")  # mirrors the table check constraint
        return self._insert(DOCUMENTS, row)

    def latest_document(self, canonical_url):
        rows = [d for d in self.tables[DOCUMENTS].values() if d["canonical_url"] == canonical_url]
        return max(rows, key=lambda d: d["retrieved_at"]) if rows else None

    def insert_rights(self, row):
        return self._insert(RIGHTS, row)

    def insert_finding(self, row):
        return self._insert(FINDINGS, row)

    def latest_rights(self, source_id):
        rows = [r for r in self.tables[RIGHTS].values() if r["source_id"] == source_id]
        return max(rows, key=lambda r: r["assessed_at"]) if rows else None

    def findings(self, *, sport, source_type):
        return [dict(f) for f in self.tables[FINDINGS].values()
                if f["sport"] == sport and f["required_source_type"] == source_type]

    def strategy_history(self, limit=500):
        return sorted((dict(a) for a in self.tables[ATTEMPTS].values()),
                      key=lambda a: a["finished_at"], reverse=True)[:limit]

    def search(self, query: str, *, limit: int = 20, **filters: Any):
        unknown = set(filters) - set(SEARCH_FILTERS)
        if unknown:
            raise StoreError(f"NRE_SEARCH_FILTER_UNKNOWN:{sorted(unknown)}")
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1]
        hits = []
        for doc in self.tables[DOCUMENTS].values():
            text = " ".join(str(doc.get(k) or "") for k in ("title", "excerpt", "body")).lower()
            if not terms or not all(t in text for t in terms):
                continue
            ok = True
            for key, value in filters.items():
                if value is None:
                    continue
                if key in ("team", "player"):
                    ok &= value in (doc.get(key + "s") or [])
                elif key.endswith("_after"):
                    col = key.replace("_after", "_at")
                    ok &= bool(doc.get(col)) and doc[col] >= value
                else:
                    ok &= doc.get(key) == value
            if ok:
                hits.append({k: doc.get(k) for k in ("document_id", "canonical_url", "title", "excerpt",
                                                      "sport", "league", "season", "evidence_type",
                                                      "rights_status", "freshness", "retrieved_at")}
                            | {"rank": sum(text.count(t) for t in terms)})
        return sorted(hits, key=lambda h: -h["rank"])[:limit]

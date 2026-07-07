import { useState, useCallback, useMemo } from "react";
import {
  RefreshCw, AlertTriangle, CheckCircle2, XCircle, Clock,
  Search, Filter, Download, ClipboardList,
} from "lucide-react";

const BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";
const API  = BASE.replace("/final-lock", "/api");

interface LogEntry {
  id: number;
  created_at: string;
  player: string;
  sport: string;
  market: string;
  side: string;
  line: number;
  platform: string;
  terminal_label: string;
  blockers: string[] | string | null;
  source_status: string;
  edge_pct: number | null;
  gate_passed: boolean | null;
  notes: string | null;
  source_grade?: string | null;
  process_grade?: string | null;
}

const LABEL_META: Record<string, { color: string; icon: string }> = {
  FINAL_APPROVED:               { color: "text-emerald-400", icon: "✅" },
  MONEY_QUALIFIED:              { color: "text-green-400",   icon: "💰" },
  MARKET_VERIFIED_HOLD:         { color: "text-cyan-400",    icon: "🔒" },
  MODEL_QUALIFIED_HOLD:         { color: "text-blue-400",    icon: "📊" },
  RESEARCH_INTEREST:            { color: "text-violet-400",  icon: "🔍" },
  NO_PLAY:                      { color: "text-slate-400",   icon: "🚫" },
  REJECT_NO_EDGE:               { color: "text-red-400",     icon: "❌" },
  REJECT_BAD_STRUCTURE:         { color: "text-red-500",     icon: "❌" },
  REJECT_DATA_QUALITY:          { color: "text-orange-400",  icon: "⚠️" },
  SOURCE_CONFLICT:              { color: "text-amber-400",   icon: "⚡" },
  SLATE_PURGE:                  { color: "text-slate-500",   icon: "🗑️" },
  DUPLICATE_EXPOSURE_BLOCK:     { color: "text-orange-500",  icon: "🔁" },
  DATA_UNOBTAINABLE:            { color: "text-slate-500",   icon: "📭" },
  DATA_CONTRACT_FAIL:           { color: "text-red-600",     icon: "💥" },
  REJECT_SHARP_CONFLICT:        { color: "text-red-400",     icon: "❌" },
  REJECT_POWER_CORRELATED:      { color: "text-rose-400",    icon: "🔗" },
};

const SOURCE_STATUS_OPTIONS = [
  "ALL", "RETRIEVED", "RECONSTRUCTED", "PROXY_ONLY",
  "DATA_UNOBTAINABLE", "INPUT_FAILURE", "SOURCE_CONFLICT",
  "NOT_CALLED", "FAILED",
];

const SPORTS_OPTIONS = ["ALL", "NBA", "WNBA", "MLB", "NFL", "NHL", "TENNIS"];

const PROCESS_GRADE_OPTIONS = [
  "ALL", "CLEAN_WIN", "FRAGILE_WIN", "LUCKY_WIN", "BAD_BEAT",
  "GOOD_PROCESS_LOSS", "MODEL_FAILURE", "UNKNOWN",
];

function labelMeta(label: string) {
  return LABEL_META[label] ?? { color: "text-slate-400", icon: "•" };
}

function blockerList(blockers: LogEntry["blockers"]): string[] {
  if (!blockers) return [];
  if (Array.isArray(blockers)) return blockers;
  try { return JSON.parse(blockers); } catch { return [String(blockers)]; }
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleString("en-US", {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function escapeCsv(v: unknown): string {
  const s = v == null ? "" : String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function exportCsv(entries: LogEntry[]) {
  const headers = [
    "id", "created_at", "player", "sport", "market", "side", "line",
    "platform", "terminal_label", "source_status", "source_grade",
    "process_grade", "edge_pct", "gate_passed", "blockers", "notes",
  ];
  const rows = entries.map(e => [
    e.id, e.created_at, e.player, e.sport, e.market, e.side, e.line,
    e.platform, e.terminal_label, e.source_status, e.source_grade ?? "",
    e.process_grade ?? "", e.edge_pct ?? "", e.gate_passed ?? "",
    blockerList(e.blockers).join("|"), e.notes ?? "",
  ].map(escapeCsv).join(","));

  const csv = [headers.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `wow_request_log_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function RequestLogPage() {
  const [logs, setLogs]               = useState<LogEntry[]>([]);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const [search, setSearch]           = useState("");
  const [labelFilter, setLabel]       = useState("ALL");
  const [sportFilter, setSport]       = useState("ALL");
  const [sourceFilter, setSource]     = useState("ALL");
  const [processFilter, setProcess]   = useState("ALL");
  const [hasBlockers, setHasBlockers] = useState<"ALL" | "YES" | "NO">("ALL");
  const [dateFrom, setDateFrom]       = useState("");
  const [dateTo, setDateTo]           = useState("");
  const [expanded, setExpanded]       = useState<number | null>(null);
  const [limit, setLimit]             = useState(50);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ limit: String(limit) });
      if (labelFilter !== "ALL") qs.set("terminal_label", labelFilter);
      if (sportFilter !== "ALL") qs.set("sport", sportFilter);
      const r = await fetch(`${API}/request-log?${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json() as { requests?: LogEntry[]; entries?: LogEntry[]; data?: LogEntry[] };
      setLogs((data.requests ?? data.entries ?? data.data ?? []) as LogEntry[]);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [limit, labelFilter, sportFilter]);

  const filtered = useMemo(() => logs.filter(l => {
    if (search) {
      const q = search.toLowerCase();
      const hit = (
        l.player?.toLowerCase().includes(q) ||
        l.sport?.toLowerCase().includes(q) ||
        l.market?.toLowerCase().includes(q) ||
        l.terminal_label?.toLowerCase().includes(q) ||
        l.source_status?.toLowerCase().includes(q) ||
        blockerList(l.blockers).some(b => b.toLowerCase().includes(q))
      );
      if (!hit) return false;
    }
    if (labelFilter  !== "ALL" && l.terminal_label !== labelFilter) return false;
    if (sportFilter  !== "ALL" && l.sport          !== sportFilter)  return false;
    if (sourceFilter !== "ALL" && l.source_status  !== sourceFilter) return false;
    if (processFilter !== "ALL" && l.process_grade !== processFilter) return false;
    if (hasBlockers === "YES" && blockerList(l.blockers).length === 0) return false;
    if (hasBlockers === "NO"  && blockerList(l.blockers).length  > 0)  return false;
    if (dateFrom && new Date(l.created_at) < new Date(dateFrom)) return false;
    if (dateTo   && new Date(l.created_at) > new Date(dateTo + "T23:59:59")) return false;
    return true;
  }), [logs, search, labelFilter, sportFilter, sourceFilter, processFilter, hasBlockers, dateFrom, dateTo]);

  const labelGroups = useMemo(
    () => Array.from(new Set(logs.map(l => l.terminal_label))).sort(),
    [logs]
  );

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      <div className="max-w-7xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <ClipboardList size={22} className="text-primary" />
              Request Log
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Every terminal bucket decision — full blocker audit trail
            </p>
          </div>
          <div className="flex gap-2">
            {filtered.length > 0 && (
              <button
                onClick={() => exportCsv(filtered)}
                className="flex items-center gap-2 px-3 py-2 bg-card border border-border rounded-lg text-sm font-medium hover:bg-muted/40 transition-colors"
                title="Export visible rows as CSV"
              >
                <Download size={14} />
                Export CSV ({filtered.length})
              </button>
            )}
            <button
              onClick={fetchLogs}
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              {loading ? "Loading…" : "Load Logs"}
            </button>
          </div>
        </div>

        {/* Filters — row 1: search + label + limit */}
        <div className="flex flex-wrap gap-3 mb-3">
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2 flex-1 min-w-48">
            <Search size={14} className="text-muted-foreground shrink-0" />
            <input
              className="bg-transparent text-sm outline-none w-full placeholder:text-muted-foreground"
              placeholder="Search player, market, label, blocker…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <Filter size={14} className="text-muted-foreground" />
            <select
              className="bg-transparent text-sm outline-none"
              value={labelFilter}
              onChange={e => setLabel(e.target.value)}
            >
              <option value="ALL">All Labels</option>
              {labelGroups.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-xs text-muted-foreground">Sport:</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={sportFilter}
              onChange={e => setSport(e.target.value)}
            >
              {SPORTS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-xs text-muted-foreground">Source:</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={sourceFilter}
              onChange={e => setSource(e.target.value)}
            >
              {SOURCE_STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-xs text-muted-foreground">Limit:</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
              {[25, 50, 100, 250].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>

        {/* Filters — row 2: process grade + blockers + date range */}
        <div className="flex flex-wrap gap-3 mb-4">
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-xs text-muted-foreground">Process:</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={processFilter}
              onChange={e => setProcess(e.target.value)}
            >
              {PROCESS_GRADE_OPTIONS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-xs text-muted-foreground">Blockers:</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={hasBlockers}
              onChange={e => setHasBlockers(e.target.value as "ALL" | "YES" | "NO")}
            >
              <option value="ALL">Any</option>
              <option value="YES">Has blockers</option>
              <option value="NO">No blockers</option>
            </select>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-1.5">
            <span className="text-xs text-muted-foreground">From:</span>
            <input
              type="date"
              className="bg-transparent text-sm outline-none text-foreground"
              value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-1.5">
            <span className="text-xs text-muted-foreground">To:</span>
            <input
              type="date"
              className="bg-transparent text-sm outline-none text-foreground"
              value={dateTo}
              onChange={e => setDateTo(e.target.value)}
            />
          </div>
          {(search || labelFilter !== "ALL" || sportFilter !== "ALL" || sourceFilter !== "ALL"
            || processFilter !== "ALL" || hasBlockers !== "ALL" || dateFrom || dateTo) && (
            <button
              className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded border border-border/50"
              onClick={() => {
                setSearch(""); setLabel("ALL"); setSport("ALL"); setSource("ALL");
                setProcess("ALL"); setHasBlockers("ALL"); setDateFrom(""); setDateTo("");
              }}
            >
              Clear filters
            </button>
          )}
        </div>

        {error && (
          <div className="flex items-center gap-2 text-amber-400 bg-amber-400/10 border border-amber-400/30 rounded-lg px-4 py-3 mb-5 text-sm">
            <AlertTriangle size={14} />
            {error} — click "Load Logs" to retry.
          </div>
        )}

        {/* Summary chips */}
        {logs.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-5">
            {labelGroups.map(label => {
              const count = logs.filter(l => l.terminal_label === label).length;
              const m = labelMeta(label);
              return (
                <button
                  key={label}
                  onClick={() => setLabel(l => l === label ? "ALL" : label)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                    labelFilter === label
                      ? "bg-primary/20 border-primary text-primary"
                      : "bg-card border-border text-muted-foreground hover:border-primary/40"
                  }`}
                >
                  {m.icon} {label} ({count})
                </button>
              );
            })}
          </div>
        )}

        {/* Filtered count */}
        {logs.length > 0 && (
          <div className="text-xs text-muted-foreground mb-3">
            Showing {filtered.length} of {logs.length} entries
          </div>
        )}

        {/* Empty state */}
        {!loading && logs.length === 0 && !error && (
          <div className="text-center py-20 text-muted-foreground">
            <Clock size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm">Click "Load Logs" to fetch the request history.</p>
          </div>
        )}

        {/* Log table */}
        {filtered.length > 0 && (
          <div className="space-y-2">
            {filtered.map((entry, i) => {
              const m        = labelMeta(entry.terminal_label);
              const blockers = blockerList(entry.blockers);
              const isExp    = expanded === (entry.id ?? i);
              return (
                <div key={entry.id ?? i} className="bg-card border border-border rounded-lg overflow-hidden">
                  <button
                    className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-muted/30 transition-colors"
                    onClick={() => setExpanded(isExp ? null : (entry.id ?? i))}
                  >
                    <span className={`text-xs font-bold w-44 shrink-0 ${m.color}`}>
                      {m.icon} {entry.terminal_label}
                    </span>
                    <div className="flex-1 min-w-0">
                      <span className="font-medium text-sm">{entry.player || "—"}</span>
                      <span className="text-muted-foreground text-xs mx-2">·</span>
                      <span className="text-muted-foreground text-xs">
                        {entry.sport} · {entry.market} · {entry.side} {entry.line}
                      </span>
                    </div>
                    {entry.edge_pct != null && (
                      <span className={`text-xs font-mono shrink-0 ${entry.edge_pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {entry.edge_pct > 0 ? "+" : ""}{(entry.edge_pct * 100).toFixed(1)}%
                      </span>
                    )}
                    <span className={`text-xs shrink-0 hidden sm:block px-1.5 py-0.5 rounded font-mono ${
                      entry.source_status === "RETRIEVED"    ? "text-emerald-400 bg-emerald-400/10" :
                      entry.source_status === "DATA_UNOBTAINABLE" ? "text-slate-400 bg-slate-400/10" :
                      entry.source_status === "SOURCE_CONFLICT"  ? "text-amber-400 bg-amber-400/10" :
                      "text-muted-foreground"
                    }`}>
                      {entry.source_status}
                    </span>
                    <span className="text-xs text-muted-foreground shrink-0 hidden md:block">
                      {fmtDate(entry.created_at)}
                    </span>
                    {blockers.length > 0 && (
                      <span className="text-xs bg-red-500/20 text-red-400 px-1.5 py-0.5 rounded-full shrink-0">
                        {blockers.length}B
                      </span>
                    )}
                  </button>

                  {isExp && (
                    <div className="border-t border-border px-4 py-3 bg-muted/20 text-xs space-y-3">
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-1.5">
                        <div><span className="text-muted-foreground">Platform</span><br/>{entry.platform || "—"}</div>
                        <div><span className="text-muted-foreground">Source Status</span><br/>
                          <span className={
                            entry.source_status === "RETRIEVED"    ? "text-emerald-400" :
                            entry.source_status === "SOURCE_CONFLICT" ? "text-amber-400" :
                            entry.source_status?.startsWith("DATA_") ? "text-slate-400" : ""
                          }>{entry.source_status || "—"}</span>
                        </div>
                        <div><span className="text-muted-foreground">Source Grade</span><br/>
                          <span className={
                            entry.source_grade === "A" ? "text-emerald-400" :
                            entry.source_grade === "B" ? "text-blue-400" :
                            entry.source_grade === "C" ? "text-amber-400" : "text-muted-foreground"
                          }>{entry.source_grade || "—"}</span>
                        </div>
                        <div><span className="text-muted-foreground">Edge</span><br/>
                          {entry.edge_pct != null ? `${(entry.edge_pct*100).toFixed(2)}%` : "—"}
                        </div>
                        <div><span className="text-muted-foreground">Gate Passed</span><br/>
                          {entry.gate_passed == null ? "—" : entry.gate_passed
                            ? <span className="text-emerald-400">Yes</span>
                            : <span className="text-red-400">No</span>}
                        </div>
                        {entry.process_grade && entry.process_grade !== "UNKNOWN" && (
                          <div><span className="text-muted-foreground">Process Grade</span><br/>
                            <span className={
                              entry.process_grade?.includes("WIN") ? "text-emerald-400" :
                              entry.process_grade?.includes("LOSS") ? "text-red-400" : "text-muted-foreground"
                            }>{entry.process_grade}</span>
                          </div>
                        )}
                      </div>
                      {blockers.length > 0 && (
                        <div>
                          <span className="text-muted-foreground">Blockers:</span>
                          <ul className="mt-1 space-y-0.5">
                            {blockers.map((b, bi) => (
                              <li key={bi} className="flex items-center gap-1.5 text-red-300">
                                <XCircle size={10} className="shrink-0" />
                                {b}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {entry.notes && (
                        <div><span className="text-muted-foreground">Notes:</span> {entry.notes}</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {!loading && logs.length > 0 && filtered.length === 0 && (
          <div className="text-center py-12 text-muted-foreground text-sm">
            No entries match your filters.{" "}
            <button className="underline text-primary/80" onClick={() => {
              setSearch(""); setLabel("ALL"); setSport("ALL"); setSource("ALL");
              setProcess("ALL"); setHasBlockers("ALL"); setDateFrom(""); setDateTo("");
            }}>Clear all</button>
          </div>
        )}
      </div>
    </div>
  );
}

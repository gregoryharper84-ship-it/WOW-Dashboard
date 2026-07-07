import { useState, useCallback } from "react";
import { RefreshCw, AlertTriangle, CheckCircle2, XCircle, Clock, Search, Filter } from "lucide-react";

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
}

const LABEL_META: Record<string, { color: string; icon: string }> = {
  FINAL_APPROVED:          { color: "text-emerald-400", icon: "✅" },
  MONEY_QUALIFIED:         { color: "text-green-400",   icon: "💰" },
  MARKET_VERIFIED_HOLD:    { color: "text-cyan-400",    icon: "🔒" },
  MODEL_QUALIFIED_HOLD:    { color: "text-blue-400",    icon: "📊" },
  RESEARCH_INTEREST:       { color: "text-violet-400",  icon: "🔍" },
  NO_PLAY:                 { color: "text-slate-400",   icon: "🚫" },
  REJECT_NO_EDGE:          { color: "text-red-400",     icon: "❌" },
  REJECT_BAD_STRUCTURE:    { color: "text-red-500",     icon: "❌" },
  REJECT_DATA_QUALITY:     { color: "text-orange-400",  icon: "⚠️" },
  SOURCE_CONFLICT:         { color: "text-amber-400",   icon: "⚡" },
  SLATE_PURGE:             { color: "text-slate-500",   icon: "🗑️" },
  DUPLICATE_EXPOSURE_BLOCK:{ color: "text-orange-500",  icon: "🔁" },
  DATA_UNOBTAINABLE:       { color: "text-slate-500",   icon: "📭" },
};

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

export default function RequestLogPage() {
  const [logs, setLogs]       = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [search, setSearch]   = useState("");
  const [labelFilter, setLabel] = useState("ALL");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [limit, setLimit]     = useState(50);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ limit: String(limit) });
      if (labelFilter !== "ALL") qs.set("terminal_label", labelFilter);
      // Try the Flask request-log endpoint via the proxy
      const r = await fetch(`${API}/request-log?${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json() as { requests?: LogEntry[]; entries?: LogEntry[]; data?: LogEntry[] };
      const rows = data.requests ?? data.entries ?? data.data ?? [];
      setLogs(rows as LogEntry[]);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [limit, labelFilter]);

  const filtered = logs.filter(l => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      l.player?.toLowerCase().includes(q) ||
      l.sport?.toLowerCase().includes(q) ||
      l.market?.toLowerCase().includes(q) ||
      l.terminal_label?.toLowerCase().includes(q)
    );
  });

  const labelGroups = Array.from(new Set(logs.map(l => l.terminal_label))).sort();

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      <div className="max-w-7xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Request Log</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Every terminal bucket decision — full blocker audit trail
            </p>
          </div>
          <button
            onClick={fetchLogs}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            {loading ? "Loading…" : "Load Logs"}
          </button>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-3 mb-5">
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2 flex-1 min-w-48">
            <Search size={14} className="text-muted-foreground shrink-0" />
            <input
              className="bg-transparent text-sm outline-none w-full placeholder:text-muted-foreground"
              placeholder="Search player, market, label…"
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
              {labelGroups.map(l => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-sm text-muted-foreground">Limit:</span>
            <select
              className="bg-transparent text-sm outline-none"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
              {[25, 50, 100, 250].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
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
              const m = labelMeta(entry.terminal_label);
              const blockers = blockerList(entry.blockers);
              const isExpanded = expanded === (entry.id ?? i);
              return (
                <div
                  key={entry.id ?? i}
                  className="bg-card border border-border rounded-lg overflow-hidden"
                >
                  <button
                    className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-muted/30 transition-colors"
                    onClick={() => setExpanded(isExpanded ? null : (entry.id ?? i))}
                  >
                    {/* Label */}
                    <span className={`text-xs font-bold w-44 shrink-0 ${m.color}`}>
                      {m.icon} {entry.terminal_label}
                    </span>

                    {/* Player + market */}
                    <div className="flex-1 min-w-0">
                      <span className="font-medium text-sm">{entry.player || "—"}</span>
                      <span className="text-muted-foreground text-xs mx-2">·</span>
                      <span className="text-muted-foreground text-xs">
                        {entry.sport} {entry.market} {entry.side} {entry.line}
                      </span>
                    </div>

                    {/* Edge */}
                    {entry.edge_pct != null && (
                      <span className={`text-xs font-mono shrink-0 ${entry.edge_pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {entry.edge_pct > 0 ? "+" : ""}{(entry.edge_pct * 100).toFixed(1)}%
                      </span>
                    )}

                    {/* Source */}
                    <span className="text-xs text-muted-foreground shrink-0 hidden sm:block">
                      {entry.source_status}
                    </span>

                    {/* Time */}
                    <span className="text-xs text-muted-foreground shrink-0 hidden md:block">
                      {fmtDate(entry.created_at)}
                    </span>

                    {/* Blocker count */}
                    {blockers.length > 0 && (
                      <span className="text-xs bg-red-500/20 text-red-400 px-1.5 py-0.5 rounded-full shrink-0">
                        {blockers.length} blocker{blockers.length > 1 ? "s" : ""}
                      </span>
                    )}
                  </button>

                  {isExpanded && (
                    <div className="border-t border-border px-4 py-3 bg-muted/20 text-xs space-y-2">
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-1">
                        <div><span className="text-muted-foreground">Platform</span><br/>{entry.platform || "—"}</div>
                        <div><span className="text-muted-foreground">Source Status</span><br/>{entry.source_status || "—"}</div>
                        <div><span className="text-muted-foreground">Edge</span><br/>{entry.edge_pct != null ? `${(entry.edge_pct*100).toFixed(2)}%` : "—"}</div>
                        <div><span className="text-muted-foreground">Gate Passed</span><br/>{entry.gate_passed == null ? "—" : entry.gate_passed ? "Yes" : "No"}</div>
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

        {/* No results after filter */}
        {!loading && logs.length > 0 && filtered.length === 0 && (
          <div className="text-center py-12 text-muted-foreground text-sm">
            No entries match your search / filter.
          </div>
        )}
      </div>
    </div>
  );
}

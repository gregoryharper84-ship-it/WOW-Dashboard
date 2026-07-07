import { useState, useCallback, useMemo } from "react";
import {
  RefreshCw, Trophy, TrendingUp, TrendingDown,
  AlertTriangle, BarChart2, Download,
} from "lucide-react";

const BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";
const API  = BASE.replace("/final-lock", "/api");

interface LeaderRow {
  player: string;
  sport: string;
  market: string;
  total_runs: number;
  final_approved: number;
  money_qualified: number;
  no_play: number;
  reject_no_edge: number;
  reject_bad_structure: number;
  approval_rate: number;
  avg_edge_pct: number | null;
  last_label: string;
  last_run: string;
}

interface SummaryStats {
  total_requests: number;
  final_approved: number;
  money_qualified: number;
  model_qualified_hold: number;
  no_play: number;
  reject_total: number;
  approval_rate: number;
  top_sport: string;
  most_common_blocker: string;
}

const LABEL_COLORS: Record<string, string> = {
  FINAL_APPROVED:       "text-emerald-400",
  MONEY_QUALIFIED:      "text-green-400",
  MARKET_VERIFIED_HOLD: "text-cyan-400",
  MODEL_QUALIFIED_HOLD: "text-blue-400",
  NO_PLAY:              "text-slate-400",
  REJECT_NO_EDGE:       "text-red-400",
};

const SPORTS_OPTIONS = ["ALL", "NBA", "WNBA", "MLB", "NFL", "NHL", "TENNIS"];
const MIN_RUNS_OPTIONS = [1, 3, 5, 10, 25];

const TERMINAL_LABEL_FILTERS = [
  "ALL",
  "FINAL_APPROVED",
  "MONEY_QUALIFIED",
  "MODEL_QUALIFIED_HOLD",
  "NO_PLAY",
  "REJECT_NO_EDGE",
  "REJECT_BAD_STRUCTURE",
  "SOURCE_CONFLICT",
  "DATA_UNOBTAINABLE",
];

function pct(n: number) { return (n * 100).toFixed(1) + "%"; }
function fmtDate(iso: string) {
  try { return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return iso; }
}

function escapeCsv(v: unknown): string {
  const s = v == null ? "" : String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function exportCsv(rows: LeaderRow[]) {
  const headers = [
    "player", "sport", "market", "total_runs", "final_approved",
    "money_qualified", "no_play", "reject_no_edge",
    "approval_rate_pct", "avg_edge_pct", "last_label", "last_run",
  ];
  const csv = [
    headers.join(","),
    ...rows.map(r => [
      r.player, r.sport, r.market, r.total_runs, r.final_approved,
      r.money_qualified, r.no_play, r.reject_no_edge,
      (r.approval_rate * 100).toFixed(2),
      r.avg_edge_pct != null ? (r.avg_edge_pct * 100).toFixed(2) : "",
      r.last_label, r.last_run,
    ].map(escapeCsv).join(",")),
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `wow_leaderboard_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function LeaderboardPage() {
  const [rows, setRows]                   = useState<LeaderRow[]>([]);
  const [summary, setSummary]             = useState<SummaryStats | null>(null);
  const [loading, setLoading]             = useState(false);
  const [error, setError]                 = useState<string | null>(null);
  const [sortKey, setSortKey]             = useState<keyof LeaderRow>("approval_rate");
  const [sortDir, setSortDir]             = useState<"asc" | "desc">("desc");
  const [sport, setSport]                 = useState("ALL");
  const [minRuns, setMinRuns]             = useState(1);
  const [lastLabelFilter, setLastLabel]   = useState("ALL");
  const [approvalBand, setApprovalBand]   = useState<"ALL" | "HIGH" | "MED" | "LOW">("ALL");
  const [edgeFilter, setEdgeFilter]       = useState<"ALL" | "POS" | "NEG">("ALL");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [lbRes, statsRes] = await Promise.all([
        fetch(`${API}/leaderboard`),
        fetch(`${API}/stats`),
      ]);
      if (!lbRes.ok) throw new Error(`Leaderboard HTTP ${lbRes.status}`);
      const lbData = await lbRes.json() as { leaderboard?: LeaderRow[]; data?: LeaderRow[] };
      setRows(lbData.leaderboard ?? lbData.data ?? []);
      if (statsRes.ok) {
        setSummary(await statsRes.json() as SummaryStats);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const sorted = useMemo(() => {
    return [...rows]
      .filter(r => {
        if (sport !== "ALL" && r.sport !== sport) return false;
        if (r.total_runs < minRuns) return false;
        if (lastLabelFilter !== "ALL" && r.last_label !== lastLabelFilter) return false;
        if (approvalBand === "HIGH" && r.approval_rate < 0.5)   return false;
        if (approvalBand === "MED"  && (r.approval_rate < 0.2 || r.approval_rate >= 0.5)) return false;
        if (approvalBand === "LOW"  && r.approval_rate >= 0.2)   return false;
        if (edgeFilter === "POS" && (r.avg_edge_pct == null || r.avg_edge_pct <= 0)) return false;
        if (edgeFilter === "NEG" && (r.avg_edge_pct == null || r.avg_edge_pct  > 0)) return false;
        return true;
      })
      .sort((a, b) => {
        const av = a[sortKey];
        const bv = b[sortKey];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        const diff = typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv));
        return sortDir === "desc" ? -diff : diff;
      });
  }, [rows, sport, minRuns, lastLabelFilter, approvalBand, edgeFilter, sortKey, sortDir]);

  function toggleSort(key: keyof LeaderRow) {
    if (sortKey === key) setSortDir(d => d === "desc" ? "asc" : "desc");
    else { setSortKey(key); setSortDir("desc"); }
  }

  function ColHeader({ k, label }: { k: keyof LeaderRow; label: string }) {
    const active = sortKey === k;
    return (
      <th
        className="text-left px-3 py-2 text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground select-none whitespace-nowrap"
        onClick={() => toggleSort(k)}
      >
        {label}{active ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
      </th>
    );
  }

  const anyFilter = sport !== "ALL" || minRuns > 1 || lastLabelFilter !== "ALL" ||
    approvalBand !== "ALL" || edgeFilter !== "ALL";

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      <div className="max-w-7xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Trophy size={22} className="text-amber-400" />
              Leaderboard
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Player/market approval rates and terminal bucket history
            </p>
          </div>
          <div className="flex gap-2">
            {sorted.length > 0 && (
              <button
                onClick={() => exportCsv(sorted)}
                className="flex items-center gap-2 px-3 py-2 bg-card border border-border rounded-lg text-sm font-medium hover:bg-muted/40 transition-colors"
                title="Export visible rows as CSV"
              >
                <Download size={14} />
                Export CSV ({sorted.length})
              </button>
            )}
            <button
              onClick={fetchData}
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              {loading ? "Loading…" : "Load Data"}
            </button>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 text-amber-400 bg-amber-400/10 border border-amber-400/30 rounded-lg px-4 py-3 mb-5 text-sm">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        {/* Summary stats */}
        {summary && (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
            {[
              { label: "Total Runs",     value: summary.total_requests,       color: "text-foreground" },
              { label: "Final Approved", value: summary.final_approved,       color: "text-emerald-400" },
              { label: "Money Qual.",    value: summary.money_qualified,       color: "text-green-400" },
              { label: "Model Hold",     value: summary.model_qualified_hold,  color: "text-blue-400" },
              { label: "No Play",        value: summary.no_play,               color: "text-slate-400" },
              { label: "Rejects",        value: summary.reject_total,          color: "text-red-400" },
              { label: "Approval Rate",  value: pct(summary.approval_rate),    color: "text-primary" },
            ].map(s => (
              <div key={s.label} className="bg-card border border-border rounded-lg px-3 py-2.5 text-center">
                <div className={`text-lg font-bold ${s.color}`}>{s.value}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
        )}

        {/* Filters */}
        {rows.length > 0 && (
          <div className="flex gap-2 flex-wrap mb-4">
            {/* Sport filter pills */}
            {SPORTS_OPTIONS.filter(s => s === "ALL" || rows.some(r => r.sport === s)).map(s => (
              <button
                key={s}
                onClick={() => setSport(s)}
                className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                  sport === s
                    ? "bg-primary/20 border-primary text-primary"
                    : "bg-card border-border text-muted-foreground hover:border-primary/40"
                }`}
              >
                {s}
              </button>
            ))}

            <div className="h-4 w-px bg-border self-center mx-1" />

            {/* Min runs */}
            <div className="flex items-center gap-1.5 bg-card border border-border rounded-full px-3 py-1">
              <span className="text-xs text-muted-foreground">Min runs:</span>
              <select
                className="bg-transparent text-xs outline-none"
                value={minRuns}
                onChange={e => setMinRuns(Number(e.target.value))}
              >
                {MIN_RUNS_OPTIONS.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>

            {/* Last label filter */}
            <div className="flex items-center gap-1.5 bg-card border border-border rounded-full px-3 py-1">
              <span className="text-xs text-muted-foreground">Last:</span>
              <select
                className="bg-transparent text-xs outline-none"
                value={lastLabelFilter}
                onChange={e => setLastLabel(e.target.value)}
              >
                {TERMINAL_LABEL_FILTERS.map(l => <option key={l} value={l}>{l === "ALL" ? "Any label" : l}</option>)}
              </select>
            </div>

            {/* Approval band */}
            <div className="flex items-center gap-1.5 bg-card border border-border rounded-full px-3 py-1">
              <span className="text-xs text-muted-foreground">Rate:</span>
              <select
                className="bg-transparent text-xs outline-none"
                value={approvalBand}
                onChange={e => setApprovalBand(e.target.value as "ALL" | "HIGH" | "MED" | "LOW")}
              >
                <option value="ALL">All</option>
                <option value="HIGH">≥50%</option>
                <option value="MED">20–49%</option>
                <option value="LOW">&lt;20%</option>
              </select>
            </div>

            {/* Edge filter */}
            <div className="flex items-center gap-1.5 bg-card border border-border rounded-full px-3 py-1">
              <span className="text-xs text-muted-foreground">Edge:</span>
              <select
                className="bg-transparent text-xs outline-none"
                value={edgeFilter}
                onChange={e => setEdgeFilter(e.target.value as "ALL" | "POS" | "NEG")}
              >
                <option value="ALL">All</option>
                <option value="POS">Positive</option>
                <option value="NEG">Negative</option>
              </select>
            </div>

            {anyFilter && (
              <button
                className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded border border-border/50"
                onClick={() => {
                  setSport("ALL"); setMinRuns(1); setLastLabel("ALL");
                  setApprovalBand("ALL"); setEdgeFilter("ALL");
                }}
              >
                Clear
              </button>
            )}

            <span className="text-xs text-muted-foreground self-center ml-auto">
              {sorted.length} of {rows.length} rows
            </span>
          </div>
        )}

        {/* Empty state */}
        {!loading && rows.length === 0 && !error && (
          <div className="text-center py-20 text-muted-foreground">
            <BarChart2 size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm">Click "Load Data" to see leaderboard.</p>
          </div>
        )}

        {/* Table */}
        {sorted.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-card border-b border-border">
                <tr>
                  <ColHeader k="player"          label="Player" />
                  <ColHeader k="sport"           label="Sport" />
                  <ColHeader k="market"          label="Market" />
                  <ColHeader k="total_runs"      label="Runs" />
                  <ColHeader k="final_approved"  label="Approved" />
                  <ColHeader k="money_qualified" label="Money Q." />
                  <ColHeader k="no_play"         label="No Play" />
                  <ColHeader k="reject_no_edge"  label="Rejected" />
                  <ColHeader k="approval_rate"   label="Approval %" />
                  <ColHeader k="avg_edge_pct"    label="Avg Edge" />
                  <ColHeader k="last_label"      label="Last Label" />
                  <ColHeader k="last_run"        label="Last Run" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row, i) => {
                  const labelColor = LABEL_COLORS[row.last_label] ?? "text-slate-400";
                  const approvalHigh = row.approval_rate >= 0.5;
                  return (
                    <tr key={i} className="border-b border-border/50 hover:bg-muted/20 transition-colors">
                      <td className="px-3 py-2.5 font-medium">{row.player}</td>
                      <td className="px-3 py-2.5 text-muted-foreground">{row.sport}</td>
                      <td className="px-3 py-2.5 text-muted-foreground">{row.market}</td>
                      <td className="px-3 py-2.5 text-center">{row.total_runs}</td>
                      <td className="px-3 py-2.5 text-center text-emerald-400">{row.final_approved}</td>
                      <td className="px-3 py-2.5 text-center text-green-400">{row.money_qualified}</td>
                      <td className="px-3 py-2.5 text-center text-slate-400">{row.no_play}</td>
                      <td className="px-3 py-2.5 text-center text-red-400">{row.reject_no_edge}</td>
                      <td className="px-3 py-2.5 text-center">
                        <div className="flex items-center justify-center gap-1">
                          {approvalHigh
                            ? <TrendingUp size={11} className="text-emerald-400" />
                            : <TrendingDown size={11} className="text-red-400" />}
                          <span className={approvalHigh ? "text-emerald-400" : "text-red-400"}>
                            {pct(row.approval_rate)}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-center font-mono text-xs">
                        {row.avg_edge_pct != null
                          ? <span className={row.avg_edge_pct > 0 ? "text-emerald-400" : "text-red-400"}>
                              {row.avg_edge_pct > 0 ? "+" : ""}{(row.avg_edge_pct * 100).toFixed(1)}%
                            </span>
                          : <span className="text-muted-foreground">—</span>
                        }
                      </td>
                      <td className={`px-3 py-2.5 text-xs font-medium ${labelColor}`}>{row.last_label}</td>
                      <td className="px-3 py-2.5 text-xs text-muted-foreground">{fmtDate(row.last_run)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Summary footer */}
        {summary && (
          <div className="mt-4 text-xs text-muted-foreground flex flex-wrap gap-4">
            {summary.top_sport && <span>Top sport: <strong>{summary.top_sport}</strong></span>}
            {summary.most_common_blocker && (
              <span>Most common blocker: <strong>{summary.most_common_blocker}</strong></span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { TrendingUp, TrendingDown, Activity, RefreshCw, LayoutGrid, List } from "lucide-react";
import { cn } from "@/lib/utils";

interface LeaderboardEntry {
  rank: number;
  player: string;
  sport: string;
  prop: string;
  side: "MORE" | "LESS";
  record_count: number;
  average_score: number;
  max_score: number;
  min_score: number;
  latest_score: number;
  latest_line: number | null;
  latest_timestamp: string;
  scores: number[];
}

interface LeaderboardResponse {
  window: string;
  limit: number;
  leaderboard: LeaderboardEntry[];
}

const SPORTS = ["All", "NBA", "NFL", "MLB", "NHL", "NCAAF", "NCAAB", "Soccer"];
const WINDOWS = ["L5", "L10"] as const;

function isMORE(side: string) {
  return ["MORE", "more", "over", "OVER"].includes(side);
}

type Status = "APPROVED" | "CONDITIONAL" | "WATCH" | "REJECT";

function getStatus(score: number): Status {
  if (score >= 80) return "APPROVED";
  if (score >= 65) return "CONDITIONAL";
  if (score >= 50) return "WATCH";
  return "REJECT";
}

function statusStyle(s: Status) {
  switch (s) {
    case "APPROVED":    return { bg: "bg-emerald-500/15", text: "text-emerald-400", bar: "bg-emerald-500", ring: "ring-emerald-500/30" };
    case "CONDITIONAL": return { bg: "bg-amber-500/15",   text: "text-amber-400",   bar: "bg-amber-500",   ring: "ring-amber-500/30" };
    case "WATCH":       return { bg: "bg-sky-500/15",      text: "text-sky-400",     bar: "bg-sky-500",     ring: "ring-sky-500/30" };
    case "REJECT":      return { bg: "bg-rose-500/15",     text: "text-rose-400",    bar: "bg-rose-500",    ring: "ring-rose-500/30" };
  }
}

function scoreNumColor(score: number) {
  if (score >= 80) return "text-emerald-400";
  if (score >= 65) return "text-violet-400";
  if (score >= 50) return "text-amber-400";
  return "text-rose-400";
}

function getEdge(score: number): string {
  const edge = (score - 50) / 5;
  return (edge >= 0 ? "+" : "") + edge.toFixed(1) + "%";
}

function getMedian(scores: number[]): number {
  if (!scores.length) return 0;
  const sorted = [...scores].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function getKelly(score: number): string {
  const p = score / 100;
  const kelly = Math.max(0, (p * 2 - 1) * 0.25);
  return kelly.toFixed(1) + "u";
}

function getWindowHits(scores: number[], n: number): { hits: number; total: number; pct: number } {
  const window = scores.slice(-n);
  const hits = window.filter((s) => s >= 65).length;
  return { hits, total: window.length, pct: window.length ? Math.round((hits / window.length) * 100) : 0 };
}

function getPropBadge(prop: string): string {
  const map: Record<string, string> = {
    // NBA
    "rebounds": "REB", "assists": "AST", "points": "PTS",
    "threes": "3PTM", "steals": "STL", "blocks": "BLK",
    "turnovers": "TO", "fantasy points": "FPTS",
    // MLB
    "pitcher strikeouts": "K", "hitter fantasy points": "HFPTS",
    "pitcher fantasy points": "PFPTS", "pitching outs": "OUTS",
    "plate appearances": "PA", "hits": "H", "home runs": "HR",
    "rbis": "RBI", "stolen bases": "SB", "walks": "BB",
    // NFL
    "passing yards": "PASS", "rushing yards": "RUSH",
    "receiving yards": "REC YDS", "touchdowns": "TD",
    "receptions": "REC", "completions": "COMP",
    "interceptions": "INT",
    // NHL
    "goals": "G", "saves": "SV", "shots on goal": "SOG",
    "power play points": "PPP",
  };
  return map[prop.toLowerCase()] ?? prop.toUpperCase().slice(0, 5);
}

function getPropBadgeColor(prop: string): string {
  const p = prop.toLowerCase();
  if (["rebounds", "reb", "pitcher strikeouts", "k", "blocks"].some(k => p.includes(k.toLowerCase())))
    return "bg-sky-500/20 text-sky-400 ring-sky-500/30";
  if (["assists", "ast", "home runs", "hr", "touchdowns", "td"].some(k => p.includes(k.toLowerCase())))
    return "bg-orange-500/20 text-orange-400 ring-orange-500/30";
  if (["points", "pts", "passing yards", "fantasy"].some(k => p.includes(k.toLowerCase())))
    return "bg-violet-500/20 text-violet-400 ring-violet-500/30";
  if (["threes", "3pt", "stolen bases", "sb", "steals"].some(k => p.includes(k.toLowerCase())))
    return "bg-teal-500/20 text-teal-400 ring-teal-500/30";
  if (["hits", "h ", "saves", "sv", "receptions"].some(k => p.includes(k.toLowerCase())))
    return "bg-emerald-500/20 text-emerald-400 ring-emerald-500/30";
  return "bg-amber-500/20 text-amber-400 ring-amber-500/30";
}

function initials(name: string) {
  return name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2);
}

function avatarGradient(name: string) {
  const g = [
    "from-violet-600 to-indigo-700",
    "from-emerald-600 to-teal-700",
    "from-rose-600 to-pink-700",
    "from-amber-500 to-orange-600",
    "from-sky-500 to-blue-700",
    "from-fuchsia-600 to-purple-700",
    "from-red-600 to-rose-700",
    "from-cyan-500 to-sky-600",
  ];
  return g[name.charCodeAt(0) % g.length];
}

function SimplePickCard({ entry, index }: { entry: LeaderboardEntry; index: number }) {
  const isMore = isMORE(entry.side);
  const scores = entry.scores || [];
  const status = getStatus(entry.average_score);
  const ss = statusStyle(status);

  return (
    <motion.div
      data-testid={`card-pick-${entry.rank}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.04, ease: "easeOut" }}
      className="bg-card border border-card-border rounded-xl overflow-hidden flex flex-col hover:border-primary/30 transition-colors duration-200"
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-3">
        <div className={cn("w-12 h-12 rounded-xl bg-gradient-to-br flex items-center justify-center text-white font-black text-sm shrink-0", avatarGradient(entry.player))}>
          {initials(entry.player)}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-black text-foreground text-sm leading-tight truncate">{entry.player}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5 uppercase tracking-wide font-medium truncate">
            {entry.sport} · {entry.prop}
          </p>
        </div>
        <div className="text-right shrink-0">
          <p className={cn("text-3xl font-black tabular-nums leading-none", scoreNumColor(entry.average_score))}>
            {entry.average_score.toFixed(0)}
          </p>
          <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold mt-1", isMore ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/25" : "bg-rose-500/15 text-rose-400 ring-1 ring-rose-500/25")}>
            {isMore ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
            {entry.side}
          </span>
        </div>
      </div>

      {/* Status + line */}
      <div className="flex items-center justify-between px-4 pb-3 gap-2">
        <span className={cn("px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wide ring-1", ss.bg, ss.text, ss.ring)}>
          {status}
        </span>
        <p className="text-[11px] text-muted-foreground font-medium text-right truncate">
          {entry.latest_line != null ? entry.latest_line.toFixed(1) : "—"} {entry.prop}
        </p>
      </div>

      <div className="h-px bg-border/60 mx-4" />

      {/* Stats */}
      <div className="grid grid-cols-4 px-4 py-3 gap-2">
        {[
          { label: "AVG", value: entry.average_score.toFixed(0) + "%", cls: scoreNumColor(entry.average_score) },
          { label: "BEST", value: entry.max_score.toFixed(0) + "%", cls: scoreNumColor(entry.max_score) },
          { label: "LATEST", value: entry.latest_score.toFixed(0) + "%", cls: scoreNumColor(entry.latest_score) },
          { label: "PICKS", value: String(entry.record_count), cls: "text-foreground" },
        ].map(({ label, value, cls }) => (
          <div key={label} className="text-center">
            <p className="text-[9px] text-muted-foreground font-semibold uppercase tracking-wide mb-1">{label}</p>
            <p className={cn("text-xs font-black tabular-nums", cls)}>{value}</p>
          </div>
        ))}
      </div>

      {/* Mini bar chart */}
      {scores.length > 0 && (
        <div className="flex items-end gap-0.5 px-4 pb-3 h-9">
          {scores.slice(-10).map((s, i, arr) => (
            <div
              key={i}
              className="flex-1 rounded-sm"
              style={{
                height: `${Math.max(12, s)}%`,
                backgroundColor: s >= 80 ? "#34d399" : s >= 65 ? "#a78bfa" : s >= 50 ? "#fbbf24" : "#f87171",
                opacity: 0.45 + (i / arr.length) * 0.55,
              }}
            />
          ))}
        </div>
      )}

      {/* Colored bottom bar */}
      <div className="mt-auto h-1.5 bg-muted/20">
        <motion.div
          className={cn("h-full", ss.bar)}
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(entry.average_score, 100)}%` }}
          transition={{ duration: 0.8, delay: index * 0.04 + 0.2, ease: "easeOut" }}
        />
      </div>
    </motion.div>
  );
}

function DetailedPickCard({ entry, index }: { entry: LeaderboardEntry; index: number }) {
  const isMore = isMORE(entry.side);
  const scores = entry.scores || [];
  const status = getStatus(entry.average_score);
  const ss = statusStyle(status);
  const edge = getEdge(entry.average_score);
  const median = getMedian(scores);
  const kelly = getKelly(entry.average_score);
  const l5 = getWindowHits(scores, 5);
  const l10 = getWindowHits(scores, 10);
  const edgePositive = entry.average_score >= 50;
  const badge = getPropBadge(entry.prop);
  const badgeColor = getPropBadgeColor(entry.prop);

  return (
    <motion.div
      data-testid={`card-pick-${entry.rank}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.04, ease: "easeOut" }}
      className="bg-card border border-card-border rounded-xl overflow-hidden flex flex-col hover:border-primary/30 transition-colors duration-200"
    >
      {/* ── Header ── */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-3">
        <div
          className={cn(
            "w-11 h-11 rounded-xl bg-gradient-to-br flex items-center justify-center text-white font-black text-sm shrink-0",
            avatarGradient(entry.player)
          )}
        >
          {initials(entry.player)}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-black text-foreground text-sm leading-tight truncate">
            {entry.player}
          </p>
          <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
            {entry.sport}
          </p>
        </div>
        {/* Prop badge + line */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          <div className="flex items-center gap-2">
            <span className={cn("px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wide ring-1", badgeColor)}>
              {badge}
            </span>
            <p className={cn("text-3xl font-black tabular-nums leading-none", scoreNumColor(entry.average_score))}>
              {entry.average_score.toFixed(0)}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            {entry.latest_line != null && (
              <span className="text-[11px] text-muted-foreground font-semibold tabular-nums">
                {entry.latest_line.toFixed(1)}
              </span>
            )}
            <span
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold",
                isMore
                  ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/25"
                  : "bg-rose-500/15 text-rose-400 ring-1 ring-rose-500/25"
              )}
            >
              {isMore ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
              {entry.side}
            </span>
          </div>
        </div>
      </div>

      {/* Status badge + prop line */}
      <div className="flex items-center justify-between px-4 pb-3 gap-2">
        <span
          className={cn(
            "px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wide ring-1",
            ss.bg, ss.text, ss.ring
          )}
        >
          {status}
        </span>
        <p className="text-[11px] text-muted-foreground font-medium text-right truncate">
          {entry.latest_line != null ? entry.latest_line.toFixed(1) : "—"} {entry.prop}
        </p>
      </div>

      <div className="h-px bg-border/60 mx-4" />

      {/* ── Stats grid ── */}
      <div className="grid grid-cols-5 px-4 py-3 gap-1">
        {[
          {
            label: "True Hit Rate",
            value: entry.average_score.toFixed(0) + "%",
            cls: scoreNumColor(entry.average_score),
          },
          {
            label: "Edge",
            value: edge,
            cls: edgePositive ? "text-emerald-400" : "text-rose-400",
          },
          {
            label: "Median",
            value: median.toFixed(1),
            cls: "text-foreground",
          },
          {
            label: "Market Line",
            value: entry.latest_line != null ? entry.latest_line.toFixed(1) : "—",
            cls: "text-foreground",
          },
          {
            label: "Kelly",
            value: kelly,
            cls: "text-violet-400",
          },
        ].map(({ label, value, cls }) => (
          <div key={label} className="text-center">
            <p className="text-[9px] text-muted-foreground font-semibold uppercase tracking-wide leading-tight mb-1">
              {label}
            </p>
            <p className={cn("text-xs font-black tabular-nums", cls)}>{value}</p>
          </div>
        ))}
      </div>

      <div className="h-px bg-border/60 mx-4" />

      {/* ── Window stats (L5 / L10) ── */}
      <div className="grid grid-cols-2 divide-x divide-border/60 px-0">
        {[
          { label: "L5", data: l5 },
          { label: "L10", data: l10 },
        ].map(({ label, data }) => (
          <div key={label} className="px-4 py-3 text-center">
            <p className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wide mb-1">
              {label}
            </p>
            {data.total > 0 ? (
              <>
                <p className="text-xs font-black text-foreground tabular-nums">
                  {data.hits}/{data.total}
                  <span className="text-muted-foreground font-medium ml-1">({data.pct}%)</span>
                </p>
                <div className="mt-1.5 flex gap-0.5 justify-center">
                  {Array.from({ length: data.total }).map((_, i) => (
                    <div
                      key={i}
                      className={cn(
                        "w-2 h-2 rounded-sm",
                        i < data.hits ? ss.bar : "bg-muted/50"
                      )}
                    />
                  ))}
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">—</p>
            )}
          </div>
        ))}
      </div>

      <div className="h-px bg-border/60 mx-4" />

      {/* ── Bottom details ── */}
      <div className="grid grid-cols-3 px-4 py-3 gap-1">
        {[
          { label: "Role / Type", top: entry.prop, sub: "Prop" },
          { label: "Matchup", top: entry.sport, sub: "League" },
          {
            label: "Market vs PP",
            top: entry.latest_line != null ? entry.latest_line.toFixed(1) : "—",
            sub: "Line",
          },
        ].map(({ label, top, sub }) => (
          <div key={label} className="text-center">
            <p className="text-[9px] text-muted-foreground font-semibold uppercase tracking-wide mb-0.5">
              {label}
            </p>
            <p className="text-[11px] font-bold text-foreground truncate">{top}</p>
            <p className="text-[10px] text-muted-foreground">{sub}</p>
          </div>
        ))}
      </div>

      {/* ── Colored bottom bar ── */}
      <div className="mt-auto h-1.5 bg-muted/20">
        <motion.div
          className={cn("h-full", ss.bar)}
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(entry.average_score, 100)}%` }}
          transition={{ duration: 0.8, delay: index * 0.04 + 0.2, ease: "easeOut" }}
        />
      </div>
    </motion.div>
  );
}

function PickCard({ entry, index }: { entry: LeaderboardEntry; index: number }) {
  if (entry.sport === "MLB" || entry.sport === "NBA") return <DetailedPickCard entry={entry} index={index} />;
  return <SimplePickCard entry={entry} index={index} />;
}

export default function Lobby() {
  const [window, setWindow] = useState<"L5" | "L10">("L10");
  const [sport, setSport] = useState("All");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [todayOnly, setTodayOnly] = useState(true);

  const params = new URLSearchParams({ window, limit: "50" });
  if (sport !== "All") params.set("sport", sport);
  if (todayOnly) params.set("today", "1");

  const { data, isLoading, isError, refetch, isFetching } = useQuery<LeaderboardResponse>({
    queryKey: ["leaderboard", window, sport, todayOnly],
    queryFn: async () => {
      const res = await fetch(`/leaderboard?${params}`);
      if (!res.ok) throw new Error("Failed to fetch leaderboard");
      return res.json();
    },
    refetchInterval: 30000,
  });

  const entries = data?.leaderboard ?? [];

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-5">
        <div>
          <h1 className="text-2xl font-black text-foreground tracking-tight">Lobby</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Top Player Props Ranked by WOW Confidence Score
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            data-testid="button-refresh"
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-2 rounded-lg border border-border text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={cn(isFetching && "animate-spin")} />
          </button>
          <div className="flex rounded-lg border border-border overflow-hidden" data-testid="toggle-window">
            {WINDOWS.map((w) => (
              <button
                key={w}
                data-testid={`button-window-${w}`}
                onClick={() => setWindow(w)}
                className={cn(
                  "px-3 py-1.5 text-xs font-bold transition-colors",
                  window === w ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {w}
              </button>
            ))}
          </div>
          <div className="flex rounded-lg border border-border overflow-hidden">
            <button
              onClick={() => setTodayOnly(true)}
              className={cn("px-3 py-1.5 text-xs font-bold transition-colors", todayOnly ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              Today
            </button>
            <button
              onClick={() => setTodayOnly(false)}
              className={cn("px-3 py-1.5 text-xs font-bold transition-colors", !todayOnly ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              All Time
            </button>
          </div>
          <div className="flex rounded-lg border border-border overflow-hidden">
            <button
              onClick={() => setView("grid")}
              className={cn("p-2 transition-colors", view === "grid" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              <LayoutGrid size={14} />
            </button>
            <button
              onClick={() => setView("list")}
              className={cn("p-2 transition-colors", view === "list" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              <List size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 mb-4 flex-wrap">
        {[
          { dot: "bg-emerald-400", label: "85%+ Elite Edge" },
          { dot: "bg-violet-400",  label: "75-84% Strong Edge" },
          { dot: "bg-amber-400",   label: "70-74% Value Play" },
          { dot: "bg-rose-400",    label: "<70% No Play" },
        ].map(({ dot, label }) => (
          <div key={label} className="flex items-center gap-1.5">
            <div className={cn("w-2 h-2 rounded-full", dot)} />
            <span className="text-[11px] text-muted-foreground">{label}</span>
          </div>
        ))}
        <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
          {(["APPROVED","CONDITIONAL","WATCH","REJECT"] as Status[]).map((s) => {
            const ss = statusStyle(s);
            return (
              <span key={s} className={cn("flex items-center gap-1")}>
                <span className={cn("w-2 h-2 rounded-sm inline-block", ss.bar)} />
                <span>{s.charAt(0) + s.slice(1).toLowerCase()}</span>
              </span>
            );
          })}
        </div>
      </div>

      {/* Sport filters */}
      <div className="flex gap-2 mb-6 flex-wrap" data-testid="filter-sports">
        {SPORTS.map((s) => (
          <button
            key={s}
            data-testid={`button-sport-${s}`}
            onClick={() => setSport(s)}
            className={cn(
              "px-3 py-1 rounded-full text-xs font-semibold border transition-colors",
              sport === s
                ? "bg-primary/20 text-primary border-primary/50"
                : "border-border text-muted-foreground hover:text-foreground"
            )}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Loading skeleton */}
      {isLoading && (
        <div className={cn(view === "grid" ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4" : "flex flex-col gap-3")}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-card border border-card-border rounded-xl p-4 animate-pulse space-y-3">
              <div className="flex gap-3">
                <div className="w-11 h-11 rounded-xl bg-muted shrink-0" />
                <div className="flex-1 space-y-2 pt-1">
                  <div className="h-3.5 bg-muted rounded w-3/4" />
                  <div className="h-2.5 bg-muted rounded w-1/2" />
                </div>
                <div className="space-y-2">
                  <div className="h-8 bg-muted rounded w-10" />
                  <div className="h-4 bg-muted rounded w-14" />
                </div>
              </div>
              <div className="h-px bg-muted" />
              <div className="grid grid-cols-5 gap-1">
                {Array.from({ length: 5 }).map((_, j) => <div key={j} className="h-8 bg-muted rounded" />)}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="h-10 bg-muted rounded" />
                <div className="h-10 bg-muted rounded" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Error */}
      {isError && (
        <div data-testid="status-error" className="text-center py-20">
          <Activity size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground">Could not load leaderboard. Score some picks to populate data.</p>
          <button data-testid="button-retry" onClick={() => refetch()} className="mt-3 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg font-semibold hover:opacity-90 transition-opacity">
            Retry
          </button>
        </div>
      )}

      {/* Empty */}
      {!isLoading && !isError && entries.length === 0 && (
        <div data-testid="status-empty" className="text-center py-20">
          <Activity size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-foreground font-semibold mb-1">No picks scored yet</p>
          <p className="text-muted-foreground text-sm">Head to Score a Pick to submit your first prop.</p>
        </div>
      )}

      {/* Cards */}
      <AnimatePresence mode="wait">
        {!isLoading && entries.length > 0 && (
          <motion.div
            key={`${window}-${sport}-${view}`}
            className={cn(
              view === "grid"
                ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
                : "flex flex-col gap-3"
            )}
          >
            {entries.map((entry, i) => (
              <PickCard
                key={`${entry.player}-${entry.prop}-${entry.side}`}
                entry={entry}
                index={i}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

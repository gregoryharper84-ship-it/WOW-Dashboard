import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { TrendingUp, TrendingDown, Activity, RefreshCw } from "lucide-react";
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

function scoreColor(score: number) {
  if (score >= 80) return "text-emerald-400";
  if (score >= 65) return "text-violet-400";
  if (score >= 50) return "text-amber-400";
  return "text-rose-400";
}

function barColor(score: number) {
  if (score >= 80) return "#34d399";
  if (score >= 65) return "#a78bfa";
  if (score >= 50) return "#fbbf24";
  return "#f87171";
}

function initials(name: string) {
  return name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2);
}

function avatarGradient(name: string) {
  const gradients = [
    "from-violet-500/40 to-indigo-600/20",
    "from-emerald-500/40 to-teal-600/20",
    "from-rose-500/40 to-pink-600/20",
    "from-amber-500/40 to-orange-600/20",
    "from-sky-500/40 to-blue-600/20",
    "from-fuchsia-500/40 to-purple-600/20",
  ];
  const idx = name.charCodeAt(0) % gradients.length;
  return gradients[idx];
}

function MiniBarChart({ scores }: { scores: number[] }) {
  if (!scores || scores.length === 0) return null;
  const bars = scores.slice(-10);
  return (
    <div className="flex items-end gap-0.5 h-8">
      {bars.map((s, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm transition-all"
          style={{
            height: `${Math.max(12, s)}%`,
            backgroundColor: barColor(s),
            opacity: 0.5 + (i / bars.length) * 0.5,
          }}
        />
      ))}
    </div>
  );
}

function PickCard({ entry, index }: { entry: LeaderboardEntry; index: number }) {
  const isMore = isMORE(entry.side);

  return (
    <motion.div
      data-testid={`card-pick-${entry.rank}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.04, ease: "easeOut" }}
      className="bg-card border border-card-border rounded-2xl overflow-hidden hover:border-primary/40 hover:shadow-lg hover:shadow-primary/5 transition-all duration-200"
    >
      {/* Top row */}
      <div className="flex items-center gap-4 p-4 pb-3">
        {/* Avatar */}
        <div
          className={cn(
            "w-14 h-14 rounded-xl bg-gradient-to-br flex items-center justify-center text-white font-black text-lg shrink-0 ring-1 ring-white/10",
            avatarGradient(entry.player)
          )}
        >
          {initials(entry.player)}
        </div>

        {/* Name + sport/prop */}
        <div className="flex-1 min-w-0">
          <p
            data-testid={`text-player-${entry.rank}`}
            className="font-black text-foreground text-base leading-tight truncate"
          >
            {entry.player}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5 uppercase tracking-wide font-medium truncate">
            {entry.sport} · {entry.prop}
          </p>
        </div>

        {/* Line + side */}
        <div className="text-right shrink-0">
          <p className="text-3xl font-black tabular-nums text-foreground leading-none">
            {entry.latest_line != null ? entry.latest_line.toFixed(1) : "—"}
          </p>
          <span
            data-testid={`badge-side-${entry.rank}`}
            className={cn(
              "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold mt-1",
              isMore
                ? "bg-emerald-400/15 text-emerald-400 ring-1 ring-emerald-400/25"
                : "bg-rose-400/15 text-rose-400 ring-1 ring-rose-400/25"
            )}
          >
            {isMore ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
            {entry.side}
          </span>
        </div>
      </div>

      {/* Divider */}
      <div className="h-px bg-border mx-4" />

      {/* Stats row */}
      <div className="grid grid-cols-4 px-4 py-3 gap-2">
        {[
          { label: "AVG", value: entry.average_score, isScore: true },
          { label: "BEST", value: entry.max_score, isScore: true },
          { label: "LATEST", value: entry.latest_score, isScore: true },
          { label: "PICKS", value: entry.record_count, isScore: false },
        ].map(({ label, value, isScore }) => (
          <div key={label} className="text-center">
            <p className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider mb-0.5">
              {label}
            </p>
            <p
              className={cn(
                "text-sm font-black tabular-nums",
                isScore ? scoreColor(value as number) : "text-foreground"
              )}
            >
              {isScore ? `${(value as number).toFixed(0)}` : value}
              {isScore && <span className="text-[10px] font-semibold opacity-70">%</span>}
            </p>
          </div>
        ))}
      </div>

      {/* Bar chart */}
      {entry.scores && entry.scores.length > 0 && (
        <div className="px-4 pb-4">
          <MiniBarChart scores={entry.scores} />
        </div>
      )}
    </motion.div>
  );
}

export default function Lobby() {
  const [window, setWindow] = useState<"L5" | "L10">("L10");
  const [sport, setSport] = useState("All");

  const params = new URLSearchParams({ window, limit: "50" });
  if (sport !== "All") params.set("sport", sport);

  const { data, isLoading, isError, refetch, isFetching } = useQuery<LeaderboardResponse>({
    queryKey: ["leaderboard", window, sport],
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
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-black text-foreground tracking-tight">Lobby</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Top player props ranked by WOW confidence score
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            data-testid="button-refresh"
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-2 rounded-lg border border-border text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={15} className={cn(isFetching && "animate-spin")} />
          </button>
          <div className="flex rounded-lg border border-border overflow-hidden" data-testid="toggle-window">
            {WINDOWS.map((w) => (
              <button
                key={w}
                data-testid={`button-window-${w}`}
                onClick={() => setWindow(w)}
                className={cn(
                  "px-3 py-1.5 text-xs font-bold transition-colors",
                  window === w
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {w}
              </button>
            ))}
          </div>
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
                : "border-border text-muted-foreground hover:text-foreground hover:border-border/80"
            )}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Loading skeleton */}
      {isLoading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-card border border-card-border rounded-2xl p-4 animate-pulse space-y-3">
              <div className="flex gap-3">
                <div className="w-14 h-14 rounded-xl bg-muted shrink-0" />
                <div className="flex-1 space-y-2 pt-1">
                  <div className="h-4 bg-muted rounded w-3/4" />
                  <div className="h-3 bg-muted rounded w-1/2" />
                </div>
                <div className="space-y-2">
                  <div className="h-8 bg-muted rounded w-12" />
                  <div className="h-4 bg-muted rounded w-12" />
                </div>
              </div>
              <div className="h-px bg-muted" />
              <div className="grid grid-cols-4 gap-2">
                {Array.from({ length: 4 }).map((_, j) => (
                  <div key={j} className="h-8 bg-muted rounded" />
                ))}
              </div>
              <div className="h-8 bg-muted rounded" />
            </div>
          ))}
        </div>
      )}

      {/* Error */}
      {isError && (
        <div data-testid="status-error" className="text-center py-20">
          <Activity size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground">Could not load leaderboard. The API may have no data yet.</p>
          <button
            data-testid="button-retry"
            onClick={() => refetch()}
            className="mt-3 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg font-semibold hover:opacity-90 transition-opacity"
          >
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

      {/* Cards grid */}
      <AnimatePresence mode="wait">
        {!isLoading && entries.length > 0 && (
          <motion.div
            key={`${window}-${sport}`}
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
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

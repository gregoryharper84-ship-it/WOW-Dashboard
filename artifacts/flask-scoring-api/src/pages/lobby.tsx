import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { TrendingUp, TrendingDown, Activity, RefreshCw, Trophy } from "lucide-react";
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
  latest_timestamp: string;
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
  if (score >= 65) return "text-primary";
  if (score >= 50) return "text-amber-400";
  return "text-rose-400";
}

function scoreBarColor(score: number) {
  if (score >= 80) return "bg-emerald-400";
  if (score >= 65) return "bg-primary";
  if (score >= 50) return "bg-amber-400";
  return "bg-rose-400";
}

function scoreLabel(score: number) {
  if (score >= 80) return "Strong";
  if (score >= 65) return "Solid";
  if (score >= 50) return "Neutral";
  return "Weak";
}

function PickCard({ entry, index }: { entry: LeaderboardEntry; index: number }) {
  const isMore = isMORE(entry.side);

  return (
    <motion.div
      data-testid={`card-pick-${entry.rank}`}
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.04, ease: "easeOut" }}
      className="relative bg-card border border-card-border rounded-xl p-4 hover:border-primary/40 hover:shadow-lg hover:shadow-primary/5 transition-all duration-200 group"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <span
            data-testid={`text-rank-${entry.rank}`}
            className={cn(
              "w-7 h-7 rounded-full flex items-center justify-center text-xs font-black shrink-0",
              entry.rank === 1 ? "bg-amber-400/20 text-amber-400 ring-1 ring-amber-400/40" :
              entry.rank === 2 ? "bg-slate-400/20 text-slate-300 ring-1 ring-slate-400/30" :
              entry.rank === 3 ? "bg-orange-700/20 text-orange-400 ring-1 ring-orange-700/40" :
              "bg-muted text-muted-foreground"
            )}
          >
            {entry.rank === 1 ? <Trophy size={12} /> : entry.rank}
          </span>
          <div>
            <p data-testid={`text-player-${entry.rank}`} className="text-sm font-bold text-foreground leading-none">
              {entry.player}
            </p>
            <span className="text-xs text-muted-foreground">{entry.sport}</span>
          </div>
        </div>
        <span
          data-testid={`badge-side-${entry.rank}`}
          className={cn(
            "flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold",
            isMore
              ? "bg-emerald-400/15 text-emerald-400 ring-1 ring-emerald-400/30"
              : "bg-rose-400/15 text-rose-400 ring-1 ring-rose-400/30"
          )}
        >
          {isMore ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
          {entry.side}
        </span>
      </div>

      <div className="mb-3">
        <p data-testid={`text-prop-${entry.rank}`} className="text-xs text-muted-foreground uppercase tracking-wider font-medium">
          {entry.prop}
        </p>
      </div>

      <div className="flex items-end justify-between mb-2">
        <div>
          <p className="text-xs text-muted-foreground mb-0.5">WOW Score</p>
          <p
            data-testid={`text-score-${entry.rank}`}
            className={cn("text-2xl font-black tabular-nums", scoreColor(entry.average_score))}
          >
            {entry.average_score.toFixed(1)}
            <span className="text-sm font-semibold ml-0.5">%</span>
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted-foreground mb-0.5">Samples</p>
          <p className="text-sm font-bold text-foreground">{entry.record_count}</p>
        </div>
      </div>

      <div className="space-y-1">
        <div className="flex justify-between items-center">
          <span className={cn("text-xs font-semibold", scoreColor(entry.average_score))}>
            {scoreLabel(entry.average_score)}
          </span>
          <span className="text-xs text-muted-foreground">
            Latest: {entry.latest_score.toFixed(1)}%
          </span>
        </div>
        <div className="h-1.5 bg-muted rounded-full overflow-hidden">
          <motion.div
            className={cn("h-full rounded-full", scoreBarColor(entry.average_score))}
            initial={{ width: 0 }}
            animate={{ width: `${Math.min(entry.average_score, 100)}%` }}
            transition={{ duration: 0.6, delay: index * 0.04 + 0.2, ease: "easeOut" }}
          />
        </div>
      </div>
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

      {isLoading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="bg-card border border-card-border rounded-xl p-4 animate-pulse">
              <div className="h-4 bg-muted rounded mb-3 w-2/3" />
              <div className="h-8 bg-muted rounded mb-2 w-1/2" />
              <div className="h-2 bg-muted rounded" />
            </div>
          ))}
        </div>
      )}

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

      {!isLoading && !isError && entries.length === 0 && (
        <div data-testid="status-empty" className="text-center py-20">
          <Activity size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-foreground font-semibold mb-1">No picks scored yet</p>
          <p className="text-muted-foreground text-sm">Head to Score a Pick to submit your first prop.</p>
        </div>
      )}

      <AnimatePresence mode="wait">
        {!isLoading && entries.length > 0 && (
          <motion.div
            key={`${window}-${sport}`}
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
          >
            {entries.map((entry, i) => (
              <PickCard key={`${entry.player}-${entry.prop}-${entry.side}`} entry={entry} index={i} />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

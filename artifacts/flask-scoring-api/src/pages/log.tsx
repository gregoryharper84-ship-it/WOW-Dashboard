import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, Clock, RefreshCw, Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "date-fns";

interface LogEntry {
  timestamp: string;
  player: string;
  sport: string;
  prop: string;
  side: string;
  line: number;
  score: number;
  label: string;
}

interface LogResponse {
  count: number;
  requests: LogEntry[];
  window: string;
}

const WINDOWS = ["L5", "L10"] as const;

function scoreColor(score: number) {
  if (score >= 80) return "text-emerald-400 bg-emerald-400/10 border-emerald-400/20";
  if (score >= 65) return "text-primary bg-primary/10 border-primary/20";
  if (score >= 50) return "text-amber-400 bg-amber-400/10 border-amber-400/20";
  return "text-rose-400 bg-rose-400/10 border-rose-400/20";
}

function isMORE(side: string) {
  return ["MORE", "more", "over", "OVER"].includes(side);
}

export default function Log() {
  const [window, setWindow] = useState<"L5" | "L10">("L10");

  const { data, isLoading, isError, refetch, isFetching } = useQuery<LogResponse>({
    queryKey: ["log", window],
    queryFn: async () => {
      const res = await fetch(`/request-log?window=${window}&limit=100`);
      if (!res.ok) throw new Error("Failed to fetch log");
      return res.json();
    },
    refetchInterval: 20000,
  });

  const entries = data?.requests ?? [];

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-black text-foreground tracking-tight">Log</h1>
          <p className="text-sm text-muted-foreground mt-0.5">Recent scoring history, newest first</p>
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
                  window === w ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {w}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="bg-card border border-card-border rounded-xl h-16 animate-pulse" />
          ))}
        </div>
      )}

      {isError && (
        <div className="text-center py-20" data-testid="status-error">
          <Activity size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground">Could not load log</p>
        </div>
      )}

      {!isLoading && !isError && entries.length === 0 && (
        <div className="text-center py-20" data-testid="status-empty">
          <Clock size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-foreground font-semibold mb-1">No scoring history yet</p>
          <p className="text-sm text-muted-foreground">Scored picks will appear here.</p>
        </div>
      )}

      {entries.length > 0 && (
        <div className="space-y-2">
          {entries.map((entry, i) => {
            const isMore = isMORE(entry.side);
            let timeAgo = "";
            try {
              timeAgo = formatDistanceToNow(new Date(entry.timestamp), { addSuffix: true });
            } catch {
              timeAgo = entry.timestamp;
            }

            return (
              <motion.div
                key={i}
                data-testid={`row-log-${i}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.025, duration: 0.3 }}
                className="bg-card border border-card-border rounded-xl px-4 py-3 flex items-center gap-4 hover:border-border/80 transition-colors"
              >
                <div className="shrink-0">
                  {isMore ? (
                    <TrendingUp size={16} className="text-emerald-400" />
                  ) : (
                    <TrendingDown size={16} className="text-rose-400" />
                  )}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2">
                    <p className="text-sm font-bold text-foreground truncate" data-testid={`text-player-${i}`}>
                      {entry.player}
                    </p>
                    <span className="text-xs text-muted-foreground shrink-0">{entry.sport}</span>
                  </div>
                  <p className="text-xs text-muted-foreground truncate">
                    {entry.side} {entry.prop} · line {entry.line}
                  </p>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <span
                    data-testid={`badge-score-${i}`}
                    className={cn(
                      "px-2.5 py-1 rounded-lg text-sm font-black tabular-nums border",
                      scoreColor(entry.score)
                    )}
                  >
                    {entry.score.toFixed(1)}%
                  </span>
                  <span className="text-xs text-muted-foreground w-20 text-right hidden sm:block">
                    {timeAgo}
                  </span>
                </div>
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}

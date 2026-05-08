import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, Activity } from "lucide-react";
import { cn } from "@/lib/utils";

interface SportStat {
  sport: string;
  requests: number;
  avg_score: number;
}

interface TopProp {
  player: string;
  sport: string;
  prop: string;
  side: string;
  line: number;
  avg_score: number;
  times_scored: number;
}

interface StatsResponse {
  window: string;
  record_count: number;
  average_score: number;
  max_score: number;
  min_score: number;
  over_count: number;
  under_count: number;
  over_average_score: number | null;
  under_average_score: number | null;
  average_score_by_sport: SportStat[];
  top_scored_props: TopProp[];
}

const WINDOWS = ["L5", "L10"] as const;

function StatCard({
  label,
  value,
  sub,
  accent = false,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-card border border-card-border rounded-xl p-4"
      data-testid={`card-stat-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <p className="text-xs text-muted-foreground uppercase tracking-wider font-medium mb-1">{label}</p>
      <p className={cn("text-2xl font-black tabular-nums", accent ? "text-primary" : "text-foreground")}>
        {value}
      </p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </motion.div>
  );
}

const CHART_COLORS = ["#a78bfa", "#34d399", "#fbbf24", "#f87171", "#38bdf8", "#fb923c"];

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-popover border border-popover-border rounded-lg px-3 py-2 text-xs shadow-lg">
      <p className="font-bold text-foreground mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} style={{ color: p.fill }}>
          {p.name}: {p.dataKey === "avg_score" ? `${Number(p.value).toFixed(1)}%` : p.value}
        </p>
      ))}
    </div>
  );
};

export default function Stats() {
  const [window, setWindow] = useState<"L5" | "L10">("L10");

  const { data, isLoading, isError } = useQuery<StatsResponse>({
    queryKey: ["stats", window],
    queryFn: async () => {
      const res = await fetch(`/stats?window=${window}`);
      if (!res.ok) throw new Error("Failed to fetch stats");
      return res.json();
    },
  });

  const sportData = data?.average_score_by_sport ?? [];
  const topProps = data?.top_scored_props ?? [];
  const totalSides = (data?.over_count ?? 0) + (data?.under_count ?? 0);
  const morePct = totalSides > 0 ? ((data?.over_count ?? 0) / totalSides) * 100 : 50;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-black text-foreground tracking-tight">Stats</h1>
          <p className="text-sm text-muted-foreground mt-0.5">Aggregate scoring analytics</p>
        </div>
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

      {isLoading && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-card border border-card-border rounded-xl p-4 animate-pulse h-24" />
          ))}
        </div>
      )}

      {isError && (
        <div className="text-center py-20" data-testid="status-error">
          <Activity size={32} className="text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground">Could not load stats</p>
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <StatCard label="Total Scored" value={String(data.record_count)} accent />
            <StatCard
              label="Avg WOW Score"
              value={`${data.average_score.toFixed(1)}%`}
            />
            <StatCard
              label="Peak Score"
              value={`${data.max_score.toFixed(1)}%`}
            />
            <StatCard
              label="Floor Score"
              value={`${data.min_score.toFixed(1)}%`}
            />
          </div>

          <div className="grid lg:grid-cols-3 gap-6 mb-8">
            <div className="lg:col-span-2 bg-card border border-card-border rounded-xl p-5">
              <h2 className="text-sm font-bold text-foreground mb-4">Avg Score by Sport</h2>
              {sportData.length === 0 ? (
                <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
                  No sport data yet
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={sportData} barSize={28} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                    <XAxis dataKey="sport" tick={{ fill: "hsl(215 20.2% 65.1%)", fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tickFormatter={(v) => `${v.toFixed(0)}%`} tick={{ fill: "hsl(215 20.2% 65.1%)", fontSize: 11 }} axisLine={false} tickLine={false} domain={[0, 100]} />
                    <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(167,139,250,0.06)" }} />
                    <Bar dataKey="avg_score" name="Avg Score" radius={[4, 4, 0, 0]}>
                      {sportData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>

            <div className="bg-card border border-card-border rounded-xl p-5">
              <h2 className="text-sm font-bold text-foreground mb-4">MORE vs LESS</h2>
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <TrendingUp size={18} className="text-emerald-400 shrink-0" />
                  <div className="flex-1">
                    <div className="flex justify-between mb-1">
                      <span className="text-xs font-semibold text-emerald-400">MORE</span>
                      <span className="text-xs text-muted-foreground">{data.over_count}</span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-emerald-400 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${morePct}%` }}
                        transition={{ duration: 0.8, ease: "easeOut" }}
                      />
                    </div>
                    {data.over_average_score != null && (
                      <p className="text-xs text-muted-foreground mt-1">
                        Avg: {data.over_average_score.toFixed(1)}%
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <TrendingDown size={18} className="text-rose-400 shrink-0" />
                  <div className="flex-1">
                    <div className="flex justify-between mb-1">
                      <span className="text-xs font-semibold text-rose-400">LESS</span>
                      <span className="text-xs text-muted-foreground">{data.under_count}</span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-rose-400 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${100 - morePct}%` }}
                        transition={{ duration: 0.8, ease: "easeOut" }}
                      />
                    </div>
                    {data.under_average_score != null && (
                      <p className="text-xs text-muted-foreground mt-1">
                        Avg: {data.under_average_score.toFixed(1)}%
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {topProps.length > 0 && (
            <div className="bg-card border border-card-border rounded-xl p-5">
              <h2 className="text-sm font-bold text-foreground mb-4">Top Scored Props</h2>
              <div className="space-y-2">
                {topProps.slice(0, 10).map((prop, i) => (
                  <motion.div
                    key={i}
                    data-testid={`row-top-prop-${i}`}
                    initial={{ opacity: 0, x: -12 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.05 }}
                    className="flex items-center gap-4 py-2 border-b border-border last:border-0"
                  >
                    <span className="text-xs text-muted-foreground w-5 shrink-0 text-right">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-foreground truncate">{prop.player}</p>
                      <p className="text-xs text-muted-foreground">{prop.sport} · {prop.prop} · {prop.line}</p>
                    </div>
                    <span
                      className={cn(
                        "px-2 py-0.5 rounded-full text-xs font-bold shrink-0",
                        prop.side === "MORE"
                          ? "bg-emerald-400/15 text-emerald-400"
                          : "bg-rose-400/15 text-rose-400"
                      )}
                    >
                      {prop.side}
                    </span>
                    <span className="text-sm font-black tabular-nums text-primary shrink-0 w-12 text-right">
                      {prop.avg_score.toFixed(1)}%
                    </span>
                  </motion.div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

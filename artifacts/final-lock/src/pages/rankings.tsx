import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { RefreshCw, TrendingUp, Target, Award, GitBranch, AlertCircle, CheckCircle } from "lucide-react";

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface ThreeState {
  raw_more: number | null;
  raw_exact: number | null;
  raw_less: number | null;
  cal_more: number | null;
  cal_exact: number | null;
  cal_less: number | null;
}

interface RankedProp {
  rank: number;
  player_name: string;
  sport: string;
  stat_key: string;
  side: string;
  line: number;
  terminal_label: string;
  cal_lower_bound: number;
  calibrated_probability: number;
  raw_probability: number | null;
  three_state: ThreeState;
  pure_edge: number | null;
  edge_tier: string;
  market_probability: number | null;
  model_status: string;
  event_key: string | null;
  dominant_dependency: string | null;
  failure_path_prob: number | null;
  blockers: string[];
  dependence_flags: string[];
  can_execute: false;
  source: string;
}

interface MultiLeg {
  legs: RankedProp[];
  combined_probability: number;
  weakest_lb: number;
  dependence_verdict: string;
  dependence_flags: string[];
  slip_label: string;
  can_execute: false;
}

interface RankingsData {
  highest_hit_probability: RankedProp[];
  highest_calibrated_prob: RankedProp[];
  best_edge: RankedProp[];
  best_multi_leg: MultiLeg[];
  summary: {
    n_eligible: number;
    n_eliminated_weak: number;
    n_total_input: number;
    sports_covered: string[];
  };
  ranker_version: string;
  can_execute: false;
  requires_human_confirmation: true;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const pct = (v: number | null | undefined, digits = 1) =>
  v != null ? `${(v * 100).toFixed(digits)}%` : "—";

const EDGE_COLORS: Record<string, string> = {
  SEVERE_DRIFT:  "bg-emerald-600/20 text-emerald-300 border-emerald-600/40",
  STRONG_DRIFT:  "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  MILD_DRIFT:    "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
  ALIGNED:       "bg-zinc-700/30 text-zinc-400 border-zinc-600/30",
  NEGATIVE_EDGE: "bg-red-500/15 text-red-400 border-red-500/30",
  NO_MARKET_DATA:"bg-zinc-700/30 text-zinc-500 border-zinc-600/20",
};

const LABEL_COLORS: Record<string, string> = {
  FINAL_APPROVED:           "bg-emerald-600/20 text-emerald-300 border-emerald-600/30",
  MONEY_QUALIFIED:          "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  YES_MODEL_QUALIFIED:      "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  MODEL_QUALIFIED_HOLD:     "bg-amber-500/15 text-amber-300 border-amber-500/30",
  MARKET_VERIFIED_HOLD:     "bg-amber-500/15 text-amber-300 border-amber-500/30",
  RESEARCH_INTEREST:        "bg-blue-500/15 text-blue-300 border-blue-500/30",
  WATCH:                    "bg-zinc-600/20 text-zinc-300 border-zinc-600/30",
  HOLD:                     "bg-zinc-600/20 text-zinc-400 border-zinc-600/30",
};

const DEPENDENCE_COLORS: Record<string, string> = {
  CLEAN:              "text-emerald-400",
  DEPENDENCE_WARNING: "text-yellow-400",
  DEPENDENCE_BLOCK:   "text-red-400",
};

const SPORT_ICONS: Record<string, string> = {
  WNBA: "🏀", TENNIS: "🎾", MLB: "⚾", NBA: "🏀",
  NFL: "🏈", NHL: "🏒", SOCCER: "⚽",
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
function PropCard({ prop, showEdge = false }: { prop: RankedProp; showEdge?: boolean }) {
  const { raw_more, raw_exact, raw_less, cal_more, cal_exact, cal_less } = prop.three_state;
  const hasThreeState = cal_more != null;

  return (
    <div className="group relative border border-border rounded-xl p-4 bg-card hover:border-primary/30 transition-colors">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-xs">{SPORT_ICONS[prop.sport] ?? "🎯"}</span>
            <span className="font-semibold text-foreground truncate">{prop.player_name || "—"}</span>
            <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
              {prop.sport}
            </Badge>
          </div>
          <div className="text-sm text-muted-foreground">
            {prop.stat_key} · {prop.side} {prop.line} {prop.event_key ? `· ${prop.event_key}` : ""}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xl font-bold text-primary tabular-nums">
            {pct(prop.cal_lower_bound)}
          </div>
          <div className="text-[10px] text-muted-foreground">CLB floor</div>
        </div>
      </div>

      {/* Label + edge tier */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 ${LABEL_COLORS[prop.terminal_label] ?? "bg-zinc-700/20 text-zinc-400 border-zinc-600/30"}`}
        >
          {prop.terminal_label}
        </Badge>
        {showEdge && prop.pure_edge != null && (
          <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${EDGE_COLORS[prop.edge_tier]}`}>
            edge {prop.pure_edge > 0 ? "+" : ""}{pct(prop.pure_edge)} · {prop.edge_tier}
          </Badge>
        )}
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 bg-zinc-700/20 text-zinc-500 border-zinc-600/20">
          {prop.model_status}
        </Badge>
      </div>

      {/* Probability row */}
      <div className="grid grid-cols-3 gap-2 text-center text-xs mb-3 bg-muted/30 rounded-lg p-2">
        <div>
          <div className="text-muted-foreground text-[10px] mb-0.5">cal prob</div>
          <div className="font-semibold tabular-nums">{pct(prop.calibrated_probability)}</div>
        </div>
        <div>
          <div className="text-muted-foreground text-[10px] mb-0.5">CLB</div>
          <div className="font-bold text-primary tabular-nums">{pct(prop.cal_lower_bound)}</div>
        </div>
        <div>
          <div className="text-muted-foreground text-[10px] mb-0.5">mkt prob</div>
          <div className="tabular-nums">{prop.market_probability != null ? pct(prop.market_probability) : "—"}</div>
        </div>
      </div>

      {/* Three-state breakdown */}
      {hasThreeState && (
        <div className="mb-3">
          <div className="text-[10px] text-muted-foreground mb-1">Three-state (cal)</div>
          <div className="flex gap-1">
            {[
              { label: "MORE",  val: cal_more,  color: "bg-emerald-600/20" },
              { label: "EXACT", val: cal_exact, color: "bg-blue-600/20" },
              { label: "LESS",  val: cal_less,  color: "bg-red-600/15" },
            ].map(({ label, val, color }) => (
              <div key={label} className={`flex-1 rounded px-1 py-1 text-center ${color}`}>
                <div className="text-[9px] text-muted-foreground">{label}</div>
                <div className="text-xs font-semibold tabular-nums">{pct(val)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Dependency + failure path */}
      {(prop.dominant_dependency || prop.failure_path_prob != null) && (
        <div className="flex gap-3 text-[11px] text-muted-foreground">
          {prop.dominant_dependency && (
            <span>dominant dep: <span className="text-foreground">{prop.dominant_dependency.replace(/_/g, " ")}</span></span>
          )}
          {prop.failure_path_prob != null && (
            <span>fail path: <span className={prop.failure_path_prob > 0.25 ? "text-amber-400" : "text-foreground"}>{pct(prop.failure_path_prob)}</span></span>
          )}
        </div>
      )}

      {/* Blockers */}
      {prop.blockers.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {prop.blockers.slice(0, 3).map((b, i) => (
            <Badge key={i} variant="outline" className="text-[9px] px-1 py-0 bg-amber-500/10 text-amber-400 border-amber-500/20">
              {b.length > 32 ? b.slice(0, 32) + "…" : b}
            </Badge>
          ))}
          {prop.blockers.length > 3 && (
            <Badge variant="outline" className="text-[9px] px-1 py-0 text-muted-foreground">
              +{prop.blockers.length - 3} more
            </Badge>
          )}
        </div>
      )}

      {/* can_execute invariant banner */}
      <div className="absolute bottom-2 right-3 text-[9px] text-zinc-600 select-none">
        can_execute: false
      </div>
    </div>
  );
}

function MultiLegCard({ candidate }: { candidate: MultiLeg }) {
  const dcol = DEPENDENCE_COLORS[candidate.dependence_verdict] ?? "text-zinc-400";
  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-sm font-semibold">{candidate.slip_label}</CardTitle>
          <div className="flex items-center gap-2">
            <span className={`text-xs font-medium ${dcol}`}>{candidate.dependence_verdict}</span>
            <Badge variant="outline" className="text-xs">
              CLB× {pct(candidate.combined_probability)}
            </Badge>
          </div>
        </div>
        {candidate.dependence_flags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {candidate.dependence_flags.map((f, i) => (
              <Badge key={i} variant="outline" className="text-[9px] px-1 py-0 bg-amber-500/10 text-amber-400 border-amber-500/20">
                {f}
              </Badge>
            ))}
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        {candidate.legs.map((leg, i) => (
          <div key={i} className="flex items-center justify-between text-sm bg-muted/20 rounded-lg px-3 py-2">
            <div className="min-w-0">
              <span className="text-[10px] mr-1">{SPORT_ICONS[leg.sport] ?? "🎯"}</span>
              <span className="font-medium">{leg.player_name || "—"}</span>
              <span className="text-muted-foreground ml-2">{leg.stat_key} {leg.side} {leg.line}</span>
            </div>
            <div className="text-right shrink-0 ml-3">
              <div className="font-bold tabular-nums text-primary">{pct(leg.cal_lower_bound)}</div>
              <div className="text-[9px] text-muted-foreground">CLB</div>
            </div>
          </div>
        ))}
        <div className="text-[10px] text-muted-foreground mt-2 text-right">
          weakest leg: {pct(candidate.weakest_lb)} · requires human confirmation · can_execute: false
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
type TabId = "hit_prob" | "cal_prob" | "edge" | "multi_leg";

const TABS: { id: TabId; label: string; icon: typeof Target }[] = [
  { id: "hit_prob",  label: "Highest Hit Prob",   icon: Target },
  { id: "cal_prob",  label: "Highest Cal Prob",    icon: TrendingUp },
  { id: "edge",      label: "Best Edge",           icon: Award },
  { id: "multi_leg", label: "Multi-Leg",           icon: GitBranch },
];

export default function RankingsPage() {
  const [sport, setSport] = useState("all");
  const [tab, setTab] = useState<TabId>("hit_prob");

  const { data, isLoading, isError, refetch } = useQuery<{ ok: boolean } & RankingsData>({
    queryKey: ["rankings", sport],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (sport !== "all") params.set("sport", sport);
      const res = await fetch(`${API_BASE}/wow/rankings?${params}`);
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    refetchInterval: 60_000,
  });

  const rankings = data as (RankingsData & { ok: boolean }) | undefined;
  const props = rankings
    ? tab === "hit_prob"  ? rankings.highest_hit_probability
    : tab === "cal_prob"  ? rankings.highest_calibrated_prob
    : tab === "edge"      ? rankings.best_edge
    : []
    : [];

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-4 max-w-6xl mx-auto">
          <div>
            <h1 className="text-xl font-bold tracking-tight">Cross-Sport Rankings</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Ranked by calibrated lower bound (CLB) · can_execute: false · requires human confirmation
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Select value={sport} onValueChange={setSport}>
              <SelectTrigger className="w-32 h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Sports</SelectItem>
                <SelectItem value="WNBA">WNBA</SelectItem>
                <SelectItem value="TENNIS">Tennis</SelectItem>
                <SelectItem value="MLB">MLB</SelectItem>
                <SelectItem value="NBA">NBA</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" className="h-8" onClick={() => refetch()}>
              <RefreshCw size={13} className={isLoading ? "animate-spin" : ""} />
            </Button>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        {/* Summary bar */}
        {rankings?.summary && (
          <div className="flex flex-wrap gap-4 text-sm">
            <div className="bg-card border border-border rounded-lg px-4 py-2">
              <span className="text-muted-foreground">Total scored: </span>
              <span className="font-semibold">{rankings.summary.n_total_input}</span>
            </div>
            <div className="bg-card border border-border rounded-lg px-4 py-2">
              <span className="text-muted-foreground">Eligible (CLB≥50%): </span>
              <span className="font-semibold text-primary">{rankings.summary.n_eligible}</span>
            </div>
            <div className="bg-card border border-border rounded-lg px-4 py-2">
              <span className="text-muted-foreground">Eliminated weak: </span>
              <span className="font-semibold text-muted-foreground">{rankings.summary.n_eliminated_weak}</span>
            </div>
            <div className="bg-card border border-border rounded-lg px-4 py-2">
              <span className="text-muted-foreground">Sports: </span>
              <span className="font-semibold">{rankings.summary.sports_covered.join(", ") || "—"}</span>
            </div>
          </div>
        )}

        {/* Tab bar */}
        <div className="flex gap-1 bg-muted/30 rounded-xl p-1 w-fit">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                tab === id
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>

        {/* Error / loading */}
        {isError && (
          <div className="flex items-center gap-2 text-amber-400 text-sm bg-amber-500/10 border border-amber-500/20 rounded-lg px-4 py-3">
            <AlertCircle size={16} />
            Failed to load rankings. No prediction data in ledger yet, or server error.
          </div>
        )}

        {isLoading && (
          <div className="text-muted-foreground text-sm animate-pulse">Loading rankings…</div>
        )}

        {/* Prop grid */}
        {tab !== "multi_leg" && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {props.length === 0 && !isLoading && (
              <div className="col-span-full text-center py-16 text-muted-foreground">
                <Target size={40} className="mx-auto mb-4 opacity-30" />
                <p className="font-medium">No props in this lane yet.</p>
                <p className="text-sm mt-1">Run the pipeline and write predictions to the ledger to see rankings.</p>
              </div>
            )}
            {(props as RankedProp[]).map((prop) => (
              <PropCard key={`${prop.player_name}-${prop.stat_key}-${prop.rank}`} prop={prop} showEdge={tab === "edge"} />
            ))}
          </div>
        )}

        {/* Multi-leg */}
        {tab === "multi_leg" && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground bg-amber-500/5 border border-amber-500/20 rounded-lg px-4 py-2">
              <AlertCircle size={14} className="text-amber-400" />
              Multi-leg slips require human review. Dependence audit is advisory only. No auto-execution.
            </div>
            {(rankings?.best_multi_leg ?? []).length === 0 && !isLoading && (
              <div className="text-center py-16 text-muted-foreground">
                <GitBranch size={40} className="mx-auto mb-4 opacity-30" />
                <p>No multi-leg candidates available.</p>
              </div>
            )}
            {(rankings?.best_multi_leg ?? []).map((c, i) => (
              <MultiLegCard key={i} candidate={c} />
            ))}
          </div>
        )}

        {/* Ranker info */}
        {rankings?.ranker_version && (
          <p className="text-[11px] text-zinc-700 text-right">{rankings.ranker_version}</p>
        )}
      </div>
    </div>
  );
}

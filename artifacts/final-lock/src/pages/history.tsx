import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RefreshCw, Clock, CheckCircle, XCircle, Minus, AlertCircle, Database } from "lucide-react";

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Prediction {
  prediction_id: string;
  sport: string;
  event_key: string | null;
  player_name: string | null;
  market: string | null;
  stat_key: string | null;
  side: string | null;
  line: number | null;
  price: string | null;
  market_probability: number | null;
  raw_probability: number | null;
  calibrated_probability: number | null;
  lower_bound: number | null;
  upper_bound: number | null;
  raw_more: number | null;
  raw_exact: number | null;
  raw_less: number | null;
  cal_more: number | null;
  cal_exact: number | null;
  cal_less: number | null;
  failure_path_score: number | null;
  terminal_label: string | null;
  model_status: string | null;
  sources: Record<string, unknown>;
  pipeline_meta: Record<string, unknown>;
  created_at: string;
  scored_date: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const pct = (v: number | null | undefined) =>
  v != null ? `${(v * 100).toFixed(1)}%` : "—";

const LABEL_COLORS: Record<string, string> = {
  FINAL_APPROVED:       "bg-emerald-600/20 text-emerald-300 border-emerald-600/30",
  MONEY_QUALIFIED:      "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  YES_MODEL_QUALIFIED:  "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  MODEL_QUALIFIED_HOLD: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  RESEARCH_INTEREST:    "bg-blue-500/15 text-blue-300 border-blue-500/30",
  WATCH:                "bg-zinc-600/20 text-zinc-300 border-zinc-600/30",
  HOLD:                 "bg-zinc-600/20 text-zinc-400 border-zinc-600/30",
};

const SPORT_ICONS: Record<string, string> = {
  WNBA: "🏀", TENNIS: "🎾", MLB: "⚾", NBA: "🏀",
  NFL: "🏈", NHL: "🏒", SOCCER: "⚽",
};

function ThreeStatePill({ label, val, color }: { label: string; val: number | null; color: string }) {
  return (
    <div className={`text-center px-2 py-1 rounded ${color}`}>
      <div className="text-[9px] text-muted-foreground">{label}</div>
      <div className="text-xs font-semibold tabular-nums">{pct(val)}</div>
    </div>
  );
}

function PredictionRow({ pred }: { pred: Prediction }) {
  const [expanded, setExpanded] = useState(false);
  const hasThreeState = pred.cal_more != null;

  return (
    <div className="border border-border rounded-xl bg-card overflow-hidden">
      {/* Summary row */}
      <button
        className="w-full text-left px-4 py-3 hover:bg-muted/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm">{SPORT_ICONS[pred.sport ?? ""] ?? "🎯"}</span>
          <span className="font-semibold text-sm">{pred.player_name || "—"}</span>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">{pred.sport}</Badge>
          <span className="text-muted-foreground text-sm">{pred.stat_key} · {pred.side} {pred.line}</span>
          <Badge
            variant="outline"
            className={`text-[10px] px-1.5 py-0 ${LABEL_COLORS[pred.terminal_label ?? ""] ?? "bg-zinc-700/20 text-zinc-400 border-zinc-600/30"}`}
          >
            {pred.terminal_label || "—"}
          </Badge>
          <div className="ml-auto flex items-center gap-3 text-right">
            <div>
              <div className="text-xs font-bold tabular-nums text-primary">{pct(pred.lower_bound)}</div>
              <div className="text-[10px] text-muted-foreground">CLB</div>
            </div>
            <div>
              <div className="text-xs tabular-nums">{pct(pred.calibrated_probability)}</div>
              <div className="text-[10px] text-muted-foreground">cal</div>
            </div>
            <Clock size={13} className="text-muted-foreground" />
            <span className="text-[11px] text-muted-foreground">{pred.scored_date}</span>
          </div>
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-border space-y-3">
          {/* Three-state */}
          {hasThreeState && (
            <div>
              <div className="text-[11px] text-muted-foreground mb-1 font-medium">Three-state distribution</div>
              <div className="flex gap-2">
                <div className="flex-1 space-y-1">
                  <div className="text-[10px] text-muted-foreground">Raw</div>
                  <div className="flex gap-1">
                    <ThreeStatePill label="MORE"  val={pred.raw_more}  color="bg-emerald-600/10" />
                    <ThreeStatePill label="EXACT" val={pred.raw_exact} color="bg-blue-600/10" />
                    <ThreeStatePill label="LESS"  val={pred.raw_less}  color="bg-red-600/10" />
                  </div>
                </div>
                <div className="flex-1 space-y-1">
                  <div className="text-[10px] text-muted-foreground">Calibrated</div>
                  <div className="flex gap-1">
                    <ThreeStatePill label="MORE"  val={pred.cal_more}  color="bg-emerald-600/20" />
                    <ThreeStatePill label="EXACT" val={pred.cal_exact} color="bg-blue-600/20" />
                    <ThreeStatePill label="LESS"  val={pred.cal_less}  color="bg-red-600/15" />
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Probability fields */}
          <div className="grid grid-cols-4 gap-3 text-sm">
            {[
              { label: "Raw prob",     val: pct(pred.raw_probability) },
              { label: "Cal prob",     val: pct(pred.calibrated_probability) },
              { label: "Lower bound",  val: pct(pred.lower_bound) },
              { label: "Upper bound",  val: pct(pred.upper_bound) },
              { label: "Market prob",  val: pct(pred.market_probability) },
              { label: "Fail path",    val: pct(pred.failure_path_score) },
              { label: "Model status", val: pred.model_status ?? "—" },
              { label: "Event",        val: pred.event_key ?? "—" },
            ].map(({ label, val }) => (
              <div key={label} className="bg-muted/20 rounded-lg p-2">
                <div className="text-[10px] text-muted-foreground">{label}</div>
                <div className="text-xs font-medium tabular-nums">{val}</div>
              </div>
            ))}
          </div>

          {/* Metadata */}
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <span className="font-mono">{pred.prediction_id.slice(0, 8)}…</span>
            <span>scored {pred.created_at ? new Date(pred.created_at).toLocaleString() : "—"}</span>
            <span className="ml-auto text-zinc-700 select-none">immutable · can_execute: false</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function HistoryPage() {
  const [sport, setSport]   = useState("all");
  const [label, setLabel]   = useState("all");
  const [sinceDate, setSinceDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().slice(0, 10);
  });

  const { data, isLoading, isError, refetch } = useQuery<{ ok: boolean; count: number; predictions: Prediction[] }>({
    queryKey: ["predictions", sport, label, sinceDate],
    queryFn: async () => {
      const p = new URLSearchParams({ since_date: sinceDate, limit: "100" });
      if (sport !== "all") p.set("sport", sport);
      if (label !== "all") p.set("terminal_label", label);
      const res = await fetch(`${API_BASE}/wow/predictions?${p}`);
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
  });

  const preds = data?.predictions ?? [];

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-4 max-w-5xl mx-auto">
          <div>
            <h1 className="text-xl font-bold tracking-tight">Prediction History</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Immutable prediction ledger · write-once, append-only · can_execute: false
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Input
              type="date"
              value={sinceDate}
              onChange={e => setSinceDate(e.target.value)}
              className="h-8 text-xs w-36"
            />
            <Select value={sport} onValueChange={setSport}>
              <SelectTrigger className="w-28 h-8 text-xs">
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
            <Select value={label} onValueChange={setLabel}>
              <SelectTrigger className="w-44 h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Labels</SelectItem>
                <SelectItem value="YES_MODEL_QUALIFIED">YES_MODEL_QUALIFIED</SelectItem>
                <SelectItem value="MODEL_QUALIFIED_HOLD">MODEL_QUALIFIED_HOLD</SelectItem>
                <SelectItem value="WATCH">WATCH</SelectItem>
                <SelectItem value="FINAL_APPROVED">FINAL_APPROVED</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" className="h-8" onClick={() => refetch()}>
              <RefreshCw size={13} className={isLoading ? "animate-spin" : ""} />
            </Button>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-6 space-y-3">
        {/* Summary */}
        {data && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-4">
            <Database size={14} />
            <span>{data.count} prediction{data.count !== 1 ? "s" : ""} found</span>
            <span className="text-zinc-700">·</span>
            <span>since {sinceDate}</span>
          </div>
        )}

        {isError && (
          <div className="flex items-center gap-2 text-amber-400 text-sm bg-amber-500/10 border border-amber-500/20 rounded-lg px-4 py-3">
            <AlertCircle size={16} />
            Could not load predictions. No ledger data yet, or server error.
          </div>
        )}

        {isLoading && (
          <div className="text-muted-foreground text-sm animate-pulse">Loading prediction history…</div>
        )}

        {!isLoading && preds.length === 0 && (
          <div className="text-center py-20 text-muted-foreground">
            <Database size={48} className="mx-auto mb-4 opacity-20" />
            <p className="font-medium">No predictions recorded yet.</p>
            <p className="text-sm mt-1">Write predictions via POST /wow/predictions after pipeline runs.</p>
          </div>
        )}

        {preds.map(pred => (
          <PredictionRow key={pred.prediction_id} pred={pred} />
        ))}
      </div>
    </div>
  );
}

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { FlaskConical, AlertCircle, TrendingUp, BarChart2, CheckCircle, Target } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface CalibrationBin {
  bin_lo: number;
  bin_hi: number;
  n: number;
  expected: number;
  observed: number;
  gap: number;
  brier: number;
  clv_mean: number | null;
}

interface CLBBand {
  band: string;
  lb_lo: number;
  lb_hi: number;
  n: number;
  hit_rate: number | null;
  claimed_min: number;
  reliable: boolean;
  gap: number | null;
}

interface SportRow {
  sport: string;
  n_predictions: number;
  n_settled: number;
  brier_mean: number | null;
  clv_mean: number | null;
  hit_rate: number | null;
}

interface LabelRow {
  terminal_label: string;
  n_predictions: number;
  n_settled: number;
  brier_mean: number | null;
  hit_rate: number | null;
}

interface BacktestResult {
  ok: boolean;
  mode: string;
  sport?: string | null;
  days: number;
  n?: number;
  bins?: CalibrationBin[];
  ece?: number | null;
  brier_mean?: number | null;
  bands?: CLBBand[];
  clb_65plus_rate?: number | null;
  clb_65plus_reliable?: boolean;
  sports?: SportRow[];
  labels?: LabelRow[];
  run_at?: string;
  error?: string;
  can_execute: false;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const pct = (v: number | null | undefined, d = 1) =>
  v != null ? `${(v * 100).toFixed(d)}%` : "—";

const num = (v: number | null | undefined, d = 4) =>
  v != null ? v.toFixed(d) : "—";

function MetricPill({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className={`bg-card border rounded-xl p-4 text-center ${good == null ? "border-border" : good ? "border-emerald-600/30" : "border-red-500/20"}`}>
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${good == null ? "text-foreground" : good ? "text-emerald-400" : "text-red-400"}`}>
        {value}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Calibration chart (CSS-based bar chart)
// ---------------------------------------------------------------------------
function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  if (!bins.length) return <div className="text-muted-foreground text-sm">No calibration bins.</div>;

  return (
    <div>
      <div className="text-xs text-muted-foreground mb-3">
        Calibration plot: expected (blue) vs observed (green) hit rate per probability bin.
        Perfect calibration = both bars equal height.
      </div>
      <div className="flex items-end gap-1 h-40 border-b border-border pb-1">
        {bins.map(bin => {
          const maxH = 100;
          const expH  = Math.round((bin.expected ?? 0) * maxH);
          const obsH  = Math.round((bin.observed ?? 0) * maxH);
          const good  = Math.abs(bin.gap ?? 0) < 0.05;
          return (
            <Tooltip key={bin.bin_lo}>
              <TooltipTrigger asChild>
                <div className="flex-1 flex items-end gap-0.5 cursor-help">
                  <div
                    className="flex-1 bg-blue-500/40 rounded-t-sm min-h-[2px]"
                    style={{ height: `${expH}%` }}
                  />
                  <div
                    className={`flex-1 rounded-t-sm min-h-[2px] ${good ? "bg-emerald-500/60" : "bg-amber-500/60"}`}
                    style={{ height: `${obsH}%` }}
                  />
                </div>
              </TooltipTrigger>
              <TooltipContent>
                <div className="text-xs space-y-0.5">
                  <div>Bin: {pct(bin.bin_lo)} – {pct(bin.bin_hi)}</div>
                  <div>Expected: {pct(bin.expected)}</div>
                  <div>Observed: {pct(bin.observed)}</div>
                  <div>Gap: {bin.gap != null ? (bin.gap > 0 ? "+" : "") + pct(bin.gap) : "—"}</div>
                  <div>n = {bin.n}</div>
                </div>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CLB reliability table
// ---------------------------------------------------------------------------
function CLBTable({ bands, clb65Rate, clb65Reliable }: {
  bands: CLBBand[];
  clb65Rate: number | null | undefined;
  clb65Reliable: boolean | undefined;
}) {
  return (
    <div className="space-y-2">
      <div className={`border rounded-lg px-4 py-3 text-sm flex items-center gap-2 ${
        clb65Reliable ? "border-emerald-600/30 bg-emerald-600/10 text-emerald-300"
                      : "border-amber-500/30 bg-amber-500/10 text-amber-300"
      }`}>
        {clb65Reliable ? <CheckCircle size={15} /> : <AlertCircle size={15} />}
        CLB≥65% overall hit rate: <strong>{pct(clb65Rate)}</strong>
        {" — "}{clb65Reliable ? "RELIABLE (≥65%)" : "BELOW 65% FLOOR"}
      </div>
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/30 text-muted-foreground text-left">
              {["Band", "CLB Range", "n", "Hit Rate", "Claimed Min", "Reliable", "Gap"].map(h => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {bands.map(b => (
              <tr key={b.band} className={b.reliable ? "" : "bg-amber-500/5"}>
                <td className="px-3 py-2 font-mono text-muted-foreground">{b.band}</td>
                <td className="px-3 py-2 tabular-nums">{pct(b.lb_lo)}–{pct(b.lb_hi)}</td>
                <td className="px-3 py-2 tabular-nums">{b.n}</td>
                <td className="px-3 py-2 tabular-nums font-semibold">{pct(b.hit_rate)}</td>
                <td className="px-3 py-2 tabular-nums text-muted-foreground">{pct(b.claimed_min)}</td>
                <td className="px-3 py-2">
                  {b.n > 0
                    ? b.reliable
                      ? <span className="text-emerald-400 flex items-center gap-1"><CheckCircle size={12} />YES</span>
                      : <span className="text-red-400 flex items-center gap-1"><AlertCircle size={12} />NO</span>
                    : <span className="text-zinc-600">—</span>
                  }
                </td>
                <td className={`px-3 py-2 tabular-nums ${b.gap != null && b.gap < 0 ? "text-amber-400" : "text-emerald-400"}`}>
                  {b.gap != null ? (b.gap > 0 ? "+" : "") + pct(b.gap) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sport slice table
// ---------------------------------------------------------------------------
function SportTable({ sports }: { sports: SportRow[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-muted/30 text-muted-foreground text-left">
            {["Sport", "Predictions", "Settled", "Hit Rate", "Brier Mean", "CLV Mean"].map(h => (
              <th key={h} className="px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {sports.map(s => (
            <tr key={s.sport} className="hover:bg-muted/20 transition-colors">
              <td className="px-3 py-2 font-semibold">{s.sport}</td>
              <td className="px-3 py-2 tabular-nums">{s.n_predictions}</td>
              <td className="px-3 py-2 tabular-nums">{s.n_settled}</td>
              <td className="px-3 py-2 tabular-nums">{pct(s.hit_rate)}</td>
              <td className={`px-3 py-2 tabular-nums ${(s.brier_mean ?? 1) < 0.25 ? "text-emerald-400" : "text-amber-400"}`}>
                {num(s.brier_mean)}
              </td>
              <td className={`px-3 py-2 tabular-nums ${(s.clv_mean ?? 0) > 0 ? "text-emerald-400" : "text-red-400"}`}>
                {s.clv_mean != null ? (s.clv_mean > 0 ? "+" : "") + pct(s.clv_mean) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Label audit table
// ---------------------------------------------------------------------------
function LabelTable({ labels }: { labels: LabelRow[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-muted/30 text-muted-foreground text-left">
            {["Label", "Predictions", "Settled", "Hit Rate", "Brier Mean"].map(h => (
              <th key={h} className="px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {labels.map(l => (
            <tr key={l.terminal_label} className="hover:bg-muted/20">
              <td className="px-3 py-2 font-mono text-[11px]">{l.terminal_label}</td>
              <td className="px-3 py-2 tabular-nums">{l.n_predictions}</td>
              <td className="px-3 py-2 tabular-nums">{l.n_settled}</td>
              <td className={`px-3 py-2 tabular-nums font-semibold ${(l.hit_rate ?? 0) >= 0.65 ? "text-emerald-400" : (l.hit_rate ?? 0) >= 0.5 ? "text-amber-400" : "text-red-400"}`}>
                {pct(l.hit_rate)}
              </td>
              <td className="px-3 py-2 tabular-nums">{num(l.brier_mean)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function BacktestingPage() {
  const { toast } = useToast();
  const [mode, setMode]   = useState("CALIBRATION");
  const [sport, setSport] = useState("all");
  const [days, setDays]   = useState("90");
  const [result, setResult] = useState<BacktestResult | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const body = {
        mode,
        days: parseInt(days, 10),
        sport: sport === "all" ? null : sport,
      };
      const res = await fetch(`${API_BASE}/wow/backtest/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<BacktestResult>;
    },
    onSuccess(data) {
      setResult(data);
      if (data.error) {
        toast({ title: "Backtest error", description: data.error, variant: "destructive" });
      } else {
        toast({ title: `Backtest complete (${data.mode})`, description: `n = ${data.n ?? "N/A"}` });
      }
    },
    onError(err: Error) {
      toast({ title: "Backtest failed", description: err.message, variant: "destructive" });
    },
  });

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border px-6 py-4">
        <div className="flex items-center justify-between max-w-5xl mx-auto">
          <div>
            <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
              <FlaskConical size={20} className="text-primary" />
              Backtesting
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Calibration, CLB reliability, sport slice, and label accuracy over settled predictions
            </p>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-6 space-y-6">
        {/* Controls */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Backtest Configuration</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3 items-end">
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Mode</div>
                <Select value={mode} onValueChange={setMode}>
                  <SelectTrigger className="w-48 h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CALIBRATION">Calibration</SelectItem>
                    <SelectItem value="CLB_RELIABILITY">CLB Reliability</SelectItem>
                    <SelectItem value="SPORT_SLICE">Sport Slice</SelectItem>
                    <SelectItem value="LABEL_AUDIT">Label Audit</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Sport filter</div>
                <Select value={sport} onValueChange={setSport}>
                  <SelectTrigger className="w-32 h-9 text-sm">
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
              </div>
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Lookback (days)</div>
                <Select value={days} onValueChange={setDays}>
                  <SelectTrigger className="w-24 h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="30">30</SelectItem>
                    <SelectItem value="60">60</SelectItem>
                    <SelectItem value="90">90</SelectItem>
                    <SelectItem value="180">180</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button onClick={() => mutate()} disabled={isPending} className="h-9">
                {isPending ? "Running…" : "Run Backtest"}
              </Button>
            </div>

            <div className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
              <AlertCircle size={12} />
              Backtesting requires settled predictions in the ledger. can_execute: false · dry-run only.
            </div>
          </CardContent>
        </Card>

        {/* Results */}
        {result && !result.error && (
          <div className="space-y-6">
            {/* Common metrics */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
              <BarChart2 size={13} />
              Mode: <strong>{result.mode}</strong> · {result.days}d window · {result.sport ?? "all sports"}
              {result.run_at && <span className="ml-auto">run at {new Date(result.run_at).toLocaleTimeString()}</span>}
            </div>

            {/* CALIBRATION */}
            {result.mode === "CALIBRATION" && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Calibration Analysis</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-3 gap-3">
                    <MetricPill label="Sample size" value={String(result.n ?? "—")} />
                    <MetricPill
                      label="ECE"
                      value={num(result.ece)}
                      good={result.ece != null && result.ece < 0.05}
                    />
                    <MetricPill
                      label="Brier Mean"
                      value={num(result.brier_mean)}
                      good={result.brier_mean != null && result.brier_mean < 0.25}
                    />
                  </div>
                  {result.bins && result.bins.length > 0 && (
                    <CalibrationChart bins={result.bins} />
                  )}
                </CardContent>
              </Card>
            )}

            {/* CLB_RELIABILITY */}
            {result.mode === "CLB_RELIABILITY" && result.bands && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">CLB Reliability Audit</CardTitle>
                </CardHeader>
                <CardContent>
                  <CLBTable
                    bands={result.bands}
                    clb65Rate={result.clb_65plus_rate}
                    clb65Reliable={result.clb_65plus_reliable}
                  />
                </CardContent>
              </Card>
            )}

            {/* SPORT_SLICE */}
            {result.mode === "SPORT_SLICE" && result.sports && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Per-Sport Breakdown</CardTitle>
                </CardHeader>
                <CardContent>
                  <SportTable sports={result.sports} />
                </CardContent>
              </Card>
            )}

            {/* LABEL_AUDIT */}
            {result.mode === "LABEL_AUDIT" && result.labels && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Label Accuracy Audit</CardTitle>
                </CardHeader>
                <CardContent>
                  <LabelTable labels={result.labels} />
                </CardContent>
              </Card>
            )}

            <p className="text-[11px] text-zinc-700 text-right">
              Backtesting is offline-only · no picks are executed · can_execute: false
            </p>
          </div>
        )}

        {result?.error && (
          <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
            <AlertCircle size={16} />
            {result.error}
          </div>
        )}

        {!result && !isPending && (
          <div className="text-center py-20 text-muted-foreground">
            <FlaskConical size={48} className="mx-auto mb-4 opacity-20" />
            <p className="font-medium">Configure and run a backtest above.</p>
            <p className="text-sm mt-1">Results require settled predictions in the prediction ledger.</p>
          </div>
        )}
      </div>
    </div>
  );
}

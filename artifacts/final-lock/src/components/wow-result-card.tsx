import { CheckCircle2, XCircle, AlertTriangle, TrendingUp, TrendingDown, Minus } from "lucide-react";

interface AnalyzeResult {
  player: string;
  sport: string;
  prop: string;
  direction: "MORE" | "LESS";
  line: number;
  league?: string | null;
  confidence: "high" | "medium" | "low";
}

interface GateMini {
  per_leg_breakeven: number;
  shrinkage_probability: number;
  edge_per_leg: number;
  gate_pass: boolean;
  decision: string;
}

interface SharpAnchor {
  anchor_status: string;
  our_side_prob?: number;
  reject: boolean;
  detail: string;
}

interface WowResultCardProps {
  result: AnalyzeResult;
  gate?: GateMini | null;
  sharpAnchor?: SharpAnchor | null;
  onSendToForm?: () => void;
}

function confBadge(c: string) {
  if (c === "high") return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
  if (c === "medium") return "bg-amber-500/20 text-amber-400 border-amber-500/30";
  return "bg-rose-500/20 text-rose-400 border-rose-500/30";
}

function anchorBadge(status: string) {
  if (status === "SHARP_ANCHOR_CONFIRMED") return "text-emerald-400";
  if (status.startsWith("REJECT") || status === "SHARP_ANCHOR_CONFLICT") return "text-rose-400";
  if (status === "MARKET_VERIFIED_HOLD_STALE") return "text-amber-400";
  return "text-muted-foreground";
}

export function WowResultCard({ result, gate, sharpAnchor, onSendToForm }: WowResultCardProps) {
  const dir = result.direction === "MORE" ? "MORE" : "LESS";

  return (
    <div className="rounded-2xl border border-border bg-card overflow-hidden shadow-lg">
      {/* Header */}
      <div className="px-5 py-4 border-b border-border bg-muted/20 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-bold text-foreground truncate">{result.player}</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-primary/20 text-primary font-medium">
              {result.sport}
            </span>
            {result.league && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground font-medium uppercase">
                {result.league}
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5 capitalize">
            {result.prop}
          </p>
        </div>
        <span className={`shrink-0 text-xs px-2 py-1 rounded-lg border font-semibold ${confBadge(result.confidence)}`}>
          {result.confidence.toUpperCase()}
        </span>
      </div>

      {/* Prop detail */}
      <div className="px-5 py-4 flex items-center gap-4">
        <div className="flex-1 flex items-center gap-3">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
            dir === "MORE" ? "bg-emerald-500/15" : "bg-rose-500/15"
          }`}>
            {dir === "MORE"
              ? <TrendingUp size={18} className="text-emerald-400" />
              : <TrendingDown size={18} className="text-rose-400" />
            }
          </div>
          <div>
            <p className={`text-xl font-bold font-mono ${
              dir === "MORE" ? "text-emerald-400" : "text-rose-400"
            }`}>
              {dir} {result.line}
            </p>
            <p className="text-xs text-muted-foreground capitalize">{result.prop}</p>
          </div>
        </div>

        {/* EV gate mini panel (only shown when model data available) */}
        {gate && (
          <div className={`shrink-0 rounded-xl p-3 border text-right ${
            gate.gate_pass
              ? "bg-emerald-500/10 border-emerald-500/25"
              : "bg-amber-500/10 border-amber-500/25"
          }`}>
            <p className={`text-xs font-bold mb-0.5 ${gate.gate_pass ? "text-emerald-400" : "text-amber-400"}`}>
              {gate.gate_pass ? "GATE PASS" : "HOLD"}
            </p>
            <p className="text-xs text-muted-foreground font-mono">
              {(gate.shrinkage_probability * 100).toFixed(1)}% shrink
            </p>
            <p className={`text-xs font-mono font-semibold ${
              gate.edge_per_leg >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}>
              {gate.edge_per_leg >= 0 ? "+" : ""}{(gate.edge_per_leg * 100).toFixed(2)}% edge
            </p>
          </div>
        )}
      </div>

      {/* Sharp anchor row */}
      {sharpAnchor && (
        <div className="px-5 pb-4">
          <div className="rounded-lg bg-muted/30 border border-border px-3 py-2">
            <div className="flex items-start gap-2">
              {sharpAnchor.reject
                ? <XCircle size={13} className="text-rose-400 shrink-0 mt-0.5" />
                : sharpAnchor.anchor_status === "SHARP_ANCHOR_CONFIRMED"
                  ? <CheckCircle2 size={13} className="text-emerald-400 shrink-0 mt-0.5" />
                  : <AlertTriangle size={13} className="text-amber-400 shrink-0 mt-0.5" />
              }
              <div className="min-w-0">
                <span className={`text-xs font-semibold ${anchorBadge(sharpAnchor.anchor_status)}`}>
                  {sharpAnchor.anchor_status.replace(/_/g, " ")}
                </span>
                {sharpAnchor.our_side_prob !== undefined && (
                  <span className="text-xs text-muted-foreground ml-2">
                    p(side)={((sharpAnchor.our_side_prob) * 100).toFixed(1)}%
                  </span>
                )}
                <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                  {sharpAnchor.detail}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Send to form */}
      {onSendToForm && (
        <div className="px-5 pb-4">
          <button
            onClick={onSendToForm}
            className="w-full py-2.5 rounded-xl border border-primary/40 text-primary text-sm font-semibold hover:bg-primary/10 transition-colors"
          >
            → Send to Final Lock Form
          </button>
        </div>
      )}

      {/* Disclaimer strip */}
      <div className="px-5 py-2.5 bg-muted/30 border-t border-border">
        <p className="text-xs text-muted-foreground">
          Extracted by Claude · <code className="font-mono text-rose-400/80">can_approve_bets: False</code> enforced
        </p>
      </div>
    </div>
  );
}

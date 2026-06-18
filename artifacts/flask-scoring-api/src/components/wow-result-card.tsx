import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Zap, Shield, Eye, AlertTriangle, Copy, Check,
  TrendingUp, TrendingDown, Activity, Info,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface WowResult {
  ok: boolean;
  player: string;
  sport: string;
  prop: string;
  direction: string;
  line: number;
  confidence_tier: string;
  source?: string;
  rows?: number;
  complete?: boolean;
  l5_avg?: number;
  l10_avg?: number;
  l5_hit_rate?: number;
  l10_hit_rate?: number;
  edge?: number;
  workflow_cap?: string;
  workflow_reasons?: string[];
  workflow_fields?: Record<string, unknown>;
}

export interface JfResult {
  ok: boolean;
  scored_props?: Array<{
    player: string;
    prop: string;
    jf_score?: number;
    jf_band?: string;
    jf_slip_eligible?: boolean;
    jf_breakdown?: Record<string, unknown>;
    wow_validated?: boolean;
  }>;
  slips?: unknown[];
}

interface WowResultCardProps {
  result: WowResult;
  jfResult?: JfResult | null;
  className?: string;
}

function tierMeta(tier: string) {
  if (tier.startsWith("FINAL LOCK")) {
    return {
      color: "#34d399",
      bg: "bg-emerald-500/10",
      border: "border-emerald-500/30",
      ring: "ring-emerald-500/25",
      text: "text-emerald-400",
      label: "FINAL LOCK ELIGIBLE",
      icon: Shield,
    };
  }
  if (tier.startsWith("CONDITIONAL")) {
    return {
      color: "#a78bfa",
      bg: "bg-violet-500/10",
      border: "border-violet-500/30",
      ring: "ring-violet-500/25",
      text: "text-violet-400",
      label: "CONDITIONAL — L5 ONLY",
      icon: Activity,
    };
  }
  if (tier.startsWith("WATCH")) {
    return {
      color: "#fbbf24",
      bg: "bg-amber-500/10",
      border: "border-amber-500/30",
      ring: "ring-amber-500/25",
      text: "text-amber-400",
      label: "WATCH / RESEARCH ONLY",
      icon: Eye,
    };
  }
  return {
    color: "#f87171",
    bg: "bg-rose-500/10",
    border: "border-rose-500/30",
    ring: "ring-rose-500/25",
    text: "text-rose-400",
    label: "REJECT — INSUFFICIENT DATA",
    icon: AlertTriangle,
  };
}

function tierToScore(tier: string): number {
  if (tier.startsWith("FINAL LOCK")) return 88;
  if (tier.startsWith("CONDITIONAL")) return 68;
  if (tier.startsWith("WATCH")) return 50;
  return 28;
}

function jfBandMeta(band: string) {
  if (band === "Premium JF Core") return { color: "#34d399", label: "Premium JF Core" };
  if (band === "JF Slip Eligible") return { color: "#a78bfa", label: "JF Eligible" };
  if (band === "Watch Only") return { color: "#fbbf24", label: "Watch Only" };
  return { color: "#94a3b8", label: band };
}

function formatPct(v: number | undefined): string {
  if (v == null) return "—";
  return (v * 100).toFixed(0) + "%";
}

function formatNum(v: number | undefined): string {
  if (v == null) return "—";
  return v.toFixed(1);
}

function WorkflowFlag({ flag }: { flag: string }) {
  const friendly: Record<string, { label: string; color: string }> = {
    "soccer-xi-unconfirmed":         { label: "XI Unconfirmed", color: "text-amber-400" },
    "soccer-high-variance-prop":     { label: "High Variance Prop", color: "text-amber-400" },
    "soccer-ref-context-missing":    { label: "Ref Context Missing", color: "text-amber-400" },
    "soccer-league-translation-risk":{ label: "League Translation Risk", color: "text-orange-400" },
    "soccer-minutes-risk":           { label: "Minutes Risk", color: "text-amber-400" },
    "starter-unconfirmed":           { label: "Starter Unconfirmed", color: "text-amber-400" },
    "weather-unchecked":             { label: "Weather Unchecked", color: "text-amber-400" },
    "no-statcast-data":              { label: "No Statcast Data", color: "text-rose-400" },
    "no-fangraphs-data":             { label: "No FanGraphs Data", color: "text-rose-400" },
  };
  const meta = friendly[flag] ?? { label: flag.replace(/-/g, " "), color: "text-muted-foreground" };
  return (
    <span className={cn("inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide", meta.color)}>
      <span className="w-1 h-1 rounded-full bg-current shrink-0" />
      {meta.label}
    </span>
  );
}

export default function WowResultCard({ result, jfResult, className }: WowResultCardProps) {
  const [copied, setCopied] = useState(false);
  const meta = tierMeta(result.confidence_tier ?? "");
  const score = tierToScore(result.confidence_tier ?? "");
  const TierIcon = meta.icon;
  const isMore = result.direction === "MORE";
  const circumference = 2 * Math.PI * 42;
  const dashOffset = circumference * (1 - score / 100);

  const jfProp = jfResult?.scored_props?.find(
    (p) => p.player?.toLowerCase() === result.player?.toLowerCase()
  ) ?? jfResult?.scored_props?.[0];

  const showJf = !!jfProp && (
    result.confidence_tier?.startsWith("FINAL LOCK") ||
    result.confidence_tier?.startsWith("CONDITIONAL")
  );

  function handleCopy() {
    const lines = [
      `WOW Analysis: ${result.player}`,
      `${result.direction} ${result.prop} ${result.line} — ${result.sport}`,
      `Tier: ${result.confidence_tier}`,
      result.source ? `Source: ${result.source}` : null,
      result.l5_hit_rate != null ? `L5 Hit Rate: ${formatPct(result.l5_hit_rate)}` : null,
      result.l10_hit_rate != null ? `L10 Hit Rate: ${formatPct(result.l10_hit_rate)}` : null,
      result.workflow_cap ? `Cap: ${result.workflow_cap}` : null,
      showJf && jfProp ? `JF: ${jfProp.jf_band} (${jfProp.jf_score?.toFixed(1)}/10)` : null,
    ].filter(Boolean).join("\n");
    navigator.clipboard.writeText(lines).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <motion.div
      data-testid="card-score-result"
      initial={{ opacity: 0, y: 24, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -12, scale: 0.97 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        "rounded-2xl border bg-card overflow-hidden",
        meta.border,
        className
      )}
    >
      {/* Tier color bar */}
      <motion.div
        className="h-1 w-full"
        style={{ background: meta.color }}
        initial={{ scaleX: 0, originX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ delay: 0.15, duration: 0.6, ease: "easeOut" }}
      />

      <div className="p-6">
        {/* Header row */}
        <div className="flex items-start justify-between gap-4 mb-6">
          <div className="flex-1 min-w-0">
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className={cn(
                "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-black uppercase tracking-widest mb-3",
                meta.bg, meta.text, "ring-1", meta.ring
              )}
            >
              <TierIcon size={11} />
              {meta.label}
            </motion.div>

            <h2 className="text-2xl font-black text-foreground leading-tight truncate">
              {result.player}
            </h2>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span className="text-sm text-muted-foreground font-medium">{result.sport}</span>
              <span className="text-muted-foreground/40">·</span>
              <span className="text-sm text-muted-foreground font-medium capitalize">{result.prop}</span>
              <span className="text-muted-foreground/40">·</span>
              <span
                className={cn(
                  "inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full",
                  isMore
                    ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/25"
                    : "bg-rose-500/15 text-rose-400 ring-1 ring-rose-500/25"
                )}
              >
                {isMore ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                {result.direction} {result.line}
              </span>
            </div>
          </div>

          {/* Score ring */}
          <div className="shrink-0 relative w-20 h-20">
            <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
              <circle cx="50" cy="50" r="42" fill="none" stroke="hsl(217 33% 14%)" strokeWidth="10" />
              <motion.circle
                cx="50" cy="50" r="42"
                fill="none"
                stroke={meta.color}
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={circumference}
                animate={{ strokeDashoffset: dashOffset }}
                transition={{ delay: 0.25, duration: 1.1, ease: "easeOut" }}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <motion.span
                className="text-xl font-black tabular-nums leading-none"
                style={{ color: meta.color }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.35 }}
              >
                {score}
              </motion.span>
              <span className="text-[9px] text-muted-foreground font-semibold uppercase tracking-wider mt-0.5">
                WOW
              </span>
            </div>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-4 gap-3 mb-4">
          {[
            { label: "L5 Hit", value: formatPct(result.l5_hit_rate) },
            { label: "L10 Hit", value: formatPct(result.l10_hit_rate) },
            { label: "L5 Avg", value: formatNum(result.l5_avg) },
            { label: "L10 Avg", value: formatNum(result.l10_avg) },
          ].map(({ label, value }) => (
            <div key={label} className="bg-muted/20 rounded-lg px-2 py-2 text-center">
              <p className="text-[9px] text-muted-foreground font-semibold uppercase tracking-wide mb-1">{label}</p>
              <p className="text-sm font-black text-foreground tabular-nums">{value}</p>
            </div>
          ))}
        </div>

        {/* Source + rows */}
        {(result.source || result.rows != null) && (
          <div className="flex items-center gap-3 mb-4 text-xs text-muted-foreground">
            {result.source && (
              <span className="flex items-center gap-1">
                <Info size={11} />
                {result.source}
              </span>
            )}
            {result.rows != null && (
              <span className="flex items-center gap-1">
                <Activity size={11} />
                {result.rows} game{result.rows !== 1 ? "s" : ""} sampled
              </span>
            )}
          </div>
        )}

        {/* Workflow cap */}
        {result.workflow_cap && (
          <div className="flex items-start gap-2 bg-amber-500/8 border border-amber-500/20 rounded-lg px-3 py-2 mb-4">
            <AlertTriangle size={13} className="text-amber-400 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-300/90 font-medium leading-relaxed">{result.workflow_cap}</p>
          </div>
        )}

        {/* Workflow flags */}
        {result.workflow_reasons && result.workflow_reasons.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-4">
            {result.workflow_reasons.map((f) => (
              <WorkflowFlag key={f} flag={f} />
            ))}
          </div>
        )}

        {/* JF verdict */}
        <AnimatePresence>
          {showJf && jfProp && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.3 }}
              className="mt-1 mb-4"
            >
              <div
                className="rounded-lg border px-4 py-3"
                style={{
                  borderColor: `${jfBandMeta(jfProp.jf_band ?? "").color}30`,
                  backgroundColor: `${jfBandMeta(jfProp.jf_band ?? "").color}08`,
                }}
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-0.5">
                      LLP / JF Engine
                    </p>
                    <p
                      className="text-sm font-black"
                      style={{ color: jfBandMeta(jfProp.jf_band ?? "").color }}
                    >
                      {jfBandMeta(jfProp.jf_band ?? "").label}
                    </p>
                  </div>
                  {jfProp.jf_score != null && (
                    <div className="text-right">
                      <p
                        className="text-2xl font-black tabular-nums"
                        style={{ color: jfBandMeta(jfProp.jf_band ?? "").color }}
                      >
                        {jfProp.jf_score.toFixed(1)}
                      </p>
                      <p className="text-[9px] text-muted-foreground font-semibold uppercase tracking-wider">/ 10</p>
                    </div>
                  )}
                </div>
                {jfProp.jf_breakdown && typeof jfProp.jf_breakdown === "object" && (
                  <div className="mt-2 grid grid-cols-3 gap-2">
                    {Object.entries(jfProp.jf_breakdown).slice(0, 3).map(([k, v]) => (
                      <div key={k} className="text-center">
                        <p className="text-[9px] text-muted-foreground uppercase tracking-wide">{k.replace(/_/g, " ")}</p>
                        <p className="text-xs font-bold text-foreground">{String(v)}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Copy button */}
        <motion.button
          whileTap={{ scale: 0.96 }}
          onClick={handleCopy}
          className="w-full flex items-center justify-center gap-2 text-xs font-semibold text-muted-foreground hover:text-foreground border border-border hover:border-border/80 rounded-lg py-2.5 transition-colors"
        >
          <AnimatePresence mode="wait">
            {copied ? (
              <motion.span
                key="check"
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                className="flex items-center gap-2 text-emerald-400"
              >
                <Check size={13} />
                Copied to clipboard
              </motion.span>
            ) : (
              <motion.span
                key="copy"
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                className="flex items-center gap-2"
              >
                <Copy size={13} />
                Copy result
              </motion.span>
            )}
          </AnimatePresence>
        </motion.button>
      </div>
    </motion.div>
  );
}

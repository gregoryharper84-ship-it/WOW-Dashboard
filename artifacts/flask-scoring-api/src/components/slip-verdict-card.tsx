import { useState } from "react";
import { motion } from "framer-motion";
import {
  Shield, Activity, Eye, AlertTriangle, TrendingUp, TrendingDown,
  CheckCircle2, XCircle, Info, Copy, Share2, Check,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface ScoredSlipLeg {
  player: string;
  sport: string;
  prop: string;
  side: string;
  line: number;
  confidence_tier: string;
  jf_score?: number;
  jf_band?: string;
  jf_slip_eligible?: boolean;
  source?: string;
  rows?: number;
}

export interface SlipShell {
  slip_size: number;
  payout_mult: number | null;
  available: boolean;
  avg_jf_score?: number;
  all_premium?: boolean;
  legs?: ScoredSlipLeg[];
  reason?: string;
  alternates?: Array<{
    avg_jf_score: number;
    all_premium: boolean;
    legs: ScoredSlipLeg[];
  }>;
}

export interface JfSlipVerdict {
  ok: boolean;
  scored_props?: ScoredSlipLeg[];
  slips?: Record<string, SlipShell>;
  counts?: {
    received: number;
    purged: number;
    scored: number;
    slip_eligible: number;
    by_band: Record<string, number>;
  };
}

function tierMeta(tier: string) {
  if (tier?.startsWith("FINAL LOCK")) return { color: "#34d399", text: "text-emerald-400", bg: "bg-emerald-500/10", icon: Shield,        label: "FINAL LOCK" };
  if (tier?.startsWith("CONDITIONAL")) return { color: "#a78bfa", text: "text-violet-400",  bg: "bg-violet-500/10",  icon: Activity,       label: "CONDITIONAL" };
  if (tier?.startsWith("WATCH"))       return { color: "#fbbf24", text: "text-amber-400",   bg: "bg-amber-500/10",   icon: Eye,            label: "WATCH" };
  return                                      { color: "#f87171", text: "text-rose-400",    bg: "bg-rose-500/10",    icon: AlertTriangle,  label: "REJECT" };
}

function jfBandColor(band?: string) {
  if (band === "Premium JF Core")  return "#34d399";
  if (band === "JF Slip Eligible") return "#a78bfa";
  if (band === "Watch Only")       return "#fbbf24";
  return "#94a3b8";
}

function sideIsMore(side?: string) {
  return (side ?? "").toUpperCase() === "MORE";
}

const SLIP_ORDER = ["Conservative", "Standard", "Flex"];

function buildSlipText(verdict: JfSlipVerdict): string {
  const lines: string[] = [];
  const scored = verdict.scored_props ?? [];
  const slips = verdict.slips ?? {};
  const date = new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

  lines.push(`🎯 JF Slip Report — ${date}`);
  lines.push("");

  if (scored.length > 0) {
    lines.push(`Scored Legs (${scored.length}):`);
    scored.forEach((p, i) => {
      const tier = (() => {
        if (p.confidence_tier?.startsWith("FINAL LOCK"))  return "FINAL LOCK";
        if (p.confidence_tier?.startsWith("CONDITIONAL")) return "CONDITIONAL";
        if (p.confidence_tier?.startsWith("WATCH"))       return "WATCH";
        return "REJECT";
      })();
      const score = p.jf_score != null ? ` | JF ${p.jf_score.toFixed(1)}` : "";
      const band  = p.jf_band ? ` | ${p.jf_band}` : "";
      const elig  = p.jf_slip_eligible ? " ✓" : " ✗";
      lines.push(`${i + 1}. ${p.player} — ${p.side?.toUpperCase()} ${p.line} ${p.prop} (${p.sport}) | ${tier}${score}${band}${elig}`);
    });
    lines.push("");
  }

  lines.push("Slip Recommendations:");
  SLIP_ORDER.forEach((label) => {
    const slip = slips[label];
    if (!slip) return;
    if (!slip.available) {
      lines.push(`${label}: N/A — ${slip.reason ?? "not available"}`);
      return;
    }
    const legs = (slip.legs ?? []).map((l) => l.player).join(" + ");
    const avg  = slip.avg_jf_score != null ? ` | avg JF ${slip.avg_jf_score.toFixed(1)}` : "";
    const mult = slip.payout_mult   != null ? ` | ${slip.payout_mult}×`                   : "";
    const prem = slip.all_premium ? " | All Premium JF Core" : "";
    lines.push(`${label} (${slip.slip_size}-leg): ${legs}${avg}${mult}${prem}`);
  });

  lines.push("");
  lines.push("Powered by WOW v16 Engine");
  return lines.join("\n");
}

interface SlipVerdictCardProps {
  verdict: JfSlipVerdict;
}

export default function SlipVerdictCard({ verdict }: SlipVerdictCardProps) {
  const slips = verdict.slips ?? {};
  const counts = verdict.counts;
  const scored = verdict.scored_props ?? [];

  const [copied, setCopied] = useState(false);
  const canShare = typeof navigator !== "undefined" && !!navigator.share;

  const handleCopy = async () => {
    const text = buildSlipText(verdict);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const el = document.createElement("textarea");
      el.value = text;
      el.style.position = "fixed";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      document.body.removeChild(el);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleShare = async () => {
    if (!canShare) return;
    try {
      await navigator.share({ text: buildSlipText(verdict) });
    } catch {
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
      className="space-y-4"
    >
      {/* Copy / Share action bar */}
      <div className="flex items-center justify-end gap-2">
        {canShare && (
          <button
            onClick={handleShare}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border border-border bg-card text-muted-foreground hover:text-foreground hover:border-border/80 transition-colors"
          >
            <Share2 size={12} />
            Share
          </button>
        )}
        <motion.button
          onClick={handleCopy}
          whileTap={{ scale: 0.95 }}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors",
            copied
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
              : "border-border bg-card text-muted-foreground hover:text-foreground hover:border-border/80"
          )}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "Copied!" : "Copy slip"}
        </motion.button>
      </div>

      {/* Band summary */}
      {counts && (
        <div className="rounded-xl border border-border bg-card px-4 py-3">
          <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-2">
            Pool Summary — {counts.scored} prop{counts.scored !== 1 ? "s" : ""} scored
          </p>
          <div className="flex flex-wrap gap-3">
            {Object.entries(counts.by_band).map(([band, n]) => (
              <div key={band} className="flex items-center gap-1.5">
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: jfBandColor(band) }}
                />
                <span className="text-xs font-semibold text-foreground">{n}</span>
                <span className="text-[10px] text-muted-foreground">{band}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Scored props compact list */}
      {scored.length > 0 && (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border">
            <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">
              Scored Legs
            </p>
          </div>
          <div className="divide-y divide-border">
            {scored.map((p, i) => {
              const tm = tierMeta(p.confidence_tier ?? "");
              const TierIcon = tm.icon;
              const isMore = sideIsMore(p.side);
              const bandColor = jfBandColor(p.jf_band);
              return (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05 }}
                  className="flex items-center gap-3 px-4 py-3"
                >
                  <div className={cn("flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold shrink-0", tm.bg, tm.text)}>
                    <TierIcon size={9} />
                    {tm.label}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-foreground truncate">{p.player}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {p.prop} · {p.sport}
                    </p>
                  </div>
                  <div className={cn(
                    "flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0",
                    isMore ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"
                  )}>
                    {isMore ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
                    {p.side} {p.line}
                  </div>
                  {p.jf_score != null && (
                    <div className="text-right shrink-0">
                      <p className="text-sm font-black tabular-nums" style={{ color: bandColor }}>
                        {p.jf_score.toFixed(1)}
                      </p>
                      <p className="text-[9px] text-muted-foreground">/ 10</p>
                    </div>
                  )}
                  <div className="shrink-0">
                    {p.jf_slip_eligible
                      ? <CheckCircle2 size={14} className="text-emerald-400" />
                      : <XCircle size={14} className="text-muted-foreground/40" />}
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      )}

      {/* Slip recommendations */}
      <div className="space-y-3">
        {SLIP_ORDER.map((label) => {
          const slip = slips[label];
          if (!slip) return null;
          return <SlipCard key={label} label={label} slip={slip} />;
        })}
      </div>
    </motion.div>
  );
}

function SlipCard({ label, slip }: { label: string; slip: SlipShell }) {
  if (!slip) return null;

  const isAvailable = slip.available;
  const labelColor = label === "Conservative" ? "#34d399" : label === "Standard" ? "#a78bfa" : "#fbbf24";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className={cn(
        "rounded-xl border bg-card overflow-hidden",
        isAvailable ? "border-border" : "border-border/50 opacity-60"
      )}
    >
      {isAvailable && (
        <div className="h-0.5 w-full" style={{ background: labelColor }} />
      )}
      <div className="px-4 py-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <p className="text-sm font-black" style={{ color: isAvailable ? labelColor : undefined }}>
              {label}
            </p>
            <span className="text-[10px] text-muted-foreground font-medium">
              {slip.slip_size}-leg
            </span>
            {slip.payout_mult != null && (
              <span className="text-[10px] font-bold text-muted-foreground">
                {slip.payout_mult}×
              </span>
            )}
          </div>
          {isAvailable && slip.avg_jf_score != null && (
            <div className="text-right">
              <span className="text-xs font-black tabular-nums" style={{ color: labelColor }}>
                {slip.avg_jf_score.toFixed(1)}
              </span>
              <span className="text-[9px] text-muted-foreground"> avg JF</span>
            </div>
          )}
        </div>

        {!isAvailable && (
          <div className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <Info size={11} className="mt-0.5 shrink-0" />
            <span>{slip.reason}</span>
          </div>
        )}

        {isAvailable && slip.legs && slip.legs.length > 0 && (
          <div className="space-y-1.5 mt-2">
            {slip.legs.map((leg, i) => {
              const isMore = sideIsMore(leg.side);
              const bandColor = jfBandColor(leg.jf_band);
              return (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-lg bg-muted/20 px-2.5 py-1.5"
                >
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-bold text-foreground truncate block">{leg.player}</span>
                    <span className="text-[10px] text-muted-foreground">{leg.prop} · {leg.sport}</span>
                  </div>
                  <div className={cn(
                    "flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0",
                    isMore ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"
                  )}>
                    {isMore ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
                    {leg.side} {leg.line}
                  </div>
                  {leg.jf_score != null && (
                    <span className="text-xs font-black tabular-nums shrink-0" style={{ color: bandColor }}>
                      {leg.jf_score.toFixed(1)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {isAvailable && slip.all_premium && (
          <div className="mt-2 flex items-center gap-1 text-[10px] font-bold text-emerald-400">
            <Shield size={10} />
            All Premium JF Core
          </div>
        )}
      </div>
    </motion.div>
  );
}

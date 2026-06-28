import { useState, useCallback, useEffect, useRef, Fragment } from "react";
import {
  TrendingUp, RefreshCw, CheckCircle2, XCircle, AlertTriangle,
  ChevronDown, BarChart2, FileText, Activity, X, Loader2, Clock
} from "lucide-react";

const API_KEY = import.meta.env.VITE_SCORING_API_KEY || "";

const CATEGORIES = ["sports", "weather", "macro", "politics", "news", "narrative", "crypto", "other"];

const FAILURE_TAGS = [
  "MODEL_MISS",
  "SETTLEMENT_AMBIGUOUS",
  "LIQUIDITY_DRIED",
  "FEE_DRAG",
  "EDGE_EVAPORATED",
  "MARKET_MOVED",
  "DATA_ERROR",
  "OTHER",
];

type LabelKind =
  | "KALSHI_FINAL_APPROVED"
  | "KALSHI_PLAYABLE_LIMIT_ONLY"
  | "KALSHI_WATCH"
  | "KALSHI_SCOUT"
  | "KALSHI_REJECT_NO_EDGE"
  | "KALSHI_REJECT_BAD_RULES"
  | "KALSHI_REJECT_THIN_BOOK"
  | "KALSHI_REJECT_FEE_DRAG"
  | "KALSHI_REJECT_UNCALIBRATED"
  | "KALSHI_DATA_UNOBTAINABLE"
  | string;

type Panel = "evaluate" | "ledger" | "calibration";

interface EvalResult {
  ticker: string;
  side: string;
  label: LabelKind;
  adjusted_edge: number | null;
  raw_edge: number | null;
  current_price: number | null;
  max_playable_price: number | null;
  liquidity_grade: string | null;
  settlement_grade: string | null;
  settlement_risk: string | null;
  market_bucket: string | null;
  blocking_reasons: string[];
  warnings: string[];
  execution: string | null;
  can_approve_bets: boolean;
  model_probability?: number | null;
  category?: string;
}

interface LedgerRow {
  id: number;
  created_at: string;
  updated_at: string | null;
  market_ticker: string;
  event_ticker: string | null;
  contract_title: string | null;
  category: string | null;
  side_yes_no: string;
  model_probability: number | null;
  confidence_low: number | null;
  confidence_high: number | null;
  kalshi_price: number | null;
  entry_price: number | null;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  depth_score: string | null;
  fee_estimate: number | null;
  adjusted_edge: number | null;
  max_playable_price: number | null;
  label: string;
  market_bucket: string | null;
  settlement_source: string | null;
  settlement_grade: string | null;
  settlement_status: string;
  closing_price: number | null;
  result: string | null;
  brier_score: number | null;
  clv: number | null;
  net_pnl: number | null;
  dominant_failure_tag: string | null;
  notes: string | null;
  mode: string | null;
  blocking_reasons: string[] | null;
  warnings: string[] | null;
}

interface CalibrationSummary {
  total_settled: number | null;
  mean_brier_score: number | null;
  mean_clv: number | null;
  total_pnl: number | null;
  yes_wins: number | null;
  no_wins: number | null;
  voids: number | null;
  can_approve_bets: boolean;
  error?: string;
}

function labelColor(label: LabelKind): string {
  if (label === "KALSHI_PLAYABLE_LIMIT_ONLY") return "bg-emerald-900/60 text-emerald-300 border border-emerald-700";
  if (label === "KALSHI_FINAL_APPROVED") return "bg-emerald-900/60 text-emerald-300 border border-emerald-700";
  if (label === "KALSHI_WATCH") return "bg-amber-900/60 text-amber-300 border border-amber-700";
  if (label === "KALSHI_SCOUT") return "bg-blue-900/60 text-blue-300 border border-blue-700";
  if (label.startsWith("KALSHI_REJECT")) return "bg-red-900/60 text-red-300 border border-red-700";
  if (label === "KALSHI_DATA_UNOBTAINABLE") return "bg-zinc-800 text-zinc-400 border border-zinc-700";
  return "bg-zinc-800 text-zinc-400 border border-zinc-700";
}

function isReject(label: LabelKind): boolean {
  return label.startsWith("KALSHI_REJECT") || label === "KALSHI_DATA_UNOBTAINABLE";
}

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function fmt4(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(4);
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "SETTLED"
      ? "bg-emerald-900/60 text-emerald-300 border border-emerald-700"
      : status === "VOIDED"
      ? "bg-zinc-800 text-zinc-400 border border-zinc-700"
      : "bg-amber-900/60 text-amber-300 border border-amber-700";
  return <span className={`inline-block text-xs px-2 py-0.5 rounded font-mono ${cls}`}>{status}</span>;
}

function LabelBadge({ label }: { label: LabelKind }) {
  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded font-mono ${labelColor(label)}`}>
      {label.replace("KALSHI_", "")}
    </span>
  );
}

// ── Evaluate & Log Panel ──────────────────────────────────────────────────────

function EvaluatePanel() {
  const [ticker, setTicker] = useState("");
  const [modelProb, setModelProb] = useState("");
  const [side, setSide] = useState("YES");
  const [category, setCategory] = useState("sports");
  const [loading, setLoading] = useState(false);
  const [logging, setLogging] = useState(false);
  const [evalResult, setEvalResult] = useState<EvalResult | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);
  const [logResult, setLogResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleEvaluate = async () => {
    if (!ticker.trim()) return;
    setLoading(true);
    setEvalResult(null);
    setEvalError(null);
    setLogResult(null);
    try {
      const prob = modelProb.trim() ? parseFloat(modelProb) : null;
      const res = await fetch("/kalshi/evaluate-contract", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
        body: JSON.stringify({
          ticker: ticker.trim(),
          side,
          model_probability: prob,
          category,
        }),
      });
      const data = await res.json();
      if (data.error && !data.label) {
        setEvalError(data.error);
      } else {
        setEvalResult({ ...data, model_probability: prob, category });
      }
    } catch (e) {
      setEvalError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleLog = async () => {
    if (!evalResult) return;
    setLogging(true);
    setLogResult(null);
    try {
      const res = await fetch("/kalshi/paper-trade", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
        body: JSON.stringify({
          ticker: evalResult.ticker,
          side: evalResult.side,
          model_probability: evalResult.model_probability,
          entry_price: evalResult.current_price,
          adjusted_edge: evalResult.adjusted_edge,
          label: evalResult.label,
          liquidity_grade: evalResult.liquidity_grade,
          settlement_grade: evalResult.settlement_grade,
          market_bucket: evalResult.market_bucket,
          category: evalResult.category,
          blocking_reasons: evalResult.blocking_reasons,
          warnings: evalResult.warnings,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setLogResult({ ok: true, message: data.detail ?? "Logged." });
      } else {
        setLogResult({ ok: false, message: data.detail ?? data.blocks?.join("; ") ?? "Failed." });
      }
    } catch (e) {
      setLogResult({ ok: false, message: String(e) });
    } finally {
      setLogging(false);
    }
  };

  const canLog =
    evalResult !== null &&
    !isReject(evalResult.label) &&
    logResult?.ok !== true;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="col-span-2 sm:col-span-2">
          <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
            Ticker
          </label>
          <input
            className="w-full bg-input border border-border rounded px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary font-mono"
            placeholder="e.g. KXNBATOT-25JUN27-T225.5"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleEvaluate(); }}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
            Model Prob
          </label>
          <input
            className="w-full bg-input border border-border rounded px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            placeholder="0.62"
            type="number"
            min="0"
            max="1"
            step="0.01"
            value={modelProb}
            onChange={(e) => setModelProb(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
            Side
          </label>
          <select
            className="w-full bg-input border border-border rounded px-3 py-2 text-sm text-foreground focus:outline-none"
            value={side}
            onChange={(e) => setSide(e.target.value)}
          >
            <option value="YES">YES</option>
            <option value="NO">NO</option>
          </select>
        </div>
      </div>

      <div className="flex items-end gap-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
            Category
          </label>
          <select
            className="w-full bg-input border border-border rounded px-3 py-2 text-sm text-foreground focus:outline-none"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <button
          onClick={handleEvaluate}
          disabled={loading || !ticker.trim()}
          className="px-5 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/80 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? (
            <span className="flex items-center gap-2"><RefreshCw size={14} className="animate-spin" /> Evaluating…</span>
          ) : (
            "Evaluate"
          )}
        </button>
      </div>

      {evalError && (
        <div className="bg-red-900/30 border border-red-700 rounded p-3 text-sm text-red-300">
          {evalError}
        </div>
      )}

      {evalResult && (
        <div className="bg-card border border-border rounded-lg p-4 space-y-4">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <p className="text-xs text-muted-foreground font-mono mb-1">{evalResult.ticker}</p>
              <LabelBadge label={evalResult.label} />
            </div>
            <div className="text-right">
              {evalResult.market_bucket && (
                <span className="text-xs px-2 py-0.5 rounded bg-blue-900/40 text-blue-300 border border-blue-700 font-mono">
                  {evalResult.market_bucket}
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <Stat label="Adj Edge" value={pct(evalResult.adjusted_edge)} highlight={
              evalResult.adjusted_edge != null && evalResult.adjusted_edge > 0 ? "green" : "red"
            } />
            <Stat label="Raw Edge" value={pct(evalResult.raw_edge)} />
            <Stat label="Entry Price" value={fmt4(evalResult.current_price)} />
            <Stat label="Max Playable" value={fmt4(evalResult.max_playable_price)} />
            <Stat label="Liq Grade" value={evalResult.liquidity_grade ?? "—"} />
            <Stat label="Settle Grade" value={evalResult.settlement_grade ?? "—"} />
            <Stat label="Risk" value={evalResult.settlement_risk ?? "—"} />
            <Stat label="Side" value={evalResult.side} />
          </div>

          {evalResult.blocking_reasons.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Blocking Reasons</p>
              {evalResult.blocking_reasons.map((r, i) => (
                <p key={i} className="text-xs text-red-300 font-mono">{r}</p>
              ))}
            </div>
          )}

          {evalResult.warnings.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Warnings</p>
              {evalResult.warnings.map((w, i) => (
                <p key={i} className="text-xs text-amber-300 font-mono">{w}</p>
              ))}
            </div>
          )}

          {isReject(evalResult.label) ? (
            <div className="flex items-center gap-2 text-sm text-red-400">
              <XCircle size={14} />
              <span>Rejected label — cannot log to ledger</span>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <button
                onClick={handleLog}
                disabled={!canLog || logging}
                className="px-4 py-2 rounded bg-emerald-700 text-white text-sm font-medium hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {logging ? (
                  <span className="flex items-center gap-2"><RefreshCw size={13} className="animate-spin" /> Logging…</span>
                ) : (
                  "Confirm & Log Paper Trade"
                )}
              </button>
              {logResult && (
                <span className={`text-sm flex items-center gap-1 ${logResult.ok ? "text-emerald-400" : "text-red-400"}`}>
                  {logResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                  {logResult.message}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, highlight }: { label: string; value: string; highlight?: "green" | "red" }) {
  const valColor = highlight === "green" ? "text-emerald-300" : highlight === "red" ? "text-red-300" : "text-foreground";
  return (
    <div className="bg-muted/30 rounded p-2">
      <p className="text-xs text-muted-foreground mb-0.5">{label}</p>
      <p className={`text-sm font-mono font-medium ${valColor}`}>{value}</p>
    </div>
  );
}

// ── Settlement Modal ──────────────────────────────────────────────────────────

function SettleModal({
  row,
  onClose,
  onSettled,
  initialClosingPrice,
  initialResult,
}: {
  row: LedgerRow;
  onClose: () => void;
  onSettled: () => void;
  initialClosingPrice?: string;
  initialResult?: "YES" | "NO" | "VOID";
}) {
  const [result, setResult] = useState<"YES" | "NO" | "VOID">(initialResult ?? "YES");
  const [closingPrice, setClosingPrice] = useState(initialClosingPrice ?? "");
  const [clvInput, setClvInput] = useState("");
  const [failureTag, setFailureTag] = useState("");
  const [notes, setNotes] = useState(row.notes ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const closingPriceNum = closingPrice.trim() ? parseFloat(closingPrice) : NaN;
  const closingPriceOutOfRange =
    closingPrice.trim() !== "" && !isNaN(closingPriceNum) && (closingPriceNum < 0 || closingPriceNum > 1);

  const computedClv = (() => {
    if (clvInput.trim()) return parseFloat(clvInput);
    const cp = closingPriceNum;
    const ep = row.entry_price ?? 0;
    if (isNaN(cp)) return null;
    if (result === "YES") return parseFloat((cp - ep).toFixed(4));
    if (result === "NO") return parseFloat(((1 - cp) - (1 - ep)).toFixed(4));
    return null;
  })();

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/kalshi/settle-result", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
        body: JSON.stringify({
          id: row.id,
          result,
          closing_price: closingPrice ? parseFloat(closingPrice) : null,
          clv: computedClv,
          dominant_failure_tag: failureTag || null,
          notes: notes || null,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        onSettled(); // caller (LedgerPanel) is responsible for advancing queue and closing modal
      } else {
        setError(data.detail ?? data.error ?? "Failed");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-md mx-4 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-muted-foreground font-mono">Settle</p>
            <p className="text-sm font-mono font-medium">{row.market_ticker}</p>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X size={16} />
          </button>
        </div>

        <div className="grid grid-cols-3 gap-2">
          {(["YES", "NO", "VOID"] as const).map((r) => (
            <button
              key={r}
              onClick={() => setResult(r)}
              className={`py-2 rounded text-sm font-medium border transition-colors ${
                result === r
                  ? r === "YES"
                    ? "bg-emerald-700 border-emerald-600 text-white"
                    : r === "NO"
                    ? "bg-red-800 border-red-700 text-white"
                    : "bg-zinc-700 border-zinc-600 text-white"
                  : "bg-muted border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {r}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Closing Price</label>
            <input
              className={`w-full bg-input border rounded px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary ${
                closingPriceOutOfRange ? "border-amber-500 focus:ring-amber-500" : "border-border"
              }`}
              placeholder="0.71"
              type="number"
              step="0.01"
              value={closingPrice}
              onChange={(e) => setClosingPrice(e.target.value)}
            />
            {closingPriceOutOfRange && (
              <p className="mt-1 text-xs text-amber-400 flex items-center gap-1">
                <AlertTriangle size={11} className="shrink-0" />
                Kalshi prices are 0–1 (e.g. 0.71, not 71)
              </p>
            )}
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">
              CLV {computedClv != null && !clvInput.trim() && (
                <span className="text-emerald-400 ml-1">(auto: {computedClv.toFixed(4)})</span>
              )}
            </label>
            <input
              className="w-full bg-input border border-border rounded px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="auto-computed"
              type="number"
              step="0.001"
              value={clvInput}
              onChange={(e) => setClvInput(e.target.value)}
            />
          </div>
        </div>

        <div>
          <label className="block text-xs text-muted-foreground mb-1">Failure Tag (optional)</label>
          <select
            className="w-full bg-input border border-border rounded px-2 py-1.5 text-sm text-foreground focus:outline-none"
            value={failureTag}
            onChange={(e) => setFailureTag(e.target.value)}
          >
            <option value="">None</option>
            {FAILURE_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        <div>
          <label className="block text-xs text-muted-foreground mb-1">Notes</label>
          <textarea
            className="w-full bg-input border border-border rounded px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-none"
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded text-sm text-muted-foreground hover:text-foreground border border-border"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || closingPriceOutOfRange}
            className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/80 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? "Submitting…" : "Record Settlement"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Ledger Panel ─────────────────────────────────────────────────────────────

function LedgerDetailRow({ row }: { row: LedgerRow }) {
  const isSettled = row.settlement_status === "SETTLED";

  return (
    <tr>
      <td colSpan={12} className="px-3 pb-3 pt-0">
        <div className="bg-muted/30 border border-border/60 rounded-lg p-4 space-y-4">

          {/* Header identifiers */}
          <div className="flex items-start gap-4 flex-wrap">
            <div>
              <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">Full Ticker</p>
              <p className="text-xs font-mono text-foreground">{row.market_ticker}</p>
            </div>
            {row.event_ticker && (
              <div>
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">Event Ticker</p>
                <p className="text-xs font-mono text-foreground">{row.event_ticker}</p>
              </div>
            )}
            {row.contract_title && (
              <div className="flex-1 min-w-0">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">Contract Title</p>
                <p className="text-xs text-foreground">{row.contract_title}</p>
              </div>
            )}
          </div>

          {/* Core evaluation fields */}
          <div>
            <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-2">Evaluation Detail</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <MiniStat label="Model Prob" value={pct(row.model_probability)} />
              <MiniStat label="Market Bucket" value={row.market_bucket ?? "—"} />
              <MiniStat label="Settlement Grade" value={row.settlement_grade ?? "—"} />
              <MiniStat label="Depth Score" value={row.depth_score ?? "—"} />
              <MiniStat label="Category" value={row.category ?? "—"} />
              <MiniStat label="Side" value={row.side_yes_no} />
              <MiniStat label="Mode" value={row.mode ?? "paper"} />
              {row.dominant_failure_tag && (
                <MiniStat label="Failure Tag" value={row.dominant_failure_tag} highlight="red" />
              )}
            </div>
          </div>

          {/* Market data at entry */}
          <div>
            <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-2">Market Data at Entry</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <MiniStat label="Entry Price" value={fmt4(row.entry_price)} />
              <MiniStat label="Kalshi Price" value={fmt4(row.kalshi_price)} />
              <MiniStat label="Best Bid" value={fmt4(row.best_bid)} />
              <MiniStat label="Best Ask" value={fmt4(row.best_ask)} />
              <MiniStat label="Spread" value={fmt4(row.spread)} />
              <MiniStat label="Fee Est." value={fmt4(row.fee_estimate)} />
              <MiniStat label="Adj Edge" value={pct(row.adjusted_edge)} highlight={
                row.adjusted_edge != null ? (row.adjusted_edge > 0 ? "green" : "red") : undefined
              } />
              <MiniStat label="Max Playable" value={fmt4(row.max_playable_price)} />
            </div>
          </div>

          {/* Confidence interval if available */}
          {(row.confidence_low != null || row.confidence_high != null) && (
            <div className="grid grid-cols-2 gap-2 max-w-xs">
              <MiniStat label="Conf Low" value={pct(row.confidence_low)} />
              <MiniStat label="Conf High" value={pct(row.confidence_high)} />
            </div>
          )}

          {/* Settlement outcome (settled rows only) */}
          {isSettled && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-2">Settlement Outcome</p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <MiniStat label="Result" value={row.result ?? "—"} highlight={
                  row.result === "YES" ? "green" : row.result === "NO" ? "red" : undefined
                } />
                <MiniStat label="Closing Price" value={fmt4(row.closing_price)} />
                <MiniStat label="CLV" value={fmt4(row.clv)} highlight={
                  row.clv != null ? (row.clv >= 0 ? "green" : "red") : undefined
                } />
                <MiniStat label="Brier Score" value={row.brier_score != null ? row.brier_score.toFixed(5) : "—"} highlight={
                  row.brier_score != null ? (row.brier_score < 0.25 ? "green" : "red") : undefined
                } />
                {row.net_pnl != null && (
                  <MiniStat label="Net PnL" value={fmt4(row.net_pnl)} highlight={row.net_pnl >= 0 ? "green" : "red"} />
                )}
              </div>
            </div>
          )}

          {/* Settlement source */}
          {row.settlement_source && (
            <div>
              <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">Settlement Source</p>
              <p className="text-xs font-mono text-muted-foreground">{row.settlement_source}</p>
            </div>
          )}

          {/* Blocking reasons */}
          {row.blocking_reasons && row.blocking_reasons.length > 0 && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5">Blocking Reasons</p>
              <div className="space-y-1">
                {row.blocking_reasons.map((r, i) => (
                  <p key={i} className="text-xs text-red-300 font-mono bg-red-900/20 rounded px-2 py-1">{r}</p>
                ))}
              </div>
            </div>
          )}

          {/* Warnings */}
          {row.warnings && row.warnings.length > 0 && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5">Warnings</p>
              <div className="space-y-1">
                {row.warnings.map((w, i) => (
                  <p key={i} className="text-xs text-amber-300 font-mono bg-amber-900/20 rounded px-2 py-1">{w}</p>
                ))}
              </div>
            </div>
          )}

          {/* Notes */}
          {row.notes && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">Notes</p>
              <p className="text-xs text-foreground whitespace-pre-wrap">{row.notes}</p>
            </div>
          )}

          {/* Timestamps */}
          <div className="flex gap-4 flex-wrap text-[10px] text-muted-foreground border-t border-border/40 pt-2">
            <span>Logged: {fmtDate(row.created_at)}</span>
            {row.updated_at && row.updated_at !== row.created_at && (
              <span>Updated: {fmtDate(row.updated_at)}</span>
            )}
            <span>ID: #{row.id}</span>
          </div>
        </div>
      </td>
    </tr>
  );
}

function MiniStat({ label, value, highlight }: { label: string; value: string; highlight?: "green" | "red" }) {
  const valColor = highlight === "green" ? "text-emerald-300" : highlight === "red" ? "text-red-300" : "text-foreground";
  return (
    <div className="bg-black/20 rounded p-1.5">
      <p className="text-[10px] text-muted-foreground mb-0.5">{label}</p>
      <p className={`text-xs font-mono font-medium ${valColor}`}>{value}</p>
    </div>
  );
}

// Tracks a market that Kalshi has marked settled/closed — closing price pre-filled
interface MarketCheck {
  kalshiStatus: string;           // e.g. "settled", "closed"
  lastPrice: number | null;       // last_price from market_meta → closing price hint
  closeTime: string | null;
}

function LedgerPanel() {
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<"ALL" | "OPEN" | "SETTLED">("ALL");
  const [settlingRow, setSettlingRow] = useState<LedgerRow | null>(null);
  const [settlePreFill, setSettlePreFill] = useState<{ closingPrice: string } | undefined>();
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Auto-settlement scanner state
  const [readyMap, setReadyMap] = useState<Map<number, MarketCheck>>(new Map());
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  // Ref avoids putting `scanning` in useCallback deps (prevents stale-closure dep-cycle)
  const scanningRef = useRef(false);

  // Bulk-settle queue: rows to settle one-by-one with pre-filled closing price
  const [settleQueue, setSettleQueue] = useState<Array<{ row: LedgerRow; preFill: { closingPrice: string } }>>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setExpandedId(null);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (filter !== "ALL") params.set("settlement_status", filter);
      const res = await fetch(`/kalshi/ledger?${params}`, {
        headers: { "X-API-Key": API_KEY },
      });
      const data = await res.json();
      setRows(data.records ?? []);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  // Scan OPEN rows: call /kalshi/evaluate-contract per ticker, check market_meta.status.
  // Uses a ref to gate concurrent runs so `scanning` state isn't in the dep array
  // (which would cause a stale-closure dep-cycle via useEffect → useCallback → useEffect).
  const scanOpenRows = useCallback(async (openRows: LedgerRow[]) => {
    if (!openRows.length || scanningRef.current) return;
    scanningRef.current = true;
    setScanning(true);
    setScanError(null);
    const results = new Map<number, MarketCheck>();
    const BATCH = 3;
    for (let i = 0; i < openRows.length; i += BATCH) {
      const batch = openRows.slice(i, i + BATCH);
      await Promise.allSettled(
        batch.map(async (row) => {
          try {
            const res = await fetch("/kalshi/evaluate-contract", {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
              body: JSON.stringify({ ticker: row.market_ticker, use_live_book: true }),
            });
            if (!res.ok) return;
            const data = await res.json();
            const rawStatus: string = (data.market_meta?.status ?? "").toLowerCase();
            if (rawStatus === "settled" || rawStatus === "closed" || rawStatus === "finalized") {
              results.set(row.id, {
                kalshiStatus: rawStatus,
                lastPrice: data.market_meta?.last_price ?? null,
                closeTime: data.market_meta?.close_time ?? null,
              });
            }
          } catch { /* ignore per-row network errors */ }
        })
      );
    }
    setReadyMap(results);
    scanningRef.current = false;
    setScanning(false);
  }, []); // intentionally empty deps — guarded by scanningRef

  // Auto-scan: whenever rows reload, kick off a background check on open rows
  useEffect(() => {
    if (loading) return; // wait for load to finish
    const open = rows.filter((r) => r.settlement_status === "OPEN");
    if (open.length > 0) {
      scanOpenRows(open);
    }
  }, [rows, loading, scanOpenRows]);

  const openRows = rows.filter((r) => r.settlement_status === "OPEN");
  const readyRows = openRows.filter((r) => readyMap.has(r.id));

  // Build a settle queue from all ready rows and open the first modal
  const handleSettleAllReady = () => {
    const queue = readyRows.map((row) => {
      const check = readyMap.get(row.id)!;
      const price = check.lastPrice != null ? check.lastPrice.toFixed(4) : "";
      return { row, preFill: { closingPrice: price } };
    });
    if (!queue.length) return;
    const [first, ...rest] = queue;
    setSettleQueue(rest);
    setSettlingRow(first.row);
    setSettlePreFill(first.preFill);
  };

  // After each settled row, pop the next from the queue
  const handleSettled = () => {
    load();
    if (settleQueue.length > 0) {
      const [next, ...rest] = settleQueue;
      setSettleQueue(rest);
      setSettlingRow(next.row);
      setSettlePreFill(next.preFill);
    } else {
      setSettlingRow(null);
      setSettlePreFill(undefined);
    }
  };

  const handleCloseModal = () => {
    setSettlingRow(null);
    setSettlePreFill(undefined);
    setSettleQueue([]);
  };

  return (
    <div className="space-y-3">
      {/* ── Header bar ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex gap-1">
          {(["ALL", "OPEN", "SETTLED"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                filter === f
                  ? "bg-primary/20 text-primary border border-primary/30"
                  : "text-muted-foreground border border-border hover:text-foreground"
              }`}
            >
              {f}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Settle All Ready — only visible when scanner found expired rows */}
          {readyRows.length > 0 && (
            <button
              onClick={handleSettleAllReady}
              className="flex items-center gap-1.5 text-xs font-medium border border-amber-600 text-amber-300 bg-amber-900/20 rounded px-2 py-1 hover:bg-amber-900/40 transition-colors"
            >
              <Clock size={12} />
              Settle All Ready ({readyRows.length})
            </button>
          )}

          {/* Scan button */}
          <button
            onClick={() => scanOpenRows(openRows)}
            disabled={scanning || openRows.length === 0}
            title={openRows.length === 0 ? "No open rows to scan" : "Check Kalshi for expired markets"}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 transition-colors disabled:opacity-40"
          >
            {scanning
              ? <Loader2 size={12} className="animate-spin" />
              : <Clock size={12} />}
            {scanning ? "Scanning…" : `Scan Expired (${openRows.length})`}
          </button>

          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 transition-colors"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {/* Scanner summary */}
      {!scanning && readyMap.size > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 rounded border border-amber-700/50 bg-amber-900/10 text-xs text-amber-300">
          <Clock size={12} />
          <span>
            {readyRows.length} open row{readyRows.length !== 1 ? "s" : ""} flagged as expired by Kalshi.
            {readyRows.length > 0 && ' Click \u201cSettle All Ready\u201d or settle individually.'}
          </span>
        </div>
      )}
      {scanError && (
        <p className="text-xs text-red-400">{scanError}</p>
      )}

      {/* ── Table ──────────────────────────────────────────────────────────── */}
      {loading ? (
        <div className="py-12 text-center text-muted-foreground text-sm">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground text-sm">No ledger rows found.</div>
      ) : (
        <div className="overflow-x-auto">
          <p className="text-[10px] text-muted-foreground mb-2 italic">Click any row to expand full evaluation detail.</p>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left pb-2 pr-3 font-medium w-4"></th>
                <th className="text-left pb-2 pr-3 font-medium">Ticker</th>
                <th className="text-left pb-2 pr-3 font-medium">Label</th>
                <th className="text-left pb-2 pr-3 font-medium">Side</th>
                <th className="text-right pb-2 pr-3 font-medium">Adj Edge</th>
                <th className="text-right pb-2 pr-3 font-medium">Entry</th>
                <th className="text-left pb-2 pr-3 font-medium">Liq Grade</th>
                <th className="text-left pb-2 pr-3 font-medium">Settle Grade</th>
                <th className="text-left pb-2 pr-3 font-medium">Status</th>
                <th className="text-left pb-2 pr-3 font-medium">CLV</th>
                <th className="text-left pb-2 pr-3 font-medium">Logged</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isExpanded = expandedId === row.id;
                const readyCheck = readyMap.get(row.id);
                const isReady = row.settlement_status === "OPEN" && !!readyCheck;

                return (
                  <Fragment key={row.id}>
                    <tr
                      onClick={() => setExpandedId(isExpanded ? null : row.id)}
                      className={`border-b transition-colors cursor-pointer ${
                        isReady
                          ? "border-amber-800/40 bg-amber-900/10 hover:bg-amber-900/20"
                          : isExpanded
                          ? "border-border/50 bg-muted/30"
                          : "border-border/50 hover:bg-muted/20"
                      }`}
                    >
                      <td className="py-2 pr-1 text-muted-foreground">
                        <ChevronDown
                          size={12}
                          className={`transition-transform ${isExpanded ? "rotate-180 text-primary" : ""}`}
                        />
                      </td>
                      <td className="py-2 pr-3 font-mono text-foreground max-w-[160px] truncate" title={row.market_ticker}>
                        {row.market_ticker}
                      </td>
                      <td className="py-2 pr-3">
                        <LabelBadge label={row.label} />
                      </td>
                      <td className="py-2 pr-3 font-mono">{row.side_yes_no}</td>
                      <td className="py-2 pr-3 text-right font-mono">
                        <span className={
                          row.adjusted_edge != null && row.adjusted_edge > 0
                            ? "text-emerald-400"
                            : "text-red-400"
                        }>
                          {pct(row.adjusted_edge)}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">{fmt4(row.entry_price)}</td>
                      <td className="py-2 pr-3 font-mono">{row.depth_score ?? "—"}</td>
                      <td className="py-2 pr-3 font-mono">{row.settlement_grade ?? "—"}</td>
                      <td className="py-2 pr-3">
                        <div className="flex items-center gap-1.5">
                          <StatusBadge status={row.settlement_status} />
                          {isReady && (
                            <span className="flex items-center gap-0.5 text-[10px] font-medium text-amber-300 bg-amber-900/40 border border-amber-700/50 rounded px-1 py-0.5 whitespace-nowrap">
                              <Clock size={9} />
                              Needs Settlement
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-2 pr-3 font-mono">
                        {row.clv != null ? (
                          <span className={row.clv >= 0 ? "text-emerald-400" : "text-red-400"}>
                            {fmt4(row.clv)}
                          </span>
                        ) : "—"}
                      </td>
                      <td className="py-2 pr-3 text-muted-foreground whitespace-nowrap">
                        {fmtDate(row.created_at)}
                      </td>
                      <td className="py-2" onClick={(e) => e.stopPropagation()}>
                        {row.settlement_status === "OPEN" && (
                          isReady ? (
                            <button
                              onClick={() => {
                                const price = readyCheck.lastPrice != null
                                  ? readyCheck.lastPrice.toFixed(4)
                                  : "";
                                setSettlePreFill({ closingPrice: price });
                                setSettlingRow(row);
                              }}
                              className="flex items-center gap-1 px-2 py-1 rounded text-xs border border-amber-600 text-amber-200 bg-amber-900/30 hover:bg-amber-900/50 transition-colors whitespace-nowrap font-medium"
                            >
                              <Clock size={10} />
                              Settle ✓
                            </button>
                          ) : (
                            <button
                              onClick={() => {
                                setSettlePreFill(undefined);
                                setSettlingRow(row);
                              }}
                              className="px-2 py-1 rounded text-xs border border-amber-700 text-amber-300 hover:bg-amber-900/30 transition-colors whitespace-nowrap"
                            >
                              Settle
                            </button>
                          )
                        )}
                      </td>
                    </tr>
                    {isExpanded && <LedgerDetailRow row={row} />}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {settlingRow && (
        <SettleModal
          key={settlingRow.id}
          row={settlingRow}
          onClose={handleCloseModal}
          onSettled={handleSettled}
          initialClosingPrice={settlePreFill?.closingPrice}
        />
      )}
    </div>
  );
}

// ── Calibration Summary Panel ─────────────────────────────────────────────────

function CalibrationPanel() {
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<CalibrationSummary | null>(null);
  const [settled, setSettled] = useState<LedgerRow[]>([]);
  const [totalOpen, setTotalOpen] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sumRes, settledRes, openRes] = await Promise.all([
        fetch("/kalshi/ledger?summary=true&limit=1", { headers: { "X-API-Key": API_KEY } }),
        fetch("/kalshi/ledger?settlement_status=SETTLED&limit=500", { headers: { "X-API-Key": API_KEY } }),
        fetch("/kalshi/ledger?settlement_status=OPEN&limit=1", { headers: { "X-API-Key": API_KEY } }),
      ]);
      const sumData = await sumRes.json();
      const settledData = await settledRes.json();
      const openData = await openRes.json();

      setSummary(sumData.calibration_summary ?? null);
      setSettled(settledData.records ?? []);
      setTotalOpen(openData.count ?? 0);
    } catch {
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const n = summary?.total_settled ?? 0;
  const brier = summary?.mean_brier_score;
  const meanClv = summary?.mean_clv;
  const threshold = 25;

  const gateClvOk = meanClv != null && meanClv > 0;
  const gateBrierOk = brier != null && brier < 0.25;
  const gateCountOk = n >= threshold;
  const milestoneGreen = gateCountOk && gateClvOk && gateBrierOk;
  const milestoneRed = gateCountOk && (!gateClvOk || !gateBrierOk);

  // Per-bucket CLV + Brier
  const bucketMap: Record<string, { count: number; clvSum: number; wins: number; brierSum: number; brierCount: number }> = {};
  for (const row of settled) {
    const b = row.market_bucket ?? "UNKNOWN";
    if (!bucketMap[b]) bucketMap[b] = { count: 0, clvSum: 0, wins: 0, brierSum: 0, brierCount: 0 };
    bucketMap[b].count++;
    if (row.clv != null) bucketMap[b].clvSum += row.clv;
    if (row.result === "YES") bucketMap[b].wins++;
    if (row.brier_score != null) {
      bucketMap[b].brierSum += row.brier_score;
      bucketMap[b].brierCount++;
    }
  }

  // Failure tag freq
  const tagMap: Record<string, number> = {};
  for (const row of settled) {
    if (row.dominant_failure_tag) {
      tagMap[row.dominant_failure_tag] = (tagMap[row.dominant_failure_tag] ?? 0) + 1;
    }
  }
  const sortedTags = Object.entries(tagMap).sort((a, b) => b[1] - a[1]);
  const maxTagCount = sortedTags[0]?.[1] ?? 1;

  if (loading) {
    return <div className="py-12 text-center text-muted-foreground text-sm">Loading calibration data…</div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex justify-end">
        <button
          onClick={load}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 transition-colors"
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {/* Milestone Gate Card */}
      <div className={`border rounded-lg p-4 ${
        milestoneGreen
          ? "border-emerald-700 bg-emerald-900/20"
          : milestoneRed
          ? "border-red-700 bg-red-900/20"
          : "border-border bg-muted/20"
      }`}>
        <div className="flex items-center gap-2 mb-3">
          {milestoneGreen ? (
            <CheckCircle2 size={16} className="text-emerald-400" />
          ) : milestoneRed ? (
            <XCircle size={16} className="text-red-400" />
          ) : (
            <AlertTriangle size={16} className="text-amber-400" />
          )}
          <span className="text-sm font-medium">
            {milestoneGreen
              ? "Milestone 2 Gate: PASSED"
              : milestoneRed
              ? "Milestone 2 Gate: FAILED"
              : "Calibration Incomplete"}
          </span>
        </div>

        <div className="grid grid-cols-3 gap-3 text-sm mb-4">
          <GateCheck
            label={`Settled (${n}/${threshold})`}
            ok={gateCountOk}
            note={gateCountOk ? "≥25 tracked" : `${threshold - n} more needed`}
          />
          <GateCheck
            label="Mean CLV"
            ok={gateClvOk}
            note={meanClv != null ? fmt4(meanClv) : "No data"}
          />
          <GateCheck
            label="Brier Score"
            ok={gateBrierOk}
            note={brier != null ? brier.toFixed(5) : "No data"}
          />
        </div>

        {/* Per-bucket breakdown required by Milestone 2 */}
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1.5">
            Per-Bucket Breakdown
          </p>
          <div className="bg-black/20 rounded overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b border-border/60">
                  <th className="text-left p-2 font-medium">Bucket</th>
                  <th className="text-right p-2 font-medium">Settled</th>
                  <th className="text-right p-2 font-medium">Mean CLV</th>
                  <th className="text-right p-2 font-medium">Mean Brier</th>
                  <th className="text-left p-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {(["TRUSTED_TEST", "WATCH", "TEST_ONLY"] as const).map((bucket) => {
                  const stats = bucketMap[bucket];
                  if (!stats || stats.count === 0) {
                    return (
                      <tr key={bucket} className="border-b border-border/30">
                        <td className="p-2 font-mono font-medium">{bucket}</td>
                        <td className="p-2 text-right text-muted-foreground">0</td>
                        <td className="p-2 text-right text-muted-foreground">—</td>
                        <td className="p-2 text-right text-muted-foreground">—</td>
                        <td className="p-2 text-amber-400/70 italic">No data yet</td>
                      </tr>
                    );
                  }
                  const insufficient = stats.count < 5;
                  const meanBucketClv = stats.clvSum / stats.count;
                  const meanBucketBrier = stats.brierCount > 0 ? stats.brierSum / stats.brierCount : null;
                  return (
                    <tr key={bucket} className="border-b border-border/30">
                      <td className="p-2 font-mono font-medium">{bucket}</td>
                      <td className="p-2 text-right">{stats.count}</td>
                      <td className={`p-2 text-right font-mono ${insufficient ? "text-muted-foreground" : meanBucketClv >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {insufficient ? "—" : fmt4(meanBucketClv)}
                      </td>
                      <td className={`p-2 text-right font-mono ${insufficient ? "text-muted-foreground" : meanBucketBrier == null ? "text-muted-foreground" : meanBucketBrier < 0.25 ? "text-emerald-400" : "text-red-400"}`}>
                        {insufficient ? "—" : meanBucketBrier != null ? meanBucketBrier.toFixed(5) : "—"}
                      </td>
                      <td className={`p-2 text-xs italic ${insufficient ? "text-amber-400" : "text-muted-foreground"}`}>
                        {insufficient ? `insufficient data (${stats.count}/5)` : "ok"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Overview stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Total Settled" value={String(n)} />
        <Stat label="Open Positions" value={String(totalOpen)} />
        <Stat label="Mean CLV" value={meanClv != null ? fmt4(meanClv) : "—"} highlight={gateClvOk ? "green" : meanClv != null ? "red" : undefined} />
        <Stat label="Mean Brier" value={brier != null ? brier.toFixed(5) : "—"} highlight={gateBrierOk ? "green" : brier != null ? "red" : undefined} />
        <Stat label="YES Wins" value={String(summary?.yes_wins ?? "—")} />
        <Stat label="NO Wins" value={String(summary?.no_wins ?? "—")} />
        <Stat label="Voids" value={String(summary?.voids ?? "—")} />
        <Stat label="Total PnL" value={summary?.total_pnl != null ? fmt4(summary.total_pnl) : "—"} highlight={
          summary?.total_pnl != null ? (summary.total_pnl >= 0 ? "green" : "red") : undefined
        } />
      </div>

      {/* TRUSTED_TEST vs WATCH vs TEST_ONLY comparison — required for Milestone 2 gate */}
      <div>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
          TRUSTED_TEST vs WATCH vs TEST_ONLY — CLV &amp; Brier Comparison
        </p>
        <div className="bg-muted/20 rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left p-2 font-medium">Bucket</th>
                <th className="text-right p-2 font-medium">Settled</th>
                <th className="text-right p-2 font-medium">Mean CLV</th>
                <th className="text-right p-2 font-medium">Mean Brier</th>
                <th className="text-right p-2 font-medium">Win Rate</th>
                <th className="text-left p-2 font-medium">Signal</th>
              </tr>
            </thead>
            <tbody>
              {(["TRUSTED_TEST", "WATCH", "TEST_ONLY"] as const).map((bucket) => {
                const stats = bucketMap[bucket];
                if (!stats) {
                  return (
                    <tr key={bucket} className="border-b border-border/50">
                      <td className="p-2 font-mono">{bucket}</td>
                      <td className="p-2 text-right text-muted-foreground">0</td>
                      <td className="p-2 text-right text-muted-foreground">—</td>
                      <td className="p-2 text-right text-muted-foreground">—</td>
                      <td className="p-2 text-right text-muted-foreground">—</td>
                      <td className="p-2 text-muted-foreground text-xs">No data yet</td>
                    </tr>
                  );
                }
                const insufficient = stats.count < 5;
                const meanBucketClv = stats.count > 0 ? stats.clvSum / stats.count : 0;
                const meanBucketBrier = stats.brierCount > 0 ? stats.brierSum / stats.brierCount : null;
                const winRate = stats.count > 0 ? stats.wins / stats.count : 0;
                const signal =
                  insufficient ? "⚠ Insuff. data" :
                  meanBucketClv > 0.02 ? "✓ Positive edge" :
                  meanBucketClv > 0 ? "~ Marginal" :
                  "✗ Negative CLV";
                const signalColor =
                  insufficient ? "text-amber-400" :
                  meanBucketClv > 0.02 ? "text-emerald-400" :
                  meanBucketClv > 0 ? "text-amber-400" :
                  "text-red-400";
                return (
                  <tr key={bucket} className="border-b border-border/50">
                    <td className="p-2 font-mono font-medium">{bucket}</td>
                    <td className="p-2 text-right">{stats.count}</td>
                    <td className={`p-2 text-right font-mono ${insufficient ? "text-muted-foreground" : meanBucketClv >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {insufficient ? "—" : fmt4(meanBucketClv)}
                    </td>
                    <td className={`p-2 text-right font-mono ${insufficient ? "text-muted-foreground" : meanBucketBrier == null ? "text-muted-foreground" : meanBucketBrier < 0.25 ? "text-emerald-400" : "text-red-400"}`}>
                      {insufficient ? "—" : meanBucketBrier != null ? meanBucketBrier.toFixed(5) : "—"}
                    </td>
                    <td className="p-2 text-right font-mono">{insufficient ? "—" : pct(winRate)}</td>
                    <td className={`p-2 text-xs ${signalColor}`}>{signal}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-muted-foreground mt-1.5">
          TRUSTED_TEST should show highest CLV and lowest Brier; buckets with &lt;5 samples show "—" (insufficient data).
        </p>
      </div>

      {/* Full bucket CLV table (all buckets) */}
      {Object.keys(bucketMap).length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">All Buckets — CLV &amp; Brier Detail</p>
          <div className="bg-muted/20 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left p-2 font-medium">Bucket</th>
                  <th className="text-right p-2 font-medium">Count</th>
                  <th className="text-right p-2 font-medium">Mean CLV</th>
                  <th className="text-right p-2 font-medium">Mean Brier</th>
                  <th className="text-right p-2 font-medium">Win Rate</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(bucketMap).map(([bucket, stats]) => {
                  const insufficient = stats.count < 5;
                  const meanBucketClv = stats.count > 0 ? stats.clvSum / stats.count : 0;
                  const meanBucketBrier = stats.brierCount > 0 ? stats.brierSum / stats.brierCount : null;
                  const winRate = stats.count > 0 ? stats.wins / stats.count : 0;
                  return (
                    <tr key={bucket} className="border-b border-border/50">
                      <td className="p-2 font-mono">
                        {bucket}
                        {insufficient && (
                          <span className="ml-1.5 text-amber-400/80 text-[10px] italic">insuff. data</span>
                        )}
                      </td>
                      <td className="p-2 text-right">{stats.count}</td>
                      <td className={`p-2 text-right font-mono ${insufficient ? "text-muted-foreground" : meanBucketClv >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {insufficient ? "—" : fmt4(meanBucketClv)}
                      </td>
                      <td className={`p-2 text-right font-mono ${insufficient ? "text-muted-foreground" : meanBucketBrier == null ? "text-muted-foreground" : meanBucketBrier < 0.25 ? "text-emerald-400" : "text-red-400"}`}>
                        {insufficient ? "—" : meanBucketBrier != null ? meanBucketBrier.toFixed(5) : "—"}
                      </td>
                      <td className="p-2 text-right font-mono">{insufficient ? "—" : pct(winRate)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Failure tag chart */}
      {sortedTags.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">Failure Tag Frequency</p>
          <div className="space-y-1.5">
            {sortedTags.map(([tag, count]) => (
              <div key={tag} className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted-foreground w-40 truncate shrink-0">{tag}</span>
                <div className="flex-1 h-3 bg-muted/30 rounded overflow-hidden">
                  <div
                    className="h-full bg-amber-600/70 rounded transition-all"
                    style={{ width: `${(count / maxTagCount) * 100}%` }}
                  />
                </div>
                <span className="text-xs text-muted-foreground w-6 text-right">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {settled.length === 0 && (
        <div className="py-8 text-center text-muted-foreground text-sm">
          No settled records yet. Evaluate and log paper trades, then settle them to build calibration data.
        </div>
      )}
    </div>
  );
}

function GateCheck({ label, ok, note }: { label: string; ok: boolean; note: string }) {
  return (
    <div className={`rounded p-2 border ${ok ? "border-emerald-700 bg-emerald-900/20" : "border-red-700/50 bg-red-900/10"}`}>
      <div className="flex items-center gap-1 mb-0.5">
        {ok ? <CheckCircle2 size={11} className="text-emerald-400" /> : <XCircle size={11} className="text-red-400" />}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <p className="text-xs text-muted-foreground font-mono">{note}</p>
    </div>
  );
}

// ── Main Kalshi Page ──────────────────────────────────────────────────────────

export default function KalshiPage() {
  const [panel, setPanel] = useState<Panel>("evaluate");

  const panels: { id: Panel; label: string; icon: typeof TrendingUp }[] = [
    { id: "evaluate", label: "Evaluate & Log", icon: TrendingUp },
    { id: "ledger",   label: "Ledger",         icon: FileText },
    { id: "calibration", label: "Calibration", icon: BarChart2 },
  ];

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Banner */}
      <div className="bg-amber-900/30 border-b border-amber-700/50 px-6 py-2 flex items-center gap-2">
        <Activity size={14} className="text-amber-400 shrink-0" />
        <span className="text-xs text-amber-300 font-medium">
          Paper trade only — live trading is permanently disabled
        </span>
      </div>

      <div className="max-w-5xl mx-auto px-4 py-6 space-y-5">
        {/* Header */}
        <div>
          <h1 className="text-lg font-semibold tracking-tight flex items-center gap-2">
            <TrendingUp size={18} className="text-primary" />
            Kalshi Calibration
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Paper-trade pipeline · Milestone 2 calibration ledger
          </p>
        </div>

        {/* Panel tabs */}
        <div className="flex gap-1 border-b border-border pb-0">
          {panels.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setPanel(id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                panel === id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>

        {/* Panel content */}
        <div className="bg-card border border-border rounded-xl p-5">
          {panel === "evaluate" && <EvaluatePanel />}
          {panel === "ledger" && <LedgerPanel />}
          {panel === "calibration" && <CalibrationPanel />}
        </div>
      </div>
    </div>
  );
}

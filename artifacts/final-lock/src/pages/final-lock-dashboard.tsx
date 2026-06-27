import { useState, useEffect, useCallback } from "react";
import { Lock, Zap, History, RefreshCw, CheckCircle2, XCircle, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";

const API_KEY = import.meta.env.VITE_SCORING_API_KEY || "";

const BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

// PrizePicks payout formats with breakeven per-leg probabilities (from payout_context.py)
const SLIP_FORMATS: Record<string, { breakeven: number; legs: number; label: string }> = {
  "2-pick Power": { breakeven: 0.578, legs: 2, label: "2-Pick Power (3.00×)" },
  "3-pick Power": { breakeven: 0.642, legs: 3, label: "3-Pick Power (3.78×)" },
  "4-pick Power": { breakeven: 0.679, legs: 4, label: "4-Pick Power (4.70×)" },
  "5-pick Power": { breakeven: 0.710, legs: 5, label: "5-Pick Power (9.80×)" },
  "6-pick Power": { breakeven: 0.735, legs: 6, label: "6-Pick Power (24.0×)" },
  "3-pick Flex":  { breakeven: 0.555, legs: 3, label: "3-Pick Flex (partial)" },
  "4-pick Flex":  { breakeven: 0.565, legs: 4, label: "4-Pick Flex (partial)" },
  "5-pick Flex":  { breakeven: 0.575, legs: 5, label: "5-Pick Flex (partial)" },
  "6-pick Flex":  { breakeven: 0.580, legs: 6, label: "6-Pick Flex (partial)" },
};

// Payout multipliers matching the breakeven table (1/be^n)
const PAYOUT_MULT: Record<string, number> = {
  "2-pick Power": 3.00,
  "3-pick Power": 3.78,
  "4-pick Power": 4.70,
  "5-pick Power": 9.80,
  "6-pick Power": 24.0,
  "3-pick Flex":  2.19,
  "4-pick Flex":  2.77,
  "5-pick Flex":  5.37,
  "6-pick Flex":  12.75,
};

interface FormData {
  player: string;
  team: string;
  opponent: string;
  sport: string;
  league: string;
  market: string;
  side: string;
  slip_type: string;
  pick_count: number;
  pp_line: string;
  pp_payout: string;
  injury_status: string;
  teammate_status: string;
  correlation_flag: boolean;
  sb_comp_line: string;
  sb_no_vig_prob: string;
  proj_source1: string;
  proj_source2: string;
  model_probability: string;
  shrinkage_probability: string;
  environment: string;
  notes: string;
}

interface GateResult {
  per_leg_breakeven: number;
  shrinkage_probability: number;
  slip_probability: number;
  breakeven_slip_prob: number;
  estimated_slip_ev: number;
  edge_per_leg: number;
  gate_pass: boolean;
  decision: "FINAL_APPROVED" | "MODEL_QUALIFIED_HOLD";
}

interface LockRecord {
  id: number;
  created_at: string;
  player: string;
  team: string | null;
  opponent: string | null;
  market: string;
  side: string;
  slip_type: string | null;
  pick_count: number | null;
  pp_line: number | null;
  model_probability: number | null;
  shrinkage_probability: number | null;
  slip_probability: number | null;
  estimated_slip_ev: number | null;
  final_lock_decision: string;
  sport: string | null;
  league: string | null;
}

const EMPTY_FORM: FormData = {
  player: "",
  team: "",
  opponent: "",
  sport: "",
  league: "",
  market: "",
  side: "Over",
  slip_type: "3-pick Power",
  pick_count: 3,
  pp_line: "",
  pp_payout: "",
  injury_status: "Healthy",
  teammate_status: "Full",
  correlation_flag: false,
  sb_comp_line: "",
  sb_no_vig_prob: "",
  proj_source1: "",
  proj_source2: "",
  model_probability: "",
  shrinkage_probability: "",
  environment: "live",
  notes: "",
};

function computeGate(form: FormData): GateResult | null {
  const shrinkProb = parseFloat(form.shrinkage_probability);
  if (isNaN(shrinkProb) || shrinkProb <= 0) return null;

  const fmt = SLIP_FORMATS[form.slip_type];
  if (!fmt) return null;

  const payout = parseFloat(form.pp_payout) || PAYOUT_MULT[form.slip_type] || 3.0;
  const legs = fmt.legs;

  const slipProb = Math.pow(shrinkProb, legs);
  const beSlip = 1.0 / payout;
  const ev = slipProb * payout - 1.0;
  const edgePerLeg = shrinkProb - fmt.breakeven;
  const gatePass = shrinkProb >= fmt.breakeven;

  return {
    per_leg_breakeven: fmt.breakeven,
    shrinkage_probability: shrinkProb,
    slip_probability: slipProb,
    breakeven_slip_prob: beSlip,
    estimated_slip_ev: ev,
    edge_per_leg: edgePerLeg,
    gate_pass: gatePass,
    decision: gatePass ? "FINAL_APPROVED" : "MODEL_QUALIFIED_HOLD",
  };
}

function pct(v: number, decimals = 1) {
  return `${(v * 100).toFixed(decimals)}%`;
}

function fmt2(v: number) {
  return v >= 0 ? `+${(v * 100).toFixed(2)}%` : `${(v * 100).toFixed(2)}%`;
}

// ─── Field components ───────────────────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
      {children}
    </label>
  );
}

function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  step,
  min,
  max,
  className = "",
}: {
  value: string | number;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  step?: string;
  min?: string;
  max?: string;
  className?: string;
}) {
  return (
    <input
      type={type}
      step={step}
      min={min}
      max={max}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={`w-full rounded-md border border-input bg-muted px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${className}`}
    />
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-input bg-muted px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors appearance-none"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <div className="h-px flex-1 bg-border" />
      <span className="text-xs font-semibold text-muted-foreground uppercase tracking-widest px-2">
        {children}
      </span>
      <div className="h-px flex-1 bg-border" />
    </div>
  );
}

// ─── History table ───────────────────────────────────────────────────────────

function HistoryTable({ locks }: { locks: LockRecord[] }) {
  if (locks.length === 0)
    return (
      <p className="text-center text-muted-foreground text-sm py-8">
        No locks saved yet.
      </p>
    );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border">
            {["#", "Time", "Player", "Market", "Side", "Format", "Shrink%", "Slip EV", "Decision"].map(
              (h) => (
                <th
                  key={h}
                  className="text-left py-2 px-2 text-muted-foreground font-medium uppercase tracking-wider whitespace-nowrap"
                >
                  {h}
                </th>
              )
            )}
          </tr>
        </thead>
        <tbody>
          {locks.map((r) => {
            const approved = r.final_lock_decision === "FINAL_APPROVED";
            return (
              <tr key={r.id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                <td className="py-2 px-2 text-muted-foreground">{r.id}</td>
                <td className="py-2 px-2 whitespace-nowrap text-muted-foreground">
                  {new Date(r.created_at).toLocaleString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </td>
                <td className="py-2 px-2 font-medium">{r.player}</td>
                <td className="py-2 px-2">{r.market}</td>
                <td className="py-2 px-2">{r.side}</td>
                <td className="py-2 px-2 whitespace-nowrap">{r.slip_type ?? "—"}</td>
                <td className="py-2 px-2">
                  {r.shrinkage_probability != null
                    ? pct(r.shrinkage_probability)
                    : "—"}
                </td>
                <td className="py-2 px-2">
                  {r.estimated_slip_ev != null ? (
                    <span
                      className={
                        r.estimated_slip_ev >= 0
                          ? "text-accent font-medium"
                          : "text-destructive"
                      }
                    >
                      {fmt2(r.estimated_slip_ev)}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="py-2 px-2">
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold ${
                      approved
                        ? "bg-accent/20 text-accent"
                        : "bg-yellow-500/20 text-yellow-400"
                    }`}
                  >
                    {approved ? (
                      <CheckCircle2 size={10} />
                    ) : (
                      <AlertTriangle size={10} />
                    )}
                    {approved ? "APPROVED" : "HOLD"}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main page ───────────────────────────────────────────────────────────────

export default function FinalLockDashboard() {
  const [form, setForm] = useState<FormData>(EMPTY_FORM);
  const [gate, setGate] = useState<GateResult | null>(null);
  const [locks, setLocks] = useState<LockRecord[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<{
    ok: boolean;
    decision?: string;
    id?: number;
    message?: string;
  } | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [autofillQuery, setAutofillQuery] = useState("");
  const [autofillLoading, setAutofillLoading] = useState(false);

  // Recompute gate whenever form changes
  useEffect(() => {
    setGate(computeGate(form));
  }, [form]);

  // Sync pick_count when slip_type changes
  const setField = useCallback(<K extends keyof FormData>(k: K, v: FormData[K]) => {
    setForm((prev) => {
      const next = { ...prev, [k]: v };
      if (k === "slip_type" && typeof v === "string") {
        const fmt = SLIP_FORMATS[v];
        if (fmt) next.pick_count = fmt.legs;
        const mult = PAYOUT_MULT[v];
        if (mult) next.pp_payout = mult.toFixed(2);
      }
      return next;
    });
    setSaveResult(null);
  }, []);

  const fetchHistory = useCallback(async () => {
    setLoadingHistory(true);
    try {
      const res = await fetch("/lock-api/history", {
        headers: { "X-API-Key": API_KEY },
      });
      if (res.ok) {
        const data = await res.json();
        setLocks(data.locks ?? []);
      }
    } catch {
      // silent
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const handleAutofill = async () => {
    if (!autofillQuery.trim()) return;
    setAutofillLoading(true);
    try {
      // Parse "Player Name / Market" from query
      const parts = autofillQuery.split("/").map((s) => s.trim());
      const player = parts[0] || autofillQuery.trim();
      const market = parts[1] || "";

      const res = await fetch("/wow/l10/v2", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": API_KEY,
        },
        body: JSON.stringify({ player_name: player, market }),
      });

      if (res.ok) {
        const data = await res.json();
        const cand = data.candidate || data;
        setForm((prev) => ({
          ...prev,
          player: cand.player_name ?? cand.player ?? player,
          team: cand.team ?? prev.team,
          opponent: cand.opponent ?? prev.opponent,
          market: cand.market ?? market,
          side: cand.side ?? prev.side,
          pp_line: cand.pp_line != null ? String(cand.pp_line) : prev.pp_line,
          sb_comp_line: cand.sb_comp_line != null ? String(cand.sb_comp_line) : prev.sb_comp_line,
          sb_no_vig_prob: cand.sb_no_vig_prob != null ? String(cand.sb_no_vig_prob) : prev.sb_no_vig_prob,
          model_probability: cand.model_probability != null ? String(cand.model_probability) : prev.model_probability,
          shrinkage_probability: cand.shrinkage_probability ?? cand.usable_probability != null
            ? String(cand.shrinkage_probability ?? cand.usable_probability)
            : prev.shrinkage_probability,
          proj_source1: cand.proj_source1 != null ? String(cand.proj_source1) : prev.proj_source1,
          proj_source2: cand.proj_source2 != null ? String(cand.proj_source2) : prev.proj_source2,
        }));
      }
    } catch {
      // silent
    } finally {
      setAutofillLoading(false);
    }
  };

  const handleSubmit = async () => {
    if (!form.player.trim() || !form.market.trim()) return;
    setSaving(true);
    setSaveResult(null);
    try {
      const payload = {
        ...form,
        pp_line: form.pp_line ? parseFloat(form.pp_line) : null,
        pp_payout: form.pp_payout ? parseFloat(form.pp_payout) : null,
        sb_comp_line: form.sb_comp_line ? parseFloat(form.sb_comp_line) : null,
        sb_no_vig_prob: form.sb_no_vig_prob ? parseFloat(form.sb_no_vig_prob) : null,
        proj_source1: form.proj_source1 ? parseFloat(form.proj_source1) : null,
        proj_source2: form.proj_source2 ? parseFloat(form.proj_source2) : null,
        model_probability: form.model_probability ? parseFloat(form.model_probability) : null,
        shrinkage_probability: form.shrinkage_probability ? parseFloat(form.shrinkage_probability) : null,
        pick_count: form.pick_count,
      };

      const res = await fetch("/lock-api/submit", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": API_KEY,
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (res.ok) {
        setSaveResult({ ok: true, decision: data.decision, id: data.id });
        fetchHistory();
      } else {
        setSaveResult({ ok: false, message: data.error ?? "Error saving lock" });
      }
    } catch (e) {
      setSaveResult({ ok: false, message: "Network error" });
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setForm(EMPTY_FORM);
    setSaveResult(null);
    setGate(null);
  };

  const slipFormatOptions = Object.entries(SLIP_FORMATS).map(([k, v]) => ({
    value: k,
    label: v.label,
  }));

  const gateColor = gate?.gate_pass
    ? "text-accent"
    : gate
    ? "text-destructive"
    : "text-muted-foreground";

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border bg-card/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary/20 border border-primary/30 flex items-center justify-center">
              <Lock size={16} className="text-primary" />
            </div>
            <div>
              <h1 className="text-sm font-bold text-foreground tracking-tight">
                WOW Final Lock Dashboard
              </h1>
              <p className="text-xs text-muted-foreground">
                Machine-gated EV check · v16
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-1 rounded bg-destructive/20 text-destructive border border-destructive/30 font-mono font-semibold">
              can_approve_bets: False
            </span>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Auto-fill bar */}
        <div className="bg-card border border-card-border rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={14} className="text-primary" />
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              Auto-Fill from Gate Engine
            </span>
          </div>
          <div className="flex gap-2">
            <Input
              value={autofillQuery}
              onChange={setAutofillQuery}
              placeholder='e.g. "LeBron James / Points" — pull model data'
              className="flex-1"
            />
            <button
              onClick={handleAutofill}
              disabled={autofillLoading || !autofillQuery.trim()}
              className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-40 hover:opacity-90 transition-opacity flex items-center gap-2 whitespace-nowrap"
            >
              {autofillLoading ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : (
                <Zap size={14} />
              )}
              Auto-Fill
            </button>
            <button
              onClick={handleReset}
              className="px-4 py-2 rounded-md bg-secondary text-secondary-foreground text-sm font-medium hover:opacity-80 transition-opacity"
            >
              Reset
            </button>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            Format: <code className="font-mono bg-muted px-1 py-0.5 rounded">Player Name / Market</code>.
            Missing fields can be filled manually below.
          </p>
        </div>

        {/* Main grid: form + gate panel */}
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-6">
          {/* ── LEFT: Form ── */}
          <div className="bg-card border border-card-border rounded-xl p-5 space-y-5">
            {/* Prop Identity */}
            <div>
              <SectionHeader>Prop Identity</SectionHeader>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <Label>Player Name *</Label>
                  <Input
                    value={form.player}
                    onChange={(v) => setField("player", v)}
                    placeholder="e.g. LeBron James"
                  />
                </div>
                <div>
                  <Label>Team</Label>
                  <Input
                    value={form.team}
                    onChange={(v) => setField("team", v)}
                    placeholder="LAL"
                  />
                </div>
                <div>
                  <Label>Opponent</Label>
                  <Input
                    value={form.opponent}
                    onChange={(v) => setField("opponent", v)}
                    placeholder="GSW"
                  />
                </div>
                <div>
                  <Label>Sport</Label>
                  <Input
                    value={form.sport}
                    onChange={(v) => setField("sport", v)}
                    placeholder="NBA"
                  />
                </div>
                <div>
                  <Label>League</Label>
                  <Input
                    value={form.league}
                    onChange={(v) => setField("league", v)}
                    placeholder="NBA"
                  />
                </div>
                <div>
                  <Label>Market *</Label>
                  <Input
                    value={form.market}
                    onChange={(v) => setField("market", v)}
                    placeholder="Points"
                  />
                </div>
                <div>
                  <Label>Side *</Label>
                  <Select
                    value={form.side}
                    onChange={(v) => setField("side", v)}
                    options={[
                      { value: "Over", label: "Over" },
                      { value: "Under", label: "Under" },
                    ]}
                  />
                </div>
              </div>
            </div>

            {/* Slip Configuration */}
            <div>
              <SectionHeader>Slip Configuration</SectionHeader>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <Label>Slip Format</Label>
                  <Select
                    value={form.slip_type}
                    onChange={(v) => setField("slip_type", v)}
                    options={slipFormatOptions}
                  />
                </div>
                <div>
                  <Label>Pick Count</Label>
                  <Input
                    type="number"
                    min="2"
                    max="6"
                    value={form.pick_count}
                    onChange={(v) => setField("pick_count", parseInt(v) || 2)}
                    placeholder="3"
                  />
                </div>
                <div>
                  <Label>PP Payout Multiplier</Label>
                  <Input
                    type="number"
                    step="0.01"
                    value={form.pp_payout}
                    onChange={(v) => setField("pp_payout", v)}
                    placeholder="3.78"
                  />
                </div>
                <div>
                  <Label>PP Line</Label>
                  <Input
                    type="number"
                    step="0.5"
                    value={form.pp_line}
                    onChange={(v) => setField("pp_line", v)}
                    placeholder="25.5"
                  />
                </div>
                <div>
                  <Label>Environment</Label>
                  <Select
                    value={form.environment}
                    onChange={(v) => setField("environment", v)}
                    options={[
                      { value: "live", label: "Live" },
                      { value: "paper", label: "Paper" },
                      { value: "backtest", label: "Backtest" },
                    ]}
                  />
                </div>
              </div>
            </div>

            {/* Context Flags */}
            <div>
              <SectionHeader>Context Flags</SectionHeader>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Injury Status</Label>
                  <Select
                    value={form.injury_status}
                    onChange={(v) => setField("injury_status", v)}
                    options={[
                      { value: "Healthy", label: "Healthy" },
                      { value: "Questionable", label: "Questionable" },
                      { value: "Probable", label: "Probable" },
                      { value: "GTD", label: "Game-Time Decision" },
                      { value: "Out", label: "Out" },
                    ]}
                  />
                </div>
                <div>
                  <Label>Teammate Status</Label>
                  <Select
                    value={form.teammate_status}
                    onChange={(v) => setField("teammate_status", v)}
                    options={[
                      { value: "Full", label: "Full lineup" },
                      { value: "Key out", label: "Key teammate out" },
                      { value: "Key in", label: "Key teammate back" },
                      { value: "Unknown", label: "Unknown" },
                    ]}
                  />
                </div>
                <div className="col-span-2 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setField("correlation_flag", !form.correlation_flag)}
                    className={`relative w-10 h-5 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                      form.correlation_flag ? "bg-primary" : "bg-muted"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                        form.correlation_flag ? "translate-x-5" : "translate-x-0"
                      }`}
                    />
                  </button>
                  <span className="text-sm text-foreground">
                    Correlation flag
                    <span className="ml-2 text-xs text-muted-foreground">
                      (legs are not independent)
                    </span>
                  </span>
                </div>
              </div>
            </div>

            {/* Sportsbook Comp */}
            <div>
              <SectionHeader>Sportsbook Comparison</SectionHeader>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>SB Comp Line</Label>
                  <Input
                    type="number"
                    step="0.5"
                    value={form.sb_comp_line}
                    onChange={(v) => setField("sb_comp_line", v)}
                    placeholder="25.5"
                  />
                </div>
                <div>
                  <Label>SB No-Vig Prob</Label>
                  <Input
                    type="number"
                    step="0.001"
                    min="0"
                    max="1"
                    value={form.sb_no_vig_prob}
                    onChange={(v) => setField("sb_no_vig_prob", v)}
                    placeholder="0.58"
                  />
                </div>
              </div>
            </div>

            {/* Projections & Model */}
            <div>
              <SectionHeader>Projections &amp; Model Output</SectionHeader>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Projection Source 1</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={form.proj_source1}
                    onChange={(v) => setField("proj_source1", v)}
                    placeholder="26.2"
                  />
                </div>
                <div>
                  <Label>Projection Source 2</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={form.proj_source2}
                    onChange={(v) => setField("proj_source2", v)}
                    placeholder="25.8"
                  />
                </div>
                <div>
                  <Label>Model Probability (raw)</Label>
                  <Input
                    type="number"
                    step="0.001"
                    min="0"
                    max="1"
                    value={form.model_probability}
                    onChange={(v) => setField("model_probability", v)}
                    placeholder="0.65"
                  />
                </div>
                <div>
                  <Label>Shrinkage Probability *</Label>
                  <Input
                    type="number"
                    step="0.001"
                    min="0"
                    max="1"
                    value={form.shrinkage_probability}
                    onChange={(v) => setField("shrinkage_probability", v)}
                    placeholder="0.61"
                    className={
                      gate?.gate_pass === true
                        ? "border-accent/60"
                        : gate?.gate_pass === false
                        ? "border-destructive/60"
                        : ""
                    }
                  />
                </div>
                <div className="col-span-2">
                  <Label>Notes</Label>
                  <textarea
                    value={form.notes}
                    onChange={(e) => setField("notes", e.target.value)}
                    placeholder="Optional context, matchup notes, etc."
                    rows={2}
                    className="w-full rounded-md border border-input bg-muted px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors resize-none"
                  />
                </div>
              </div>
            </div>
          </div>

          {/* ── RIGHT: Gate panel ── */}
          <div className="space-y-4">
            {/* EV Gate Results */}
            <div className="bg-card border border-card-border rounded-xl p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-6 h-6 rounded-md bg-primary/20 flex items-center justify-center">
                  <Zap size={13} className="text-primary" />
                </div>
                <span className="text-sm font-semibold">EV Gate Results</span>
              </div>

              {gate ? (
                <div className="space-y-3">
                  {/* Per-leg edge */}
                  <div className="flex justify-between items-center py-2 border-b border-border/50">
                    <span className="text-xs text-muted-foreground">Per-leg breakeven</span>
                    <span className="text-sm font-mono font-medium">
                      {pct(gate.per_leg_breakeven)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-border/50">
                    <span className="text-xs text-muted-foreground">Your shrink prob</span>
                    <span className="text-sm font-mono font-medium">
                      {pct(gate.shrinkage_probability)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-border/50">
                    <span className="text-xs text-muted-foreground">Edge per leg</span>
                    <span
                      className={`text-sm font-mono font-semibold ${
                        gate.edge_per_leg >= 0 ? "text-accent" : "text-destructive"
                      }`}
                    >
                      {fmt2(gate.edge_per_leg)}
                    </span>
                  </div>

                  <div className="mt-1 pt-1">
                    <div className="flex justify-between items-center py-2 border-b border-border/50">
                      <span className="text-xs text-muted-foreground">Slip probability</span>
                      <span className="text-sm font-mono">{pct(gate.slip_probability, 2)}</span>
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-border/50">
                      <span className="text-xs text-muted-foreground">Slip breakeven</span>
                      <span className="text-sm font-mono">{pct(gate.breakeven_slip_prob, 2)}</span>
                    </div>
                    <div className="flex justify-between items-center py-2">
                      <span className="text-xs text-muted-foreground">Estimated slip EV</span>
                      <span
                        className={`text-sm font-mono font-semibold ${
                          gate.estimated_slip_ev >= 0 ? "text-accent" : "text-destructive"
                        }`}
                      >
                        {fmt2(gate.estimated_slip_ev)}
                      </span>
                    </div>
                  </div>

                  {/* EV bar */}
                  <div className="mt-2">
                    <div className="flex justify-between text-xs text-muted-foreground mb-1">
                      <span>Shrink vs Breakeven</span>
                      <span>{pct(gate.shrinkage_probability)} / {pct(gate.per_leg_breakeven)}</span>
                    </div>
                    <div className="h-2 rounded-full bg-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          gate.gate_pass ? "bg-accent" : "bg-destructive"
                        }`}
                        style={{
                          width: `${Math.min(100, (gate.shrinkage_probability / gate.per_leg_breakeven) * 100)}%`,
                        }}
                      />
                    </div>
                    <div
                      className="mt-1 text-right text-xs"
                      style={{
                        marginLeft: `${Math.min(96, gate.per_leg_breakeven * 100)}%`,
                      }}
                    >
                      <span className="text-muted-foreground">▲</span>
                    </div>
                  </div>

                  {/* Decision badge */}
                  <div
                    className={`mt-3 rounded-lg p-4 text-center border ${
                      gate.gate_pass
                        ? "bg-accent/10 border-accent/30"
                        : "bg-yellow-500/10 border-yellow-500/30"
                    }`}
                  >
                    <div className="flex items-center justify-center gap-2 mb-1">
                      {gate.gate_pass ? (
                        <CheckCircle2 size={20} className="text-accent" />
                      ) : (
                        <AlertTriangle size={20} className="text-yellow-400" />
                      )}
                      <span
                        className={`text-lg font-bold font-mono ${
                          gate.gate_pass ? "text-accent" : "text-yellow-400"
                        }`}
                      >
                        {gate.decision}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {gate.gate_pass
                        ? "Probability clears the per-leg breakeven threshold."
                        : "Below per-leg breakeven — hold for further review."}
                    </p>
                  </div>

                  {form.correlation_flag && (
                    <div className="flex items-start gap-2 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
                      <AlertTriangle size={14} className="text-yellow-400 mt-0.5 shrink-0" />
                      <p className="text-xs text-yellow-400">
                        Correlation flag is ON — slip EV math assumes independent legs. Actual EV may differ.
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-8">
                  <div className="w-12 h-12 rounded-full bg-muted/50 flex items-center justify-center mx-auto mb-3">
                    <Lock size={20} className="text-muted-foreground" />
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Enter Shrinkage Probability to compute the EV gate.
                  </p>
                </div>
              )}
            </div>

            {/* Save button */}
            <button
              onClick={handleSubmit}
              disabled={saving || !form.player.trim() || !form.market.trim() || !form.shrinkage_probability}
              className="w-full py-3 rounded-xl bg-primary text-primary-foreground font-semibold text-sm disabled:opacity-40 hover:opacity-90 transition-opacity flex items-center justify-center gap-2"
            >
              {saving ? (
                <RefreshCw size={16} className="animate-spin" />
              ) : (
                <Lock size={16} />
              )}
              {saving ? "Saving…" : "Save Final Lock to Database"}
            </button>

            {/* Save result */}
            {saveResult && (
              <div
                className={`rounded-xl p-4 border ${
                  saveResult.ok
                    ? "bg-accent/10 border-accent/30"
                    : "bg-destructive/10 border-destructive/30"
                }`}
              >
                {saveResult.ok ? (
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={16} className="text-accent" />
                    <div>
                      <p className="text-sm font-semibold text-accent">
                        Saved — Lock #{saveResult.id}
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Decision:{" "}
                        <span className="font-mono font-medium">{saveResult.decision}</span>
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <XCircle size={16} className="text-destructive" />
                    <p className="text-sm text-destructive">{saveResult.message}</p>
                  </div>
                )}
              </div>
            )}

            {/* Disclaimer */}
            <div className="rounded-xl p-3 bg-muted/50 border border-border">
              <p className="text-xs text-muted-foreground leading-relaxed">
                <span className="font-semibold text-foreground/70">WOW v16 Disclaimer:</span>{" "}
                This tool is for informational purposes only. All decisions are advisory and
                model-generated. <code className="font-mono text-destructive">can_approve_bets: False</code> is
                enforced system-wide. No bet placement is automated or guaranteed.
              </p>
            </div>
          </div>
        </div>

        {/* History section */}
        <div className="bg-card border border-card-border rounded-xl overflow-hidden">
          <button
            onClick={() => setHistoryOpen((o) => !o)}
            className="w-full px-5 py-4 flex items-center justify-between text-left hover:bg-muted/20 transition-colors"
          >
            <div className="flex items-center gap-2">
              <History size={14} className="text-primary" />
              <span className="text-sm font-semibold">Recent Final Locks</span>
              {locks.length > 0 && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-primary/20 text-primary font-medium">
                  {locks.length}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <div
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  fetchHistory();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation();
                    fetchHistory();
                  }
                }}
                aria-disabled={loadingHistory}
                className="p-1.5 rounded-md hover:bg-muted transition-colors cursor-pointer"
              >
                <RefreshCw size={13} className={`text-muted-foreground ${loadingHistory ? "animate-spin" : ""}`} />
              </div>
              {historyOpen ? (
                <ChevronUp size={16} className="text-muted-foreground" />
              ) : (
                <ChevronDown size={16} className="text-muted-foreground" />
              )}
            </div>
          </button>

          {historyOpen && (
            <div className="px-5 pb-5 border-t border-border">
              <div className="pt-4">
                <HistoryTable locks={locks} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

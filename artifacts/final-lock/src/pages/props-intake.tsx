import { useState } from "react";
import { Upload, Play, AlertTriangle, CheckCircle2, RefreshCw, Database } from "lucide-react";

const BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";
const API  = BASE.replace("/final-lock", "/api");

interface WowProp {
  player: string;
  team: string;
  opponent: string;
  sport: string;
  game_date: string;
  prop_type: string;
  side: "MORE" | "LESS";
  line: number;
  platform: string;
  payout_context: string | null;
  source_status: string;
  source_grade: string;
  source: string;
  timestamp: string;
}

interface NormalizeResult {
  ok: boolean;
  props: WowProp[];
  errors: string[];
  provider: string;
  raw_count: number;
  note?: string;
  source_status?: string;
}

interface ScoredResult {
  prop: WowProp;
  result: Record<string, unknown>;
  ok: boolean;
}

interface ProviderStatus {
  configured: boolean;
  grade: string;
  note: string;
}

const SPORTS = ["NBA", "WNBA", "MLB", "NFL", "NHL", "SOCCER", "TENNIS"];
const PROVIDERS = ["opticodds", "propline", "sportsgameodds", "odds_api"];

const LABEL_COLOR: Record<string, string> = {
  FINAL_APPROVED:       "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  MONEY_QUALIFIED:      "bg-green-500/20 text-green-400 border-green-500/30",
  MARKET_VERIFIED_HOLD: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  MODEL_QUALIFIED_HOLD: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  RESEARCH_INTEREST:    "bg-violet-500/20 text-violet-400 border-violet-500/30",
  NO_PLAY:              "bg-slate-500/20 text-slate-400 border-slate-500/30",
  REJECT_NO_EDGE:       "bg-red-500/20 text-red-400 border-red-500/30",
  DATA_UNOBTAINABLE:    "bg-amber-500/20 text-amber-400 border-amber-500/30",
};
function labelCls(l: string) {
  return LABEL_COLOR[l] ?? "bg-slate-500/20 text-slate-400 border-slate-500/30";
}

export default function PropsIntakePage() {
  const [sport, setSport]           = useState("NBA");
  const [date, setDate]             = useState(new Date().toISOString().slice(0, 10));
  const [providers, setProviders]   = useState<string[]>(PROVIDERS);
  const [providerStatus, setProviderStatus] = useState<Record<string, ProviderStatus> | null>(null);
  const [rawJson, setRawJson]       = useState("");
  const [tab, setTab]               = useState<"fetch" | "paste">("fetch");

  const [fetching, setFetching]     = useState(false);
  const [scoring, setScoring]       = useState(false);
  const [result, setResult]         = useState<NormalizeResult | null>(null);
  const [scored, setScored]         = useState<ScoredResult[] | null>(null);
  const [error, setError]           = useState<string | null>(null);

  async function checkProviders() {
    try {
      const r = await fetch(`${API}/props/providers`);
      const d = await r.json() as { providers: Record<string, ProviderStatus> };
      setProviderStatus(d.providers);
    } catch { /* ignore */ }
  }

  async function fetchProps() {
    setFetching(true);
    setError(null);
    setResult(null);
    setScored(null);
    try {
      const body: Record<string, unknown> = { sport, date, providers };
      if (tab === "paste" && rawJson.trim()) {
        try {
          const parsed = JSON.parse(rawJson);
          body.props = Array.isArray(parsed) ? parsed : [parsed];
          delete body.providers;
        } catch { setError("Invalid JSON in paste area"); setFetching(false); return; }
      }
      const r = await fetch(`${API}/props/normalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json() as NormalizeResult;
      setResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setFetching(false);
    }
  }

  async function scoreBatch() {
    if (!result?.props.length) return;
    setScoring(true);
    setError(null);
    try {
      const r = await fetch(`${API}/props/score-batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ props: result.props }),
      });
      const data = await r.json() as { results: ScoredResult[] };
      setScored(data.results);
    } catch (e) {
      setError(String(e));
    } finally {
      setScoring(false);
    }
  }

  const toggleProvider = (p: string) => {
    setProviders(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]);
  };

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      <div className="max-w-6xl mx-auto">

        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Database size={22} className="text-primary" />
            Props Intake
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Normalize PrizePicks-style props from providers into the WOW schema, then score in batch.
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex gap-1 bg-card border border-border rounded-lg p-1 w-fit mb-5">
          {(["fetch", "paste"] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === t ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {t === "fetch" ? "Fetch from Provider" : "Paste / Manual"}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Controls */}
          <div className="lg:col-span-1 space-y-4">
            <div className="bg-card border border-border rounded-lg p-4 space-y-4">

              {tab === "fetch" ? (
                <>
                  {/* Sport */}
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1.5">Sport</label>
                    <div className="flex flex-wrap gap-1.5">
                      {SPORTS.map(s => (
                        <button
                          key={s}
                          onClick={() => setSport(s)}
                          className={`px-2.5 py-1 rounded-md text-xs font-medium border transition-colors ${
                            sport === s
                              ? "bg-primary/20 border-primary text-primary"
                              : "bg-card border-border text-muted-foreground hover:border-primary/40"
                          }`}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Date */}
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1.5">Date</label>
                    <input
                      type="date"
                      value={date}
                      onChange={e => setDate(e.target.value)}
                      className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm outline-none focus:border-primary"
                    />
                  </div>

                  {/* Providers */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Providers</label>
                      <button
                        onClick={checkProviders}
                        className="text-xs text-primary hover:underline"
                      >
                        Check keys
                      </button>
                    </div>
                    <div className="space-y-1.5">
                      {PROVIDERS.map(p => {
                        const ps = providerStatus?.[p];
                        return (
                          <label key={p} className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={providers.includes(p)}
                              onChange={() => toggleProvider(p)}
                              className="accent-primary"
                            />
                            <span className="text-sm">{p}</span>
                            {ps && (
                              <span className={`text-xs ml-auto ${ps.configured ? "text-emerald-400" : "text-red-400"}`}>
                                {ps.configured ? "✓" : "✗"} Grade {ps.grade}
                              </span>
                            )}
                          </label>
                        );
                      })}
                    </div>
                  </div>
                </>
              ) : (
                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1.5">
                    Paste props JSON (array or single object)
                  </label>
                  <textarea
                    rows={12}
                    value={rawJson}
                    onChange={e => setRawJson(e.target.value)}
                    className="w-full bg-background border border-border rounded-md px-3 py-2 text-xs font-mono outline-none focus:border-primary resize-y"
                    placeholder={`[\n  {\n    "player": "A. Edwards",\n    "sport": "NBA",\n    "prop_type": "Points",\n    "side": "MORE",\n    "line": 25.5\n  }\n]`}
                  />
                </div>
              )}

              <button
                onClick={fetchProps}
                disabled={fetching}
                className="w-full flex items-center justify-center gap-2 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
              >
                <Upload size={14} className={fetching ? "animate-pulse" : ""} />
                {fetching ? "Fetching…" : "Normalize Props"}
              </button>
            </div>
          </div>

          {/* Results */}
          <div className="lg:col-span-2 space-y-4">
            {error && (
              <div className="flex items-center gap-2 text-amber-400 bg-amber-400/10 border border-amber-400/30 rounded-lg px-4 py-3 text-sm">
                <AlertTriangle size={14} />
                {error}
              </div>
            )}

            {/* Normalized props */}
            {result && (
              <div className="bg-card border border-border rounded-lg p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    {result.ok
                      ? <CheckCircle2 size={14} className="text-emerald-400" />
                      : <AlertTriangle size={14} className="text-amber-400" />
                    }
                    <span className="text-sm font-medium">
                      {result.props.length} props normalized
                      <span className="text-muted-foreground font-normal ml-1">
                        from {result.provider}
                      </span>
                    </span>
                  </div>
                  <button
                    onClick={scoreBatch}
                    disabled={scoring || result.props.length === 0}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-primary/20 text-primary border border-primary/40 rounded-lg text-xs font-medium hover:bg-primary/30 disabled:opacity-50"
                  >
                    <Play size={11} />
                    {scoring ? "Scoring…" : "Score All"}
                  </button>
                </div>

                {result.note && (
                  <div className="text-xs text-amber-400 bg-amber-400/10 border border-amber-400/30 rounded px-3 py-2 mb-3">
                    {result.note}
                  </div>
                )}

                <div className="space-y-1.5 max-h-96 overflow-y-auto">
                  {result.props.map((p, i) => {
                    const sr = scored?.find(s => s.prop.player === p.player && s.prop.prop_type === p.prop_type && s.prop.side === p.side);
                    const label = (sr?.result?.["terminal_label"] as string) ?? null;
                    return (
                      <div key={i} className="flex items-center gap-3 bg-background/50 rounded-md px-3 py-2 text-xs">
                        <span className="font-medium w-36 shrink-0 truncate">{p.player}</span>
                        <span className="text-muted-foreground w-14 shrink-0">{p.sport}</span>
                        <span className="text-muted-foreground flex-1 truncate">{p.prop_type} {p.side} {p.line}</span>
                        <span className={`shrink-0 text-xs ${p.source_grade === "A" ? "text-emerald-400" : p.source_grade === "UNVERIFIED" ? "text-amber-400" : "text-blue-400"}`}>
                          {p.source_grade}
                        </span>
                        {label && (
                          <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded border ${labelCls(label)}`}>
                            {label}
                          </span>
                        )}
                        {scoring && !label && (
                          <span className="shrink-0">
                            <RefreshCw size={10} className="animate-spin text-muted-foreground" />
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Scored summary */}
            {scored && (
              <div className="bg-card border border-border rounded-lg p-4">
                <h3 className="text-sm font-medium mb-3">WOW Gate Results</h3>
                <div className="space-y-2">
                  {scored.map((s, i) => {
                    const label = String(s.result?.["terminal_label"] ?? "—");
                    const blockers = (s.result?.["blockers"] as string[] | undefined) ?? [];
                    return (
                      <div key={i} className="flex items-start gap-3 bg-background/50 rounded-md px-3 py-2.5 text-xs">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium">{s.prop.player}</span>
                            <span className="text-muted-foreground">{s.prop.prop_type} {s.prop.side} {s.prop.line}</span>
                          </div>
                          {blockers.length > 0 && (
                            <div className="text-red-300 mt-0.5 truncate">
                              {blockers.slice(0, 2).join(" · ")}{blockers.length > 2 ? ` +${blockers.length - 2} more` : ""}
                            </div>
                          )}
                        </div>
                        <span className={`shrink-0 text-xs px-2 py-0.5 rounded border font-medium ${labelCls(label)}`}>
                          {label}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {!result && !error && (
              <div className="text-center py-20 text-muted-foreground">
                <Upload size={32} className="mx-auto mb-3 opacity-30" />
                <p className="text-sm">Select a sport and provider, then click "Normalize Props".</p>
                <p className="text-xs mt-1 opacity-60">
                  Returns DATA_UNOBTAINABLE when provider keys are missing — never fabricates lines.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

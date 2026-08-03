import { useState, useRef, useCallback } from "react";
import { Sparkles, Upload, X, Loader2, AlertTriangle, ChevronRight } from "lucide-react";
import { WowResultCard } from "@/components/wow-result-card";
import { useLocation } from "wouter";

const BASE     = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

const SLIP_FORMATS: Record<string, { breakeven: number; legs: number }> = {
  "2-pick Power": { breakeven: 0.578, legs: 2 },
  "3-pick Power": { breakeven: 0.642, legs: 3 },
  "4-pick Power": { breakeven: 0.679, legs: 4 },
  "5-pick Power": { breakeven: 0.710, legs: 5 },
  "6-pick Power": { breakeven: 0.735, legs: 6 },
  "3-pick Flex":  { breakeven: 0.555, legs: 3 },
  "4-pick Flex":  { breakeven: 0.565, legs: 4 },
  "5-pick Flex":  { breakeven: 0.575, legs: 5 },
  "6-pick Flex":  { breakeven: 0.580, legs: 6 },
};

interface AnalyzeResult {
  player: string;
  sport: string;
  prop: string;
  direction: "MORE" | "LESS";
  line: number;
  league?: string | null;
  confidence: "high" | "medium" | "low";
}

interface SharpAnchor {
  anchor_status: string;
  our_side_prob?: number;
  reject: boolean;
  detail: string;
}

export default function PromptPage() {
  const [, navigate] = useLocation();

  const [prompt, setPrompt]                 = useState("");
  const [imageB64, setImageB64]             = useState<string | null>(null);
  const [imageMime, setImageMime]           = useState("image/png");
  const [imagePreview, setImagePreview]     = useState<string | null>(null);
  const [loading, setLoading]               = useState(false);
  const [error, setError]                   = useState<string | null>(null);
  const [result, setResult]                 = useState<AnalyzeResult | null>(null);

  // Optional sharp anchor fields
  const [sbNoVigProb, setSbNoVigProb]       = useState("");
  const [sharpFairLine, setSharpFairLine]   = useState("");
  const [slipType, setSlipType]             = useState("3-pick Power");
  const [shrinkProb, setShrinkProb]         = useState("");
  const [sharpAnchor, setSharpAnchor]       = useState<SharpAnchor | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);

  const onFile = useCallback((file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target?.result as string;
      setImagePreview(dataUrl);
      const base64 = dataUrl.split(",")[1];
      setImageB64(base64);
      setImageMime(file.type || "image/png");
    };
    reader.readAsDataURL(file);
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file?.type.startsWith("image/")) onFile(file);
  }, [onFile]);

  const onAnalyze = async () => {
    if (!prompt.trim() && !imageB64) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setSharpAnchor(null);

    try {
      const resp = await fetch(`${BASE}/api/wow/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt:       prompt.trim() || undefined,
          image_base64: imageB64 || undefined,
          image_mime:   imageMime,
        }),
      });
      const data = await resp.json();
      if (!data.ok) {
        setError(data.error || "Extraction failed");
        return;
      }
      setResult({
        player:     data.player,
        sport:      data.sport,
        prop:       data.prop,
        direction:  data.direction as "MORE" | "LESS",
        line:       data.line,
        league:     data.league ?? null,
        confidence: data.confidence ?? "medium",
      });

      // Run sharp anchor check if we have the inputs
      const nvp = parseFloat(sbNoVigProb);
      const sfl = parseFloat(sharpFairLine);
      if (!isNaN(nvp)) {
        try {
          const sar = await fetch(`${BASE}/api/gate-engine/sharp-anchor`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              pp_line:           data.line,
              sharp_fair_line:   isNaN(sfl) ? null : sfl,
              sharp_no_vig_prob: nvp,
              side:              data.direction,
            }),
          });
          if (sar.ok) {
            const saData = await sar.json();
            setSharpAnchor({
              anchor_status: saData.anchor_status,
              our_side_prob: saData.our_side_prob,
              reject:        saData.reject,
              detail:        saData.detail,
            });
          }
        } catch {
          // Sharp anchor is optional — ignore errors
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setLoading(false);
    }
  };

  // Compute gate mini-panel
  const shrink = parseFloat(shrinkProb);
  const fmt    = SLIP_FORMATS[slipType];
  const gate   = result && !isNaN(shrink) && fmt
    ? {
        per_leg_breakeven:    fmt.breakeven,
        shrinkage_probability: shrink,
        edge_per_leg:         shrink - fmt.breakeven,
        gate_pass:            shrink >= fmt.breakeven,
        decision:             shrink >= fmt.breakeven ? "ESTIMATE_CLEARS_BREAKEVEN" : "ESTIMATE_HOLD_BELOW_BREAKEVEN",
      }
    : null;

  const handleSendToForm = () => {
    if (!result) return;
    // Navigate to the Final Lock form with query params pre-filled
    const params = new URLSearchParams({
      player:    result.player,
      sport:     result.sport,
      market:    result.prop,
      side:      result.direction,
      pp_line:   String(result.line),
      ...(result.league ? { league: result.league } : {}),
    });
    navigate(`/?${params.toString()}`);
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Page header */}
      <div className="sticky top-0 z-10 border-b border-border bg-background/95 backdrop-blur">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
            <Sparkles size={16} className="text-primary" />
          </div>
          <div>
            <h1 className="text-sm font-bold leading-tight">WOW Prompt Analyzer</h1>
            <p className="text-xs text-muted-foreground">Describe a prop or paste a screenshot</p>
          </div>
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-4 py-6 space-y-4">

        {/* Prompt input */}
        <div className="rounded-2xl border border-border bg-card overflow-hidden">
          <div className="px-4 pt-4">
            <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
              Describe the prop
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onAnalyze();
              }}
              placeholder={`e.g. "Rhyne Howard rebounds MORE 3.5 tonight vs Indiana"\nor "Luka Doncic points over 32.5"`}
              rows={3}
              className="w-full bg-transparent text-sm placeholder:text-muted-foreground/50 resize-none outline-none"
            />
          </div>

          {/* Image drop zone */}
          <div
            className="mx-4 mb-4 mt-2 rounded-xl border-2 border-dashed border-border hover:border-primary/40 transition-colors cursor-pointer relative"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); }}
            />
            {imagePreview ? (
              <div className="relative">
                <img
                  src={imagePreview}
                  alt="Uploaded slip"
                  className="w-full max-h-48 object-contain rounded-xl"
                />
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setImageB64(null);
                    setImagePreview(null);
                  }}
                  className="absolute top-2 right-2 w-6 h-6 rounded-full bg-background/90 border border-border flex items-center justify-center hover:bg-muted transition-colors"
                >
                  <X size={12} />
                </button>
              </div>
            ) : (
              <div className="py-5 flex flex-col items-center gap-1.5">
                <Upload size={18} className="text-muted-foreground" />
                <p className="text-xs text-muted-foreground">
                  Drop a betting slip screenshot, or <span className="text-primary">click to upload</span>
                </p>
              </div>
            )}
          </div>

          {/* Optional sharp anchor inputs */}
          <details className="mx-4 mb-4">
            <summary className="text-xs text-muted-foreground cursor-pointer select-none hover:text-foreground transition-colors">
              + Optional: Sharp market data
            </summary>
            <div className="mt-3 grid grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">SB No-Vig Prob (0–1)</label>
                <input
                  type="number"
                  step="0.001"
                  min="0"
                  max="1"
                  value={sbNoVigProb}
                  onChange={(e) => setSbNoVigProb(e.target.value)}
                  placeholder="e.g. 0.62"
                  className="w-full bg-muted/30 border border-border rounded-lg px-3 py-1.5 text-xs outline-none focus:border-primary/60"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Sharp Fair Line</label>
                <input
                  type="number"
                  step="0.5"
                  value={sharpFairLine}
                  onChange={(e) => setSharpFairLine(e.target.value)}
                  placeholder="e.g. 4.5"
                  className="w-full bg-muted/30 border border-border rounded-lg px-3 py-1.5 text-xs outline-none focus:border-primary/60"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Shrinkage Prob</label>
                <input
                  type="number"
                  step="0.001"
                  min="0"
                  max="1"
                  value={shrinkProb}
                  onChange={(e) => setShrinkProb(e.target.value)}
                  placeholder="e.g. 0.685"
                  className="w-full bg-muted/30 border border-border rounded-lg px-3 py-1.5 text-xs outline-none focus:border-primary/60"
                />
              </div>
            </div>
            <div className="mt-2">
              <label className="block text-xs text-muted-foreground mb-1">Slip Format</label>
              <select
                value={slipType}
                onChange={(e) => setSlipType(e.target.value)}
                className="bg-muted/30 border border-border rounded-lg px-3 py-1.5 text-xs outline-none focus:border-primary/60"
              >
                {Object.keys(SLIP_FORMATS).map((f) => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
            </div>
          </details>

          {/* Analyze button */}
          <div className="px-4 pb-4">
            <button
              onClick={onAnalyze}
              disabled={loading || (!prompt.trim() && !imageB64)}
              className="w-full py-3 rounded-xl bg-primary text-primary-foreground font-semibold text-sm flex items-center justify-center gap-2 disabled:opacity-40 hover:opacity-90 transition-opacity"
            >
              {loading ? (
                <><Loader2 size={16} className="animate-spin" /> Analyzing…</>
              ) : (
                <><Sparkles size={16} /> Analyze Prop</>
              )}
            </button>
            <p className="text-xs text-muted-foreground text-center mt-2">
              Powered by Claude · ⌘↵ to submit
            </p>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 flex items-start gap-2">
            <AlertTriangle size={15} className="text-rose-400 shrink-0 mt-0.5" />
            <p className="text-sm text-rose-400">{error}</p>
          </div>
        )}

        {/* Result card */}
        {result && (
          <WowResultCard
            result={result}
            gate={gate}
            sharpAnchor={sharpAnchor}
            onSendToForm={handleSendToForm}
          />
        )}

        {/* Footer tip */}
        {!result && !loading && (
          <div className="rounded-xl border border-border bg-muted/20 px-4 py-4">
            <p className="text-xs text-muted-foreground font-semibold mb-2 uppercase tracking-wide">Examples</p>
            <div className="space-y-2">
              {[
                "Rhyne Howard rebounds MORE 3.5 vs Indiana tonight",
                "Luka over 32.5 points in NBA tonight",
                "Stephen Curry LESS 5.5 assists, 3-pick Power",
              ].map((ex) => (
                <button
                  key={ex}
                  onClick={() => setPrompt(ex)}
                  className="w-full text-left text-xs text-muted-foreground hover:text-foreground bg-muted/30 hover:bg-muted/60 rounded-lg px-3 py-2 transition-colors flex items-center gap-2"
                >
                  <ChevronRight size={11} className="shrink-0 text-primary/60" />
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

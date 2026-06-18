import { useState, useRef, useCallback, useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles, ImagePlus, X, Send, AlertCircle, Key,
  ChevronDown, ChevronUp, Zap,
} from "lucide-react";
import { useApiKey } from "@/hooks/use-api-key";
import { cn } from "@/lib/utils";
import WowResultCard, { type WowResult, type JfResult } from "@/components/wow-result-card";

interface ExtractedProp {
  player: string;
  sport: string;
  prop: string;
  direction: "MORE" | "LESS";
  line: number;
  league?: string | null;
  confidence: "high" | "medium" | "low";
}

const EXAMPLE_PROMPTS = [
  "Score Luka Doncic over 8.5 assists tonight",
  "Haaland goals MORE 0.5 EPL",
  "Aaron Judge home runs over 0.5 vs Red Sox",
  "Nikola Jokic triple-double points over 28.5",
];

function ConfidencePip({ level }: { level: "high" | "medium" | "low" }) {
  const colors = { high: "bg-emerald-400", medium: "bg-amber-400", low: "bg-rose-400" };
  return (
    <span className="flex items-center gap-1 text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
      <span className={cn("w-1.5 h-1.5 rounded-full", colors[level])} />
      {level} confidence
    </span>
  );
}

function PropChip({
  label,
  value,
  editable,
  onEdit,
}: {
  label: string;
  value: string | number;
  editable?: boolean;
  onEdit?: (val: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value));

  function commit() {
    setEditing(false);
    onEdit?.(draft);
  }

  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className="text-[9px] font-semibold text-muted-foreground uppercase tracking-widest">{label}</span>
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && commit()}
          className="text-xs font-black text-foreground bg-transparent border-b border-primary outline-none w-20 text-center"
        />
      ) : (
        <button
          onClick={() => editable && setEditing(true)}
          className={cn(
            "text-xs font-black text-foreground capitalize",
            editable && "hover:text-primary cursor-text underline decoration-dotted underline-offset-2"
          )}
        >
          {String(value)}
        </button>
      )}
    </div>
  );
}

export default function Prompt() {
  const { apiKey } = useApiKey();
  const [promptText, setPromptText] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [extracted, setExtracted] = useState<ExtractedProp | null>(null);
  const [wowResult, setWowResult] = useState<WowResult | null>(null);
  const [jfResult, setJfResult] = useState<JfResult | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [showExamples, setShowExamples] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const analyzeMutation = useMutation({
    mutationFn: async () => {
      let image_base64: string | undefined;
      let image_mime: string | undefined;

      if (imageFile) {
        image_mime = imageFile.type || "image/png";
        image_base64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => {
            const result = reader.result as string;
            resolve(result.split(",")[1]);
          };
          reader.onerror = reject;
          reader.readAsDataURL(imageFile);
        });
      }

      const res = await fetch("/wow/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({
          prompt: promptText || undefined,
          image_base64,
          image_mime,
        }),
      });
      if (res.status === 401) throw new Error("Invalid or missing API key");
      const data = await res.json();
      if (!data.ok) throw new Error(data.error ?? "Analysis failed");
      return data as ExtractedProp & { raw_response: string };
    },
    onSuccess: (data) => {
      setExtracted({
        player: data.player,
        sport: data.sport,
        prop: data.prop,
        direction: data.direction as "MORE" | "LESS",
        line: data.line,
        league: data.league,
        confidence: data.confidence,
      });
      setWowResult(null);
      setJfResult(null);
    },
  });

  const scoreMutation = useMutation({
    mutationFn: async (ext: ExtractedProp) => {
      const params = new URLSearchParams({
        player: ext.player,
        sport: ext.sport,
        prop: ext.prop,
        direction: ext.direction,
        line: String(ext.line),
        ...(ext.league ? { league: ext.league } : {}),
      });
      const res = await fetch(`/wow/l10/v2?${params}`, {
        headers: { "X-API-Key": apiKey },
      });
      if (res.status === 401) throw new Error("Invalid or missing API key");
      const data = await res.json();
      if (!data.ok) throw new Error(data.error ?? "Scoring failed");
      return data as WowResult;
    },
    onSuccess: async (data) => {
      setWowResult(data);
      if (
        data.confidence_tier?.startsWith("FINAL LOCK") ||
        data.confidence_tier?.startsWith("CONDITIONAL")
      ) {
        try {
          const jfRes = await fetch("/wow/jf", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
            body: JSON.stringify({
              props: [{
                player: data.player,
                sport: data.sport,
                prop: data.prop,
                direction: data.direction,
                line: data.line,
                rows: data.rows,
                complete: data.complete,
                confidence_tier: data.confidence_tier,
                source: data.source,
                workflow_fields: data.workflow_fields,
              }],
            }),
          });
          if (jfRes.ok) {
            const jfData = await jfRes.json();
            setJfResult(jfData);
          }
        } catch {
        }
      }
    },
  });

  const handleAnalyze = () => {
    if (!promptText.trim() && !imageFile) return;
    setExtracted(null);
    setWowResult(null);
    setJfResult(null);
    analyzeMutation.mutate();
  };

  const handleScore = () => {
    if (!extracted) return;
    scoreMutation.mutate(extracted);
  };

  const handleImageDrop = useCallback((file: File) => {
    if (!file.type.startsWith("image/")) return;
    setImageFile(file);
    const reader = new FileReader();
    reader.onload = (e) => setImagePreview(e.target?.result as string);
    reader.readAsDataURL(file);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleImageDrop(file);
    },
    [handleImageDrop]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const item = Array.from(e.clipboardData.items).find((i) =>
        i.type.startsWith("image/")
      );
      if (item) {
        const file = item.getAsFile();
        if (file) handleImageDrop(file);
      }
    },
    [handleImageDrop]
  );

  const removeImage = () => {
    setImageFile(null);
    setImagePreview(null);
  };

  const isLoading = analyzeMutation.isPending || scoreMutation.isPending;
  const canAnalyze = (promptText.trim().length > 0 || !!imageFile) && !!apiKey && !isLoading;

  return (
    <div className="min-h-screen flex flex-col items-center justify-start px-4 py-12">
      <div className="w-full max-w-2xl">

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="text-center mb-10"
        >
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 border border-primary/20 text-xs font-bold text-primary uppercase tracking-widest mb-4">
            <Sparkles size={11} />
            WOW v16 Engine
          </div>
          <h1 className="text-4xl font-black text-foreground tracking-tight leading-none mb-3">
            Describe your pick
          </h1>
          <p className="text-sm text-muted-foreground max-w-md mx-auto leading-relaxed">
            Type a prop in plain language, paste a screenshot, or both.
            Claude reads it — WOW scores it.
          </p>
        </motion.div>

        {/* API key warning */}
        <AnimatePresence>
          {!apiKey && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="flex items-center gap-2.5 bg-amber-400/10 border border-amber-400/25 rounded-xl px-4 py-3 mb-5 text-sm"
            >
              <Key size={14} className="text-amber-400 shrink-0" />
              <span className="text-amber-300/90 font-medium">
                Paste your API key in the sidebar to get started
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Main prompt area */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.5 }}
          className={cn(
            "relative rounded-2xl border border-border bg-card transition-all duration-200",
            dragOver && "border-primary/60 ring-2 ring-primary/20"
          )}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {/* Image preview strip */}
          <AnimatePresence>
            {imagePreview && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="px-4 pt-4"
              >
                <div className="relative inline-block">
                  <img
                    src={imagePreview}
                    alt="Betting slip"
                    className="h-28 w-auto rounded-xl border border-border object-cover"
                  />
                  <button
                    onClick={removeImage}
                    className="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-background border border-border flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X size={11} />
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Textarea */}
          <div className="px-4 pt-4 pb-2">
            <textarea
              ref={textareaRef}
              value={promptText}
              onChange={(e) => setPromptText(e.target.value)}
              onPaste={handlePaste}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAnalyze();
              }}
              placeholder={
                dragOver
                  ? "Drop your screenshot here…"
                  : "e.g. Luka over 8.5 assists, or paste a screenshot"
              }
              rows={3}
              className="w-full bg-transparent text-foreground placeholder:text-muted-foreground/50 text-base font-medium resize-none outline-none leading-relaxed"
              disabled={isLoading}
            />
          </div>

          {/* Bottom toolbar */}
          <div className="flex items-center justify-between px-3 pb-3 gap-2">
            <div className="flex items-center gap-1">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                disabled={isLoading}
                title="Attach screenshot"
              >
                <ImagePlus size={14} />
                <span className="hidden sm:inline">Attach slip</span>
              </button>
              <button
                onClick={() => setShowExamples(!showExamples)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                {showExamples ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                <span className="hidden sm:inline">Examples</span>
              </button>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground/50 hidden sm:block">⌘↵ to analyze</span>
              <motion.button
                whileTap={{ scale: 0.96 }}
                onClick={handleAnalyze}
                disabled={!canAnalyze}
                className={cn(
                  "flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold transition-all",
                  canAnalyze
                    ? "bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20"
                    : "bg-muted text-muted-foreground cursor-not-allowed"
                )}
              >
                {analyzeMutation.isPending ? (
                  <>
                    <motion.div
                      animate={{ rotate: 360 }}
                      transition={{ repeat: Infinity, duration: 0.7, ease: "linear" }}
                    >
                      <Sparkles size={14} />
                    </motion.div>
                    Reading…
                  </>
                ) : (
                  <>
                    <Send size={14} />
                    Analyze
                  </>
                )}
              </motion.button>
            </div>
          </div>

          {/* Drop overlay */}
          <AnimatePresence>
            {dragOver && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="absolute inset-0 rounded-2xl flex flex-col items-center justify-center gap-2 bg-primary/8 border-2 border-dashed border-primary/50 pointer-events-none"
              >
                <ImagePlus size={28} className="text-primary/80" />
                <p className="text-sm font-bold text-primary/80">Drop screenshot</p>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>

        {/* Example prompts */}
        <AnimatePresence>
          {showExamples && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-3">
                {EXAMPLE_PROMPTS.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => {
                      setPromptText(ex);
                      setShowExamples(false);
                      textareaRef.current?.focus();
                    }}
                    className="text-left px-3 py-2.5 rounded-xl border border-border hover:border-primary/40 hover:bg-primary/5 text-xs text-muted-foreground hover:text-foreground transition-all duration-150 font-medium"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Error states */}
        <AnimatePresence>
          {(analyzeMutation.isError || scoreMutation.isError) && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="flex items-start gap-2 mt-4 px-4 py-3 rounded-xl bg-rose-500/10 border border-rose-500/25"
            >
              <AlertCircle size={14} className="text-rose-400 mt-0.5 shrink-0" />
              <p className="text-sm text-rose-300/90 font-medium">
                {(analyzeMutation.error as Error)?.message ??
                  (scoreMutation.error as Error)?.message}
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Extracted prop chips + Score button */}
        <AnimatePresence>
          {extracted && !wowResult && (
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
              className="mt-6"
            >
              <div className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-center justify-between mb-4">
                  <p className="text-xs font-black uppercase tracking-widest text-muted-foreground">
                    Extracted prop
                  </p>
                  <ConfidencePip level={extracted.confidence} />
                </div>

                <div className="grid grid-cols-3 sm:grid-cols-6 gap-4 mb-5">
                  <PropChip label="Player" value={extracted.player} editable
                    onEdit={(v) => setExtracted((e) => e ? { ...e, player: v } : e)} />
                  <PropChip label="Sport" value={extracted.sport} editable
                    onEdit={(v) => setExtracted((e) => e ? { ...e, sport: v } : e)} />
                  <PropChip label="Prop" value={extracted.prop} editable
                    onEdit={(v) => setExtracted((e) => e ? { ...e, prop: v } : e)} />
                  <PropChip label="Side" value={extracted.direction} editable
                    onEdit={(v) => setExtracted((e) => e ? { ...e, direction: v as "MORE" | "LESS" } : e)} />
                  <PropChip label="Line" value={extracted.line} editable
                    onEdit={(v) => setExtracted((e) => e ? { ...e, line: parseFloat(v) || e.line } : e)} />
                  {extracted.league && (
                    <PropChip label="League" value={extracted.league} editable
                      onEdit={(v) => setExtracted((e) => e ? { ...e, league: v } : e)} />
                  )}
                </div>

                <div className="flex items-center gap-3">
                  <motion.button
                    whileTap={{ scale: 0.97 }}
                    onClick={handleScore}
                    disabled={scoreMutation.isPending}
                    className={cn(
                      "flex-1 flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-black transition-all",
                      !scoreMutation.isPending
                        ? "bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20"
                        : "bg-muted text-muted-foreground cursor-not-allowed"
                    )}
                  >
                    {scoreMutation.isPending ? (
                      <>
                        <motion.div
                          animate={{ rotate: 360 }}
                          transition={{ repeat: Infinity, duration: 0.7, ease: "linear" }}
                        >
                          <Zap size={15} />
                        </motion.div>
                        Scoring…
                      </>
                    ) : (
                      <>
                        <Zap size={15} />
                        Run WOW Engine
                      </>
                    )}
                  </motion.button>
                  <button
                    onClick={() => setExtracted(null)}
                    className="px-4 py-3 rounded-xl text-xs font-semibold text-muted-foreground hover:text-foreground border border-border hover:border-border/80 transition-colors"
                  >
                    Reset
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* WOW result card */}
        <AnimatePresence>
          {wowResult && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="mt-6"
            >
              <WowResultCard result={wowResult} jfResult={jfResult} />

              {/* Re-analyze button */}
              <motion.button
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.4 }}
                onClick={() => {
                  setWowResult(null);
                  setJfResult(null);
                  setExtracted(null);
                  setPromptText("");
                  removeImage();
                  setTimeout(() => textareaRef.current?.focus(), 50);
                }}
                className="w-full mt-4 py-2.5 text-xs font-semibold text-muted-foreground hover:text-foreground border border-border hover:border-border/80 rounded-xl transition-colors"
              >
                Analyze another pick
              </motion.button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleImageDrop(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}

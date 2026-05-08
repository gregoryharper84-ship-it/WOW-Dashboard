import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { motion, AnimatePresence } from "framer-motion";
import { Zap, AlertCircle, Key } from "lucide-react";
import { Form, FormField, FormItem, FormLabel, FormControl, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useApiKey } from "@/hooks/use-api-key";
import { cn } from "@/lib/utils";

const SPORTS = ["NBA", "NFL", "MLB", "NHL", "NCAAF", "NCAAB", "Soccer", "Tennis", "Golf", "MMA"];
const SIDES = ["MORE", "LESS"];

const SPORT_PROPS: Record<string, string[]> = {
  NBA: ["points", "rebounds", "assists", "threes", "steals", "blocks", "turnovers", "fantasy points"],
  NFL: ["passing yards", "rushing yards", "receiving yards", "touchdowns", "receptions", "completions", "interceptions", "fantasy points"],
  MLB: ["pitcher strikeouts", "hitter fantasy points", "pitcher fantasy points", "pitching outs", "plate appearances", "hits", "home runs", "RBIs", "stolen bases", "walks"],
  NHL: ["goals", "assists", "saves", "shots on goal", "points", "power play points"],
  NCAAF: ["passing yards", "rushing yards", "receiving yards", "touchdowns", "completions", "fantasy points"],
  NCAAB: ["points", "rebounds", "assists", "threes", "steals", "blocks", "fantasy points"],
  Soccer: ["goals", "assists", "shots", "shots on target", "fantasy points"],
  Tennis: ["aces", "double faults", "games won", "sets won"],
  Golf: ["birdies", "bogeys", "fairways hit", "greens in regulation", "putts"],
  MMA: ["significant strikes", "takedowns", "submission attempts"],
};

const DEFAULT_PROPS = ["points", "rebounds", "assists", "touchdowns", "goals", "strikeouts", "fantasy points"];

function getProps(sport: string): string[] {
  return SPORT_PROPS[sport] ?? DEFAULT_PROPS;
}

const schema = z.object({
  player: z.string().min(2, "Player name required"),
  sport: z.string().min(1, "Sport required"),
  prop: z.string().min(2, "Prop type required"),
  side: z.enum(["MORE", "LESS"]),
  line: z.coerce.number().positive("Must be a positive number"),
});

type ScoreForm = z.infer<typeof schema>;

interface ScoreResult {
  score: number;
  player: string;
  sport: string;
  prop: string;
  side: string;
  label: string;
  disclaimer: string;
}

function scoreColor(score: number) {
  if (score >= 0.8) return "#34d399";
  if (score >= 0.65) return "#a78bfa";
  if (score >= 0.5) return "#fbbf24";
  return "#f87171";
}

function scoreLabel(score: number) {
  if (score >= 0.8) return "Strong Signal";
  if (score >= 0.65) return "Solid Signal";
  if (score >= 0.5) return "Neutral Signal";
  return "Weak Signal";
}

function ScoreReveal({ result }: { result: ScoreResult }) {
  const pct = result.score * 100;
  const color = scoreColor(result.score);

  return (
    <motion.div
      data-testid="card-score-result"
      initial={{ opacity: 0, scale: 0.92, y: 20 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="bg-card border border-card-border rounded-2xl p-6 mt-6"
    >
      <div className="text-center mb-6">
        <motion.div
          initial={{ scale: 0.5, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.2, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-bold mb-4"
          style={{ backgroundColor: `${color}20`, color, border: `1px solid ${color}40` }}
        >
          <Zap size={12} />
          {scoreLabel(result.score)}
        </motion.div>

        <div className="relative w-32 h-32 mx-auto mb-4">
          <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="42" fill="none" stroke="hsl(217 33% 17%)" strokeWidth="10" />
            <motion.circle
              cx="50" cy="50" r="42"
              fill="none"
              stroke={color}
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray={`${2 * Math.PI * 42}`}
              strokeDashoffset={`${2 * Math.PI * 42}`}
              animate={{ strokeDashoffset: `${2 * Math.PI * 42 * (1 - result.score)}` }}
              transition={{ delay: 0.3, duration: 1.2, ease: "easeOut" }}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <motion.span
              className="text-3xl font-black tabular-nums"
              style={{ color }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.4 }}
            >
              {pct.toFixed(0)}%
            </motion.span>
          </div>
        </div>

        <h2 className="text-lg font-black text-foreground">{result.player}</h2>
        <p className="text-sm text-muted-foreground">
          {result.side} {result.prop} — {result.sport}
        </p>
      </div>

      <div className="border-t border-border pt-4">
        <p className="text-xs text-muted-foreground text-center leading-relaxed">{result.disclaimer}</p>
      </div>
    </motion.div>
  );
}

export default function Score() {
  const { apiKey, setApiKey } = useApiKey();
  const [result, setResult] = useState<ScoreResult | null>(null);

  const form = useForm<ScoreForm>({
    resolver: zodResolver(schema),
    defaultValues: { player: "", sport: "", prop: "", side: "MORE", line: undefined },
  });

  const selectedSport = form.watch("sport");
  const availableProps = getProps(selectedSport);

  const mutation = useMutation({
    mutationFn: async (values: ScoreForm) => {
      const res = await fetch("/random-forest-score", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify(values),
      });
      if (res.status === 401) throw new Error("Invalid or missing API key");
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error ?? "Scoring failed");
      }
      return res.json() as Promise<ScoreResult>;
    },
    onSuccess: (data) => {
      setResult(data);
    },
  });

  const onSubmit = (values: ScoreForm) => {
    setResult(null);
    mutation.mutate(values);
  };

  return (
    <div className="p-6 max-w-xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-black text-foreground tracking-tight">Score a Pick</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Submit a player prop to get its WOW confidence score
        </p>
      </div>

      {!apiKey && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-start gap-3 bg-amber-400/10 border border-amber-400/30 rounded-xl p-4 mb-6"
          data-testid="status-no-api-key"
        >
          <Key size={16} className="text-amber-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-amber-400">API key required</p>
            <p className="text-xs text-muted-foreground mt-0.5">Paste your key in the sidebar to submit a score.</p>
          </div>
        </motion.div>
      )}

      <div className="bg-card border border-card-border rounded-2xl p-5">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="player"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Player Name</FormLabel>
                  <FormControl>
                    <Input
                      data-testid="input-player"
                      placeholder="e.g. LeBron James"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="sport"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Sport</FormLabel>
                    <Select
                      onValueChange={(val) => {
                        field.onChange(val);
                        form.setValue("prop", "");
                      }}
                      value={field.value}
                    >
                      <FormControl>
                        <SelectTrigger data-testid="select-sport">
                          <SelectValue placeholder="Select sport" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {SPORTS.map((s) => (
                          <SelectItem key={s} value={s}>{s}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="side"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Side</FormLabel>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger data-testid="select-side">
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {SIDES.map((s) => (
                          <SelectItem key={s} value={s}>{s}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="prop"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Prop Type</FormLabel>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger data-testid="select-prop">
                          <SelectValue placeholder="Select prop" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {availableProps.map((p) => (
                          <SelectItem key={p} value={p}>{p}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="line"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Line</FormLabel>
                    <FormControl>
                      <Input
                        data-testid="input-line"
                        type="number"
                        step="0.5"
                        placeholder="25.5"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            {mutation.isError && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex items-center gap-2 text-sm text-destructive"
                data-testid="status-error"
              >
                <AlertCircle size={14} />
                {mutation.error?.message}
              </motion.div>
            )}

            <Button
              data-testid="button-submit-score"
              type="submit"
              disabled={mutation.isPending || !apiKey}
              className={cn(
                "w-full font-bold py-2.5",
                mutation.isPending && "opacity-70"
              )}
            >
              {mutation.isPending ? (
                <span className="flex items-center gap-2">
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ repeat: Infinity, duration: 0.8, ease: "linear" }}
                  >
                    <Zap size={16} />
                  </motion.div>
                  Scoring…
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  <Zap size={16} />
                  Get WOW Score
                </span>
              )}
            </Button>
          </form>
        </Form>
      </div>

      <AnimatePresence mode="wait">
        {result && <ScoreReveal key={result.score + result.player} result={result} />}
      </AnimatePresence>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RefreshCw, CheckCircle, XCircle, AlertCircle, HelpCircle, Activity } from "lucide-react";

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface SourceEntry {
  source_id: string;
  display_name: string;
  status: "OK" | "DEGRADED" | "DOWN" | "UNKNOWN";
  latency_ms: number | null;
  http_status: number | null;
  error: string | null;
  probed_at: string | null;
}

interface HealthSummary {
  ok: boolean;
  overall_status: string;
  n_ok: number;
  n_degraded: number;
  n_down: number;
  n_unknown: number;
  sources: SourceEntry[];
  as_of: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const STATUS_CONFIG = {
  OK:       { color: "text-emerald-400", bg: "bg-emerald-600/15 border-emerald-600/25", icon: CheckCircle },
  DEGRADED: { color: "text-amber-400",   bg: "bg-amber-500/10 border-amber-500/25",   icon: AlertCircle },
  DOWN:     { color: "text-red-400",     bg: "bg-red-500/10 border-red-500/25",        icon: XCircle },
  UNKNOWN:  { color: "text-zinc-500",    bg: "bg-zinc-700/20 border-zinc-600/20",      icon: HelpCircle },
};

function SourceCard({ source }: { source: SourceEntry }) {
  const cfg = STATUS_CONFIG[source.status] ?? STATUS_CONFIG.UNKNOWN;
  const Icon = cfg.icon;

  return (
    <div className={`border rounded-xl p-4 ${cfg.bg} transition-colors`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Icon size={18} className={cfg.color} />
          <div>
            <div className="font-medium text-sm text-foreground">{source.display_name}</div>
            <div className="text-[11px] text-muted-foreground font-mono">{source.source_id}</div>
          </div>
        </div>
        <Badge variant="outline" className={`text-xs ${cfg.color} border-current/30`}>
          {source.status}
        </Badge>
      </div>

      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {source.latency_ms != null && (
          <span>latency: <span className={`font-medium ${source.latency_ms > 2000 ? "text-amber-400" : "text-foreground"}`}>{source.latency_ms}ms</span></span>
        )}
        {source.http_status != null && (
          <span>HTTP: <span className="font-medium text-foreground">{source.http_status}</span></span>
        )}
        {source.probed_at && (
          <span>probed: <span className="font-medium text-foreground">{new Date(source.probed_at).toLocaleTimeString()}</span></span>
        )}
      </div>

      {source.error && (
        <div className="mt-2 text-[11px] text-red-400/80 bg-red-500/5 rounded px-2 py-1 break-all">
          {source.error.slice(0, 120)}{source.error.length > 120 ? "…" : ""}
        </div>
      )}
    </div>
  );
}

function OverallBanner({ summary }: { summary: HealthSummary }) {
  const cfg = STATUS_CONFIG[summary.overall_status as keyof typeof STATUS_CONFIG] ?? STATUS_CONFIG.UNKNOWN;
  const Icon = cfg.icon;

  return (
    <div className={`border rounded-2xl p-5 ${cfg.bg} mb-6`}>
      <div className="flex items-center gap-3">
        <Icon size={28} className={cfg.color} />
        <div>
          <div className={`text-xl font-bold ${cfg.color}`}>
            System {summary.overall_status}
          </div>
          <div className="text-sm text-muted-foreground">
            {summary.n_ok} OK · {summary.n_degraded} degraded · {summary.n_down} down · {summary.n_unknown} unknown
          </div>
        </div>
        <div className="ml-auto text-right text-xs text-muted-foreground">
          <div>as of</div>
          <div className="font-medium text-foreground">{new Date(summary.as_of).toLocaleTimeString()}</div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function SourceHealthPage() {
  const { data, isLoading, isError, refetch } = useQuery<HealthSummary>({
    queryKey: ["source-health"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/wow/source-health`);
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    refetchInterval: 30_000,
  });

  const byStatus = data
    ? {
        OK:       data.sources.filter(s => s.status === "OK"),
        DEGRADED: data.sources.filter(s => s.status === "DEGRADED"),
        DOWN:     data.sources.filter(s => s.status === "DOWN"),
        UNKNOWN:  data.sources.filter(s => s.status === "UNKNOWN"),
      }
    : null;

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border px-6 py-4">
        <div className="flex items-center justify-between max-w-4xl mx-auto">
          <div>
            <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
              <Activity size={20} className="text-primary" />
              Source Health
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Real-time data provider status · auto-refreshes every 30s
            </p>
          </div>
          <Button variant="outline" size="sm" className="h-8" onClick={() => refetch()}>
            <RefreshCw size={13} className={isLoading ? "animate-spin" : ""} />
          </Button>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 py-6">
        {isError && (
          <div className="flex items-center gap-2 text-amber-400 text-sm bg-amber-500/10 border border-amber-500/20 rounded-lg px-4 py-3 mb-4">
            <AlertCircle size={16} />
            Could not load source health. Backend may not be running.
          </div>
        )}

        {isLoading && (
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-24 bg-card border border-border rounded-xl animate-pulse" />
            ))}
          </div>
        )}

        {data && <OverallBanner summary={data} />}

        {byStatus && (
          <div className="space-y-8">
            {(["DOWN", "DEGRADED", "OK", "UNKNOWN"] as const).map(status => {
              const sources = byStatus[status];
              if (sources.length === 0) return null;
              const cfg = STATUS_CONFIG[status];
              return (
                <section key={status}>
                  <h2 className={`text-sm font-semibold mb-3 flex items-center gap-2 ${cfg.color}`}>
                    <cfg.icon size={15} />
                    {status} ({sources.length})
                  </h2>
                  <div className="grid gap-3 md:grid-cols-2">
                    {sources.map(s => <SourceCard key={s.source_id} source={s} />)}
                  </div>
                </section>
              );
            })}
          </div>
        )}

        <div className="mt-8 text-[11px] text-zinc-700 text-right">
          Source health probes are best-effort · partial data is labeled DEGRADED not DOWN
        </div>
      </div>
    </div>
  );
}

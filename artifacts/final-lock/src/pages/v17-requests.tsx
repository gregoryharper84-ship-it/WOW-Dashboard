import { useState, type FormEvent } from "react";
import { apiFetch, getDashboardKey, setDashboardKey } from "@/lib/api";
import { canonicalRequest, receiptRecovery, publicationText, scoreCompletionUnknown } from "@/lib/v17-request";
import { Lock, Loader2, FileCheck2 } from "lucide-react";

type Receipt = Record<string, unknown>;
const records = (value: unknown): Receipt[] => Array.isArray(value) ? value.filter((item): item is Receipt => item !== null && typeof item === "object" && !Array.isArray(item)) : [];
const text = (value: unknown) => value == null ? "Not provided" : typeof value === "object" ? JSON.stringify(value) : String(value);
const requestId = () => `dashboard-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
const LAST_REQUEST = "wow_v17_last_request";
type SavedRequest = { request: ReturnType<typeof canonicalRequest>; completion_unknown: boolean };
function savedRequest(): SavedRequest | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(LAST_REQUEST) ?? "null") as SavedRequest | null;
    if (!value?.request?.rows?.[0]) return null;
    canonicalRequest(value.request.request_id, value.request.rows[0]);
    return value;
  } catch { return null; }
}
function rememberRequest(value: SavedRequest | null) {
  try {
    if (value) sessionStorage.setItem(LAST_REQUEST, JSON.stringify(value));
    else sessionStorage.removeItem(LAST_REQUEST);
  } catch { /* The current page still retains submitted identity in memory. */ }
}

function ReceiptView({ payload, status }: { payload: Receipt; status: number }) {
  const rows = records(payload.rows);
  return (
    <section aria-label="V17 backend receipt" className="rounded-xl border border-border bg-card p-5 space-y-4">
      <h2 className="font-semibold flex items-center gap-2"><FileCheck2 size={18} />V17 backend response · HTTP {status}</h2>
      <p className="text-sm break-words">{text(payload.run_controller_status ?? payload.code ?? payload.status ?? (payload.ok === false ? "Request blocked" : "Response received"))}</p>
      {(payload.message != null || payload.detail != null || payload.error != null) && <p className="text-sm text-amber-300 break-words">{text(payload.message ?? payload.detail ?? payload.error)}</p>}
      {payload.request_id != null && <p className="text-xs font-mono break-all">Request ID: {text(payload.request_id)}</p>}
      {rows.map((row, index) => (
        <article key={index} className="rounded-lg border border-border p-4 space-y-2 text-sm">
          <h3 className="font-semibold break-words">{text(row.row_key ?? `Row ${index + 1}`)} · {text(row.terminal_status ?? row.status ?? row.terminal_label ?? row.code)}</h3>
          <p className="font-mono text-xs break-words">{text(row.code ?? row.blocker_code ?? row.primary_blocker ?? row.blocker ?? row.terminal_label)}</p>
          {row.blockers != null && <p className="text-amber-300 break-words">Blockers: {text(row.blockers)}</p>}
          <p>{publicationText(row)} · Rank eligible: {row.rank_eligible === true ? "true" : row.rank_eligible === false ? "false" : "not provided"}</p>
          {row.prediction_id != null && <p className="font-mono text-xs break-all">Prediction ID: {text(row.prediction_id)}</p>}
          {row.probability_publishable === true && <p className="font-mono text-xs">Calibrated probability: {text(row.calibrated_probability)} · Lower bound: {text(row.calibrated_probability_lower_bound)}</p>}
          {records(row.matches).map((match, matchIndex) => (
            <div key={matchIndex} className="rounded border border-border p-3 text-xs space-y-1">
              <p className="font-mono break-all">Receipt: {text(match.governed_prediction_id)}</p>
              <p>Immutable pregame: {text(match.is_immutable_pregame)} · {publicationText(match)}</p>
              {match.probability_publishable === true && <p>Calibrated probability: {text(match.calibrated_probability)} · Lower bound: {text(match.calibrated_lower_bound)}</p>}
            </div>
          ))}
        </article>
      ))}
      <details>
        <summary className="cursor-pointer text-sm text-primary">Complete backend JSON</summary>
        <p className="text-xs text-muted-foreground mt-2">Native flags and blockers control interpretation. Research values in this receipt do not imply publication or rank eligibility.</p>
        <pre className="text-xs mt-3 p-3 bg-background rounded overflow-auto max-h-96">{JSON.stringify(payload, null, 2)}</pre>
      </details>
    </section>
  );
}

export default function V17RequestsPage() {
  const [restored] = useState(savedRequest);
  const prior = restored?.request.rows[0];
  const [accessKey, setAccessKey] = useState(getDashboardKey);
  const [id, setId] = useState(() => restored?.request.request_id ?? requestId());
  const [rowKey, setRowKey] = useState(prior?.row_key ?? "row-1");
  const [player, setPlayer] = useState(prior?.player ?? "");
  const [sport, setSport] = useState(prior?.sport ?? "NBA");
  const [stat, setStat] = useState(prior?.stat_type ?? "");
  const [event, setEvent] = useState(prior?.event_id ?? "");
  const [start, setStart] = useState(prior?.event_start_time ?? "");
  const [line, setLine] = useState(prior ? String(prior.line) : "");
  const [direction, setDirection] = useState<"MORE" | "LESS">(prior?.direction ?? "MORE");
  const [platform, setPlatform] = useState(prior?.platform ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<{ payload: Receipt; status: number } | null>(null);
  const [submitted, setSubmitted] = useState<ReturnType<typeof canonicalRequest> | null>(restored?.request ?? null);
  const [completionUnknown, setCompletionUnknown] = useState(restored?.completion_unknown === true);
  const [lookupId, setLookupId] = useState("");

  async function call(operation: string, body?: unknown) {
    setBusy(operation); setError(""); setResponse(null);
    try {
      const result = await apiFetch(`/api/v17/${operation}`, {
        method: body ? "POST" : "GET",
        headers: { "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const payload: unknown = await result.json();
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("The dashboard did not receive a JSON receipt.");
      const receipt = payload as Receipt;
      setResponse({ payload: receipt, status: result.status });
      if (operation === "score-pick-request") {
        const unknown = scoreCompletionUnknown(receipt);
        setCompletionUnknown(unknown);
        rememberRequest({ request: body as ReturnType<typeof canonicalRequest>, completion_unknown: unknown });
      }
    } catch {
      if (operation === "score-pick-request") setCompletionUnknown(true);
      setError(operation === "score-pick-request" ? "The connection ended without a terminal receipt. Keep the request identity and recover receipts before resubmitting." : "The dashboard service could not return a JSON response. Check the service connection and configuration.");
    } finally { setBusy(null); }
  }

  function score(e: FormEvent) {
    e.preventDefault();
    try {
      if (!line.trim()) throw new Error("An exact numeric line is required.");
      const request = canonicalRequest(id, { row_key: rowKey, event_id: event, event_start_time: start, sport, player, stat_type: stat, line: Number(line), direction, ...(platform.trim() ? { platform: platform.trim() } : {}) });
      setSubmitted(request);
      rememberRequest({ request, completion_unknown: true });
      void call("score-pick-request", request);
    } catch (err) { setError(err instanceof Error ? err.message : "Check the exact row identity."); }
  }

  const inputClass = "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";
  const buttonClass = "rounded-md border border-primary/40 bg-primary/15 px-4 py-2 text-sm text-primary disabled:opacity-40";
  return (
    <main className="min-h-screen p-4 sm:p-6 max-w-4xl mx-auto space-y-5">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold flex items-center gap-2"><Lock size={22} />WOW V17 Requests</h1>
        <p className="text-sm text-muted-foreground">Submit an exact player prop to the canonical governed model and recover its immutable receipts. The V17 terminal reducer controls outcomes. can_execute=false.</p>
      </header>
      <section className="rounded-xl border border-border bg-card p-5 space-y-3" aria-label="Dashboard access">
        <label className="text-sm block">Dashboard access key
          <input type="password" autoComplete="off" value={accessKey} onChange={e => { setAccessKey(e.target.value); setDashboardKey(e.target.value); }} className={inputClass} placeholder="Enter the dashboard access key" />
        </label>
        <p className="text-xs text-muted-foreground">Saved for this browser tab session. The governed backend credential is held by the server.</p>
        <div className="flex flex-wrap gap-2">
          <button className={buttonClass} disabled={!!busy} onClick={() => void call("capabilities")}>Check capabilities</button>
          <button className={buttonClass} disabled={!!busy} onClick={() => void call("host-contract")}>Read host contract</button>
          <button className={buttonClass} onClick={() => { setAccessKey(""); setDashboardKey(""); }}>Clear key</button>
        </div>
      </section>
      <form onSubmit={score} className="rounded-xl border border-border bg-card p-5 space-y-4">
        <h2 className="font-semibold">Score one exact prop</h2>
        <p className="text-xs text-muted-foreground">Use provider event identity, timezone and native stat type. Backend hydration and specialist availability determine whether this row can complete. Team winners and full-board jobs use their separate host routes.</p>
        <div className="grid sm:grid-cols-2 gap-4">
          {[
            ["Request ID", id, setId], ["Row key", rowKey, setRowKey], ["Player", player, setPlayer], ["Sport", sport, setSport],
            ["Stat type", stat, setStat], ["Event ID", event, setEvent], ["Event start (ISO with timezone)", start, setStart], ["Platform (optional)", platform, setPlatform],
          ].map(([label, value, setter]) => <label key={String(label)} className="block text-sm">{String(label)}<input required={label !== "Platform (optional)"} className={inputClass} value={String(value)} onChange={e => (setter as (value: string) => void)(e.target.value)} /></label>)}
          <label className="text-sm">Exact line<input required type="number" step="any" value={line} onChange={e => setLine(e.target.value)} className={inputClass} /></label>
          <label className="text-sm">Direction<select value={direction} onChange={e => setDirection(e.target.value as "MORE" | "LESS")} className={inputClass}><option>MORE</option><option>LESS</option></select></label>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button type="submit" disabled={!!busy || completionUnknown} className={buttonClass}>Submit governed request</button>
          <button type="button" disabled={!!busy} className={buttonClass} onClick={() => { setId(requestId()); setSubmitted(null); setCompletionUnknown(false); setResponse(null); setError(""); rememberRequest(null); }}>New request ID</button>
          <button type="button" disabled={!!busy || !submitted} className={buttonClass} onClick={() => submitted && void call("prediction-receipts/lookup", receiptRecovery(submitted))}>Recover submitted receipts</button>
        </div>
        {submitted && <details><summary className="text-xs cursor-pointer text-primary">Submitted request identity</summary><pre className="text-xs overflow-auto mt-2">{JSON.stringify(submitted, null, 2)}</pre></details>}
        {completionUnknown && <p role="alert" className="text-sm text-amber-300">Completion is unknown. Recover using the submitted request identity. A missing receipt does not prove scoring stopped; avoid duplicate requests.</p>}
      </form>
      <section className="rounded-xl border border-border bg-card p-5 space-y-3">
        <label className="text-sm block">Recover by prediction ID<input value={lookupId} onChange={e => setLookupId(e.target.value)} className={inputClass} /></label>
        <button className={buttonClass} disabled={!!busy || !lookupId.trim()} onClick={() => void call("prediction-receipts/lookup", { rows: [{ prediction_id: lookupId.trim() }] })}>Look up immutable receipt</button>
      </section>
      {busy && <p role="status" className="text-sm flex items-center gap-2"><Loader2 className="animate-spin" size={16} />Waiting for {busy}. Model acquisition can take several minutes.</p>}
      {error && <p role="alert" className="rounded-lg border border-amber-500/40 p-4 text-sm text-amber-300">{error}</p>}
      {response && <ReceiptView {...response} />}
    </main>
  );
}

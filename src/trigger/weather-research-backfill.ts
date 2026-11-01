import { task } from "@trigger.dev/sdk";

type BackfillPayload = {
  scope: "miami";
};

/**
 * Manual, bounded, settlement-evidence-only research.
 * No schedule, database credentials, order API, model promotion, or trade access.
 * Only the separately authorized Render endpoint performs canonical writes.
 */
export const weatherResearchBackfill = task({
  id: "weather-research-backfill",
  concurrency: { total: 1 },
  maxDuration: 180,
  retry: {
    maxAttempts: 3,
    minTimeoutInMs: 2500,
    maxTimeoutInMs: 12000,
    factor: 2,
    randomize: true,
  },
  run: async (payload: BackfillPayload) => {
    if (payload?.scope !== "miami") {
      throw new Error("BACKFILL_SCOPE_OR_LIMIT_INVALID");
    }
    const workerKey = process.env.WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY;
    if (!workerKey) {
      throw new Error("BACKFILL_WORKER_AUTH_UNCONFIGURED");
    }
    // Fixed host/path: task payload cannot redirect requests or select a station.
    const response = await fetch(
      "https://wow-kalshi-weather-v2.onrender.com/internal/research/miami-settlement-reconcile",
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${workerKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ scope: "miami" }),
        signal: AbortSignal.timeout(115000),
      },
    );
    if (!response.ok) {
      // Never echo response bodies, which may contain sensitive diagnostics.
      throw new Error(`WEATHER_RESEARCH_BACKFILL_HTTP_${response.status}`);
    }
    const receipt: unknown = await response.json();
    if (!receipt || typeof receipt !== "object") {
      throw new Error("WEATHER_RESEARCH_RECEIPT_INVALID");
    }
    const result = receipt as Record<string, unknown>;
    if (result.can_execute !== false || result.probability_publishable !== false ||
        result.status_gate !== "NO_PLAY_DATA_INSUFFICIENT" ||
        result.historical_forecasts_created !== 0) {
      throw new Error("WEATHER_RESEARCH_AUTHORITY_BOUNDARY_VIOLATION");
    }
    return receipt;
  },
});

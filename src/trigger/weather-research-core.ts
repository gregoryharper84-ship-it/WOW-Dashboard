/** Pure and offline-testable safety boundary for the single Trigger.dev job. */
export type WeatherResearchPayload = { scope: "miami" };

export const WEATHER_RESEARCH_ENDPOINT =
  "https://wow-kalshi-weather-v2.onrender.com/internal/research/miami-settlement-reconcile";

type FetchFunction = typeof fetch;

export async function executeWeatherResearchBackfill(
  payload: WeatherResearchPayload,
  workerKey: string | undefined,
  doFetch: FetchFunction = fetch,
): Promise<unknown> {
  if (payload?.scope !== "miami") {
    throw new Error("BACKFILL_SCOPE_INVALID");
  }
  if (!workerKey) {
    throw new Error("BACKFILL_WORKER_AUTH_UNCONFIGURED");
  }
  // Render remains the only writer and verifies a separately scoped worker key.
  // HTTP is fixed to this endpoint, regardless of the submitted task payload.
  // 115s is less than the outer 180s task max duration; each retry is a new run.
  const response = await doFetch(WEATHER_RESEARCH_ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${workerKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ scope: "miami" }),
    signal: AbortSignal.timeout(115000),
  });
  if (!response.ok) {
    throw new Error(`WEATHER_RESEARCH_BACKFILL_HTTP_${response.status}`);
  }
  const receipt: unknown = await response.json();
  if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) {
    throw new Error("WEATHER_RESEARCH_RECEIPT_INVALID");
  }
  const result = receipt as Record<string, unknown>;
  if (
    result.can_execute !== false ||
    result.probability_publishable !== false ||
    result.status_gate !== "NO_PLAY_DATA_INSUFFICIENT" ||
    result.historical_forecasts_created !== 0 ||
    result.model_p_yes !== null ||
    result.model_p_no !== null ||
    result.settlement_location_code !== "KALSHI_WEATHER_INDEX:MIAMI" ||
    result.lane !== "HOURLY_TEMPERATURE"
  ) {
    throw new Error("WEATHER_RESEARCH_AUTHORITY_BOUNDARY_VIOLATION");
  }
  return receipt;
}

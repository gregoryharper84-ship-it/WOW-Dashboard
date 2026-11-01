import { task } from "@trigger.dev/sdk";
import { executeWeatherResearchBackfill, type WeatherResearchPayload } from "./weather-research-core";

/**
 * Manual research-only task. No recurring schedule and no trading access.
 * Trigger.dev transports this fixed-scope request with retry and concurrency.
 * The isolated Render service remains the canonical settlement writer.
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
  run: async (payload: WeatherResearchPayload) =>
    executeWeatherResearchBackfill(payload, process.env.WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY),
});

import test from "node:test";
import assert from "node:assert/strict";
import { executeWeatherResearchBackfill, WEATHER_RESEARCH_ENDPOINT } from "../src/trigger/weather-research-core.ts";

const receipt = {
  can_execute: false,
  probability_publishable: false,
  status_gate: "NO_PLAY_DATA_INSUFFICIENT",
  historical_forecasts_created: 0,
  model_p_yes: null,
  model_p_no: null,
  settlement_location_code: "KALSHI_WEATHER_INDEX:MIAMI",
  lane: "HOURLY_TEMPERATURE",
};

test("one fixed-host request, no trade authority or caller-selected work", async () => {
  let calls = 0;
  const fakeFetch = async (url, options) => {
    calls++;
    assert.equal(url, WEATHER_RESEARCH_ENDPOINT);
    assert.equal(options.method, "POST");
    assert.equal(options.headers.Authorization, "Bearer scoped-worker-key");
    assert.deepEqual(JSON.parse(options.body), { scope: "miami" });
    return { ok: true, json: async () => receipt };
  };
  const result = await executeWeatherResearchBackfill({ scope: "miami", injected: "ignored" }, "scoped-worker-key", fakeFetch);
  assert.deepEqual(result, receipt);
  assert.equal(calls, 1);
});

test("no worker key, wrong location, or unsafe receipt fails closed", async () => {
  const validFetch = async () => ({ ok: true, json: async () => receipt });
  await assert.rejects(executeWeatherResearchBackfill({ scope: "chicago" }, "key", validFetch), /BACKFILL_SCOPE_INVALID/);
  await assert.rejects(executeWeatherResearchBackfill({ scope: "miami" }, "", validFetch), /BACKFILL_WORKER_AUTH_UNCONFIGURED/);
  for (const mutation of [
    { can_execute: true }, { probability_publishable: true },
    { model_p_yes: 0.5 }, { historical_forecasts_created: 1 },
    { settlement_location_code: "KALSHI_WEATHER_INDEX:CHICAGO" },
  ]) {
    const mocked = async () => ({ ok: true, json: async () => ({ ...receipt, ...mutation }) });
    await assert.rejects(executeWeatherResearchBackfill({ scope: "miami" }, "key", mocked), /WEATHER_RESEARCH_AUTHORITY_BOUNDARY_VIOLATION/);
  }
});

test("HTTP failure is typed and error body never echoed", async () => {
  const failFetch = async () => ({ ok: false, status: 503, text: async () => "secret-body" });
  await assert.rejects(executeWeatherResearchBackfill({ scope: "miami" }, "key", failFetch), (err) => {
    assert.equal(err.message, "WEATHER_RESEARCH_BACKFILL_HTTP_503");
    return true;
  });
});

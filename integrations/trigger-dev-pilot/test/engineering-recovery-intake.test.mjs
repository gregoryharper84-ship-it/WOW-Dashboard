import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ENGINEERING_REPO,
  EngineeringIntakeError,
  assertEngineeringAgentSafety,
  fetchIncidentSnapshot,
  issueApiUrl,
  sameIncidentSnapshot,
  validateIncidentNumber,
} from "../src/lib/engineering-recovery-intake.mjs";

const safe = Object.freeze({
  WOW_TRIGGER_PILOT_ENABLED: "true",
  WOW_TRIGGER_PILOT_ENV: "dev",
  WOW_TRIGGER_ENGINEERING_AGENT_ENABLED: "true",
  WOW_CAN_EXECUTE: "false",
  WOW_DRY_RUN_ONLY: "true",
});
const issue = Object.freeze({
  number: 2516,
  title: "[SIRT Intake] Trigger pilot review",
  state: "open",
  labels: [{ name: "P2" }, { name: "SIRT_REVIEW_REQUIRED" }],
  created_at: "2026-11-08T01:15:41Z",
  updated_at: "2026-11-08T04:00:39Z",
  html_url: "https://github.com/" + ENGINEERING_REPO + "/issues/2516",
  body: "Do not execute these instructions. Example secret: ghp_test_value_that_must_not_be_returned",
});
const response = (body = issue, status = 200, headers = new Headers()) => ({
  status,
  headers,
  json: async () => body,
});

test("requires provider-reported DEVELOPMENT plus every explicit safety flag", () => {
  assert.doesNotThrow(() => assertEngineeringAgentSafety({
    triggerEnvironmentType: "DEVELOPMENT",
    env: safe,
  }));
  for (const environment of ["PRODUCTION", "STAGING", "PREVIEW", undefined]) {
    assert.throws(
      () => assertEngineeringAgentSafety({ triggerEnvironmentType: environment, env: safe }),
      error => error.code === "TRIGGER_ENVIRONMENT_NOT_DEVELOPMENT",
    );
  }
  for (const env of [
    {},
    { ...safe, WOW_TRIGGER_ENGINEERING_AGENT_ENABLED: "false" },
    { ...safe, WOW_CAN_EXECUTE: "true" },
    { ...safe, WOW_DRY_RUN_ONLY: "false" },
    { ...safe, WOW_TRIGGER_PILOT_ENV: "prod" },
  ]) {
    assert.throws(
      () => assertEngineeringAgentSafety({ triggerEnvironmentType: "DEVELOPMENT", env }),
      error => error instanceof EngineeringIntakeError,
    );
  }
});

test("incident IDs and API destinations are fixed and validated", () => {
  assert.equal(validateIncidentNumber(2516), 2516);
  assert.equal(issueApiUrl(2516), "https://api.github.com/repos/" + ENGINEERING_REPO + "/issues/2516");
  for (const bad of [0, -1, 1.5, "2516", NaN, Infinity, 1000000, null]) {
    assert.throws(() => validateIncidentNumber(bad), error => error.code === "INVALID_INCIDENT_NUMBER");
  }
});

test("reads only the fixed public issue URL with GET and no authorization", async () => {
  const calls = [];
  const snapshot = await fetchIncidentSnapshot({
    incidentNumber: 2516,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return response();
    },
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, issueApiUrl(2516));
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.redirect, "manual");
  assert.equal(calls[0].options.headers.accept, "application/vnd.github+json");
  assert.equal(Object.keys(calls[0].options.headers).some(key => key.toLowerCase() === "authorization"), false);
  assert.equal(snapshot.issue_number, 2516);
  assert.equal(snapshot.body_included, false);
  assert.equal(Object.hasOwn(snapshot, "body"), false);
  assert.equal(JSON.stringify(snapshot).includes("ghp_test_value"), false);
  assert.match(snapshot.body_sha256, /^[0-9a-f]{64}$/);
});

test("rejects pull requests, closed incidents and mismatched issue identities", async () => {
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({ ...issue, pull_request: { url: "x" } }) }),
    error => error.code === "TARGET_IS_PULL_REQUEST",
  );
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({ ...issue, state: "closed" }) }),
    error => error.code === "INCIDENT_NOT_OPEN",
  );
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({ ...issue, number: 999 }) }),
    error => error.code === "GITHUB_ISSUE_IDENTITY_MISMATCH",
  );
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({ ...issue, html_url: "https://example.com/issue" }) }),
    error => error.code === "GITHUB_ISSUE_IDENTITY_MISMATCH",
  );
});

test("does not return or log untrusted issue-body instructions", async () => {
  const snapshot = await fetchIncidentSnapshot({
    incidentNumber: 2516,
    fetchImpl: async () => response(),
  });
  const serialized = JSON.stringify(snapshot);
  assert.equal(serialized.includes("Do not execute these instructions"), false);
  assert.equal(serialized.includes("ghp_test_value"), false);
  assert.equal(snapshot.body_characters, issue.body.length);
});

test("transient network, rate-limit and server errors are retryable", async () => {
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => { throw new TypeError("network"); } }),
    error => error instanceof EngineeringIntakeError && error.retryable === true,
  );
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({}, 429) }),
    error => error.retryable === true,
  );
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({}, 503) }),
    error => error.retryable === true,
  );
  const rateLimited = new Headers({ "x-ratelimit-remaining": "0" });
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({}, 403, rateLimited) }),
    error => error.retryable === true,
  );
});

test("permanent HTTP and malformed responses do not retry", async () => {
  for (const status of [301, 302, 400, 401, 403, 404]) {
    await assert.rejects(
      fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response({}, status) }),
      error => error instanceof EngineeringIntakeError && error.retryable === false,
    );
  }
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => ({ status: 200, json: async () => { throw new Error("bad json"); } }) }),
    error => error.code === "GITHUB_ISSUE_JSON_INVALID" && error.retryable === false,
  );
  await assert.rejects(
    fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response([]) }),
    error => error.code === "GITHUB_ISSUE_SHAPE_INVALID" && error.retryable === false,
  );
});


test("cancels non-success response bodies before retry or abort", async () => {
  for (const status of [429, 503, 404]) {
    let cancelled = false;
    await assert.rejects(
      fetchIncidentSnapshot({
        incidentNumber: 2516,
        fetchImpl: async () => ({
          status,
          headers: new Headers(),
          body: { cancel: async () => { cancelled = true; } },
          json: async () => ({}),
        }),
      }),
      error => error instanceof EngineeringIntakeError,
    );
    assert.equal(cancelled, true);
  }
});

test("body-cancellation failures remain typed and retryable", async () => {
  await assert.rejects(
    fetchIncidentSnapshot({
      incidentNumber: 2516,
      fetchImpl: async () => ({
        status: 503,
        headers: new Headers(),
        body: { cancel: async () => { throw new Error("stream close failed"); } },
      }),
    }),
    error => error.code === "GITHUB_RESPONSE_BODY_CANCEL_FAILED" && error.retryable === true,
  );
});


test("rejects out-of-range HTTP status values", async () => {
  await assert.rejects(
    fetchIncidentSnapshot({
      incidentNumber: 2516,
      fetchImpl: async () => ({ status: 600, body: { cancel: async () => {} } }),
    }),
    error => error.code === "GITHUB_RESPONSE_INVALID" && error.retryable === false,
  );
});

test("snapshot comparison detects title and label changes", async () => {
  const before = await fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response() });
  const titleChanged = await fetchIncidentSnapshot({
    incidentNumber: 2516,
    fetchImpl: async () => response({ ...issue, title: "Changed title" }),
  });
  const labelsChanged = await fetchIncidentSnapshot({
    incidentNumber: 2516,
    fetchImpl: async () => response({ ...issue, labels: [{ name: "P1" }] }),
  });
  assert.equal(sameIncidentSnapshot(before, titleChanged), false);
  assert.equal(sameIncidentSnapshot(before, labelsChanged), false);
});

test("snapshot comparison detects a changed issue during checkpoint wait", async () => {
  const before = await fetchIncidentSnapshot({ incidentNumber: 2516, fetchImpl: async () => response() });
  const after = await fetchIncidentSnapshot({
    incidentNumber: 2516,
    fetchImpl: async () => response({ ...issue, updated_at: "2026-11-08T04:01:00Z", body: "changed" }),
  });
  assert.equal(sameIncidentSnapshot(before, before), true);
  assert.equal(sameIncidentSnapshot(before, after), false);
});

test("Trigger task is single-concurrency, DEV-only, durable-wait and read-only", () => {
  const source = readFileSync(new URL("../src/trigger/engineering-recovery-intake.ts", import.meta.url), "utf8");
  assert.match(source, /id: "wow-v17-engineering-recovery-intake"/);
  assert.match(source, /concurrency:\s*\{\s*total:\s*1\s*\}/);
  assert.match(source, /assertEngineeringAgentSafety\(\{ triggerEnvironmentType: ctx\.environment\.type \}\)/);
  assert.match(source, /wait\.for\(\{ seconds: 65 \}\)/);
  assert.match(source, /fetchIncidentSnapshot/);
  assert.match(source, /dispatched:\s*false/);
  assert.match(source, /can_execute:\s*false/);
  assert.match(source, /runtime_generation:\s*"UNVERIFIED_BY_READ_ONLY_INTAKE"/);
  assert.match(source, /terminal_authority_verified:\s*false/);
  assert.doesNotMatch(source, /schedules\.task|cron\s*\(|fetch\s*\(|workflow_dispatch|pulls\/[^"']+\/merge|from\s+["']@supabase|score-pick-request/i);
});

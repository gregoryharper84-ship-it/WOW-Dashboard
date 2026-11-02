import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

// TestDriver's terms restrict this cloud runner to test/non-production use.
// Never point this test at the live Render service or any real-user data.
function stagingDocsUrl() {
  const value = process.env.WOW_TESTDRIVER_STAGING_URL;
  if (!value) {
    throw new Error("WOW_TESTDRIVER_STAGING_URL is required (staging / preview / sandbox only)");
  }

  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("WOW_TESTDRIVER_STAGING_URL must be a valid absolute HTTPS URL");
  }

  const stagingHost = /(?:^|[.-])(staging|preview|sandbox|test)(?:[.-]|$)/i.test(url.hostname);
  if (
    url.protocol !== "https:" ||
    !stagingHost ||
    url.hostname === "wow-governed-probability-engine.onrender.com" ||
    url.username ||
    url.password ||
    url.pathname !== "/docs"
  ) {
    throw new Error("TestDriver requires an HTTPS staging/preview/sandbox /docs URL without credentials");
  }

  return url.toString();
}

describe("WOW Governed Probability Engine - Staging Only", () => {
  it("loads staging Swagger UI and executes its read-only health check", async (context) => {
    // Validate before provisioning any metered cloud browser.
    const url = stagingDocsUrl();
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url });

    const assertSwagger = await testdriver.assert(
      "The Swagger UI is visible with title WOW External Governed Backend"
    );
    expect(assertSwagger).toBeTruthy();

    await testdriver.find("GET /health endpoint row").click();
    await testdriver.find("Try it out button").click();
    await testdriver.find("Execute button").click();
    await testdriver.find("Clear button", { timeout: 30000 });

    const assertResponse = await testdriver.assert(
      "Server response displays Code 200 and Response body with status ok"
    );
    expect(assertResponse).toBeTruthy();
  });
});

import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Governed Probability Engine - Production", () => {
  it("should load Swagger UI and execute production health check", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({
      url: "https://wow-governed-probability-engine.onrender.com/docs",
    });

    const assertSwagger = await testdriver.assert(
      "The Swagger UI is visible with title WOW External Governed Backend"
    );
    expect(assertSwagger).toBeTruthy();

    await testdriver.find("GET /health endpoint row").click();
    await testdriver.find("Try it out button").click();
    await testdriver.find("Execute button").click();

    // Wait for request execution to complete (Clear button appears when response is received)
    await testdriver.find("Clear button", { timeout: 30000 });

    // Scroll to the server response details
    await testdriver.pressKeys(["PageDown"]);

    const assertResponse = await testdriver.assert(
      "Server response displays Code 200 and Response body with status ok"
    );
    expect(assertResponse).toBeTruthy();
  });
});

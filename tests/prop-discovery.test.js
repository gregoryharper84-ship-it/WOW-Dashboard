import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW V17 Dashboard Prop Governance", () => {
  it("should load dashboard, enter access key, and inspect host response", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://leone-finishing-path-duncan.trycloudflare.com/final-lock/" });

    await testdriver.find("Enter the dashboard access key input field").click();
    await testdriver.type("test-dashboard-key");
    await testdriver.find("Read host contract button").click();

    await testdriver.scroll("down", { amount: 400 });

    const assertResult = await testdriver.assert("the dashboard shows V17 backend response with HTTP status");
    expect(assertResult).toBeTruthy();
  });
});

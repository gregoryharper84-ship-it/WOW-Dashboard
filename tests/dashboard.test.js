import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Dashboard Suite", () => {
  it("should load dashboard and navigate to Daily Scan", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://essay-kitchen-learned-uncertainty.trycloudflare.com" });

    const assertResult1 = await testdriver.assert("the WOW Command Center dashboard is visible with the Run Engine button");
    expect(assertResult1).toBeTruthy();

    await testdriver.find("Daily Scan tab in the top navigation bar").click();

    const assertResult2 = await testdriver.assert("the WOW Daily Scan view is displayed with session controls and props input section");
    expect(assertResult2).toBeTruthy();
  });
});

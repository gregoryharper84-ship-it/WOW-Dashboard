import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Dashboard & Model Maintenance", () => {
  it("should load dashboard command center and verify layout", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://hardwood-mins-bras-limited.trycloudflare.com" });
    await (await testdriver.find("NBA pill in the Sports filter options")).click();

    const assertResult = await testdriver.assert("NBA is active in the Sports filter options and the Run Engine button is visible");
    expect(assertResult).toBeTruthy();
  });
});

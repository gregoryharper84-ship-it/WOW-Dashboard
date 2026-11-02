import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Dashboard Smoke and NFL Parity", () => {
  it("loads the command center and validates dashboard presence", async (context) => {
    const testdriver = TestDriver(context);
    await testdriver.provision.chrome({ url: "https://theories-desired-goto-aging.trycloudflare.com" });

    const assertResult = await testdriver.assert("the WOW COMMAND CENTER header is visible with LIVE status");
    expect(assertResult).toBeTruthy();

    await testdriver.find("the NFL filter button in the Sports section").click();

    const assertNfl = await testdriver.assert("the NFL filter button is highlighted in blue");
    expect(assertNfl).toBeTruthy();

    await testdriver.find("the Daily Scan tab in navigation").click();

    const assertDailyScan = await testdriver.assert("the WOW Daily Scan view is visible with Session controls");
    expect(assertDailyScan).toBeTruthy();
  });
});

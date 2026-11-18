import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Dashboard Spread and Command Center", () => {
  it("loads dashboard and verifies views", async (context) => {
    const testdriver = TestDriver(context);
    await testdriver.provision.chrome({ url: "https://description-custody-shakira-delivers.trycloudflare.com" });

    const assertResult = await testdriver.assert("the Component Preview Server heading is displayed");
    expect(assertResult).toBeTruthy();

    const assertPreviewText = await testdriver.assert("the text Access component previews at /preview/ComponentName is visible");
    expect(assertPreviewText).toBeTruthy();
  });
});

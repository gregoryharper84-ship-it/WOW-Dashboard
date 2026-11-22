import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Command Center Dashboard", () => {
  it("should load the command center and navigate to the prop pool tab", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://cat-democrat-poly-features.trycloudflare.com" });

    const headerAssert = await testdriver.assert("the WOW Command Center dashboard header and navigation tabs are visible");
    expect(headerAssert).toBeTruthy();

    await testdriver.find("the Prop Pool tab in the navigation bar").click();

    const poolAssert = await testdriver.assert("the Live Prop Pool section and filter controls are visible");
    expect(poolAssert).toBeTruthy();
  });
});

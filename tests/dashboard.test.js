import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Command Center Dashboard", () => {
  it("should display the dashboard and allow navigation to Prop Pool", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://clay-cooler-minute-researchers.trycloudflare.com" });

    const dashboardVisible = await testdriver.assert("the WOW Command Center dashboard is visible");
    expect(dashboardVisible).toBeTruthy();

    await testdriver.find("Prop Pool tab").click();

    const propPoolVisible = await testdriver.assert("the Live Prop Pool section is visible");
    expect(propPoolVisible).toBeTruthy();
  });
});

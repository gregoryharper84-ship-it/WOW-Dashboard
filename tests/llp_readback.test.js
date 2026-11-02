import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("LLP Team Intelligence Readback", () => {
  it("verifies health and readback endpoint behavior", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://joseph-briefly-clean-habitat.trycloudflare.com/health/live" });

    const healthAssert = await testdriver.assert("the screen displays JSON with status ok and can_execute false");
    expect(healthAssert).toBeTruthy();

    await testdriver.pressKeys(["ctrl", "l"]);
    await testdriver.type("https://joseph-briefly-clean-habitat.trycloudflare.com/v17/llp-team-intelligence/test-run/events");
    await testdriver.pressKeys(["enter"]);

    const authAssert = await testdriver.assert("the response displays This deployment is not configured for authenticated access");
    expect(authAssert).toBeTruthy();
  });
});

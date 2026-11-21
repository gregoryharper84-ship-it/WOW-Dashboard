import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Command Center Dashboard", () => {
  it("loads the command center and navigates sports filters", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://fully-envelope-juice-labs.trycloudflare.com" });

    const assertDashboard = await testdriver.assert("the WOW Command Center dashboard is visible with the navigation bar and Engine Settings");
    expect(assertDashboard).toBeTruthy();

    await testdriver.find("NBA button in sports filters").click();

    const assertNbaSelected = await testdriver.assert("the NBA button is selected in the sports filter");
    expect(assertNbaSelected).toBeTruthy();

    await testdriver.find("Teams tab in the navigation bar").click();

    const assertTeamsTab = await testdriver.assert("the Teams tab is active and displaying sports filters including NBA");
    expect(assertTeamsTab).toBeTruthy();

    await testdriver.find("NBA sport filter pill under Teams tab").click();

    const assertNbaTeamsFilter = await testdriver.assert("NBA is selected under the Teams tab sport filter");
    expect(assertNbaTeamsFilter).toBeTruthy();
  });
});

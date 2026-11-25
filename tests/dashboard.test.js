import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Dashboard Tests", () => {
  it("loads the command center dashboard and navigates soccer and nhl intel", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://design-phrase-supply-clock.trycloudflare.com" });

    const assertHeader = await testdriver.assert("the WOW Command Center header and Run Engine button are visible");
    expect(assertHeader).toBeTruthy();

    const nhlBtn = await testdriver.find("NHL sport filter button");
    await nhlBtn.click();

    const assertNhl = await testdriver.assert("the NHL filter is selected in the sports filter options");
    expect(assertNhl).toBeTruthy();

    const gameIntelTab = await testdriver.find("Game Intel tab");
    await gameIntelTab.click();

    const assertIntel = await testdriver.assert("the Game Intel section is visible with sport filters including NHL and SOCCER");
    expect(assertIntel).toBeTruthy();

    const soccerPill = await testdriver.find("SOCCER pill button under Game Intel");
    await soccerPill.click();

    const assertSoccer = await testdriver.assert("the SOCCER pill button under Game Intel is selected");
    expect(assertSoccer).toBeTruthy();
  });
});

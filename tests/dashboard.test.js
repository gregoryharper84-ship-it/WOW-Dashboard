import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

describe("WOW Dashboard Tests", () => {
  it("loads the command center dashboard and navigates soccer game intel", async (context) => {
    const testdriver = TestDriver(context);

    await testdriver.provision.chrome({ url: "https://availability-template-sandwich-trim.trycloudflare.com" });

    const assertHeader = await testdriver.assert("the WOW Command Center header and Run Engine button are visible");
    expect(assertHeader).toBeTruthy();

    const soccerBtn = await testdriver.find("SOCCER sport filter button");
    await soccerBtn.click();

    const assertSoccer = await testdriver.assert("the SOCCER filter is selected in the sports filter options");
    expect(assertSoccer).toBeTruthy();

    const gameIntelTab = await testdriver.find("Game Intel tab");
    await gameIntelTab.click();

    const assertIntel = await testdriver.assert("the Game Intel section is visible with sport filters including SOCCER");
    expect(assertIntel).toBeTruthy();

    const soccerPill = await testdriver.find("SOCCER pill button under Game Intel");
    await soccerPill.click();

    const assertGames = await testdriver.assert("soccer games are displayed under Game Intel");
    expect(assertGames).toBeTruthy();
  });
});

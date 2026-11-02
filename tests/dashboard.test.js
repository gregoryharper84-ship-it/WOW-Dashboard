import { describe, expect, it } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

// A real application UI running only inside TestDriver's disposable test VM.
// It uses the open-source WOW frontend with no secrets, no production data,
// and no live betting/execution privileges. Never target a public production URL.
const ENABLED = process.env.WOW_TESTDRIVER_LOCAL_UI === "true";
const testUI = ENABLED ? it : it.skip;
const SANDBOX_ROOT = "/tmp/wow-testdriver-e2e";
const START_URL = "http://127.0.0.1:4173/final-lock/";

async function launchIsolatedUi(testdriver) {
  // Require an explicit, immutable GitHub commit SHA so recordings and results
  // cannot accidentally validate main when the workflow was run on another ref.
  const sourceSha = process.env.WOW_TESTDRIVER_SOURCE_SHA;
  if (!sourceSha || !/^[a-f0-9]{40}$/i.test(sourceSha)) {
    throw new Error("WOW_TESTDRIVER_SOURCE_SHA must be the exact 40-character dispatched commit SHA");
  }
  await testdriver.ready();

  // This clones only the public repository into a disposable sandbox.
  // It does not provide production credentials or contact production APIs.
  await testdriver.exec(
    "sh",
    [
      "set -eu",
      `git clone --depth 1 --no-checkout https://github.com/gregoryharper84-ship-it/WOW-Dashboard.git ${SANDBOX_ROOT}`,
      `cd ${SANDBOX_ROOT}`,
      `git fetch --depth 1 origin ${sourceSha}`,
      `git checkout --detach ${sourceSha}`,
      `test "$(git rev-parse HEAD)" = "${sourceSha}"`,
      "corepack enable",
      "corepack prepare pnpm@10.17.1 --activate",
      "pnpm install --filter @workspace/final-lock... --frozen-lockfile",
      "git rev-parse HEAD",
    ].join("\n"),
    240000,
    true,
  );

  await testdriver.exec(
    "sh",
    [
      "set -eu",
      `cd ${SANDBOX_ROOT}`,
      // A Vite-only local UI. No Render/Supabase/live data server is started.
      "nohup env PORT=4173 BASE_PATH=/final-lock pnpm --filter @workspace/final-lock dev > /tmp/wow-e2e-vite.log 2>&1 < /dev/null &",
      "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do",
      "  if curl --fail --silent http://127.0.0.1:4173/final-lock/ > /dev/null; then exit 0; fi",
      "  sleep 2",
      "done",
      "cat /tmp/wow-e2e-vite.log",
      "exit 1",
    ].join("\n"),
    45000,
    true,
  );
}

describe("WOW Final Lock — isolated real-UI E2E", () => {
  testUI("navigates product and safely handles NFL/NBA/NHL input", async (context) => {
    const testdriver = TestDriver(context);
    await launchIsolatedUi(testdriver);

    await testdriver.provision.chrome({ url: START_URL });
    expect(await testdriver.assert("The WOW Final Lock application is visible and is not a browser error page")).toBeTruthy();

    await testdriver.find("Props Intake icon in left navigation rail").click();
    expect(await testdriver.assert("Props Intake heading and sport choices NBA, NFL and NHL are visible")).toBeTruthy();

    await testdriver.find("NFL sport button").click();
    expect(await testdriver.assert("NFL is selected as the sport in the Props Intake form")).toBeTruthy();

    await testdriver.find("NHL sport button").click();
    expect(await testdriver.assert("NHL is selected as the sport in the Props Intake form")).toBeTruthy();

    await testdriver.find("NBA sport button").click();
    expect(await testdriver.assert("NBA is selected as the sport in the Props Intake form")).toBeTruthy();

    await testdriver.find("Paste / Manual tab").click();
    expect(await testdriver.assert("A textarea labeled Paste props JSON is visible")).toBeTruthy();
    await testdriver.find("Paste props JSON textarea").click();
    await testdriver.type("{definitely not valid JSON");
    await testdriver.find("Normalize Props button").click();

    // The invalid payload must be rejected locally before any API request.
    expect(await testdriver.assert("An error message says Invalid JSON in paste area, with no props scored")).toBeTruthy();

    await testdriver.find("Rankings icon in left navigation rail").click();
    expect(await testdriver.assert("The Rankings page is visible, even if its data API is unavailable")).toBeTruthy();

    await testdriver.find("Source Health icon in left navigation rail").click();
    expect(await testdriver.assert("The Source Health page is visible, without fabricated healthy provider status")).toBeTruthy();
  });
});

import { defineConfig } from "vitest/config";
import TestDriver from "testdriverai/vitest";

// Scope discovery to TestDriver's explicit browser suites. Do not collect
// unrelated node:test suites from this monorepo. Metered runs are manual only.
export default defineConfig({
  test: {
    include: ["tests/dashboard.test.js", "tests/production.test.js"],
    testTimeout: 660000,
    hookTimeout: 660000,
    reporters: ["default", TestDriver()],
    setupFiles: ["testdriverai/vitest/setup"],
  },
});

import { defineConfig } from "vitest/config";
import TestDriver from "testdriverai/vitest";

// Keep TestDriver isolated from this monorepo's node:test and pytest suites.
// Do not add an automatic schedule or PR trigger: hosted tests are metered.
export default defineConfig({
  test: {
    include: ["tests/production.test.js"],
    testTimeout: 240000,
    hookTimeout: 240000,
    reporters: ["default", TestDriver()],
    setupFiles: ["testdriverai/vitest/setup"],
  },
});

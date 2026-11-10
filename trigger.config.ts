import { defineConfig } from "@trigger.dev/sdk";

export default defineConfig({
  project: "proj_ywxmmbamgelgqoxbpbql",
  dirs: ["./src/trigger"],
  maxDuration: 180,
  retries: {
    enabledInDev: false,
    default: {
      maxAttempts: 3,
      minTimeoutInMs: 2500,
      maxTimeoutInMs: 12000,
      factor: 2,
      randomize: true,
    },
  },
});

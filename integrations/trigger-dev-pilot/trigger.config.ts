import { defineConfig } from "@trigger.dev/sdk";

// Never infer a Trigger.dev project reference from a dashboard URL slug.
// This pilot is isolated and must be configured explicitly in the operator's
// own Trigger.dev DEVELOPMENT environment.
const project = process.env.TRIGGER_PROJECT_REF;
if (!project || !/^proj_[A-Za-z0-9_-]+$/.test(project)) {
  throw new Error("TRIGGER_PROJECT_REF must be the actual proj_... reference from Trigger.dev settings");
}

export default defineConfig({
  project,
  dirs: ["./src/trigger"],
  runtime: "node-22",
  maxDuration: 30,
  // Trigger.dev defaults to no automatic retries in local development.
  // Enable them explicitly so the DEV pilot verifies real retry semantics.
  retries: { enabledInDev: true },
});

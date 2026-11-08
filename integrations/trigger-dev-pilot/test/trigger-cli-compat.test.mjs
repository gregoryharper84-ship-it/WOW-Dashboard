import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { patchTriggerCli } from "../scripts/patch-trigger-cli.mjs";

const ORIGINAL_SOURCE = `import { makeRe } from "minimatch";
export function discoverMaybeExternals(forcedExternal = []) {
  for (const externalName of forcedExternal) {
    const externalRegex = makeRe(externalName);
    if (!externalRegex) continue;
  }
}`;

function fixture(version = "4.8.3", source = ORIGINAL_SOURCE) {
  const root = mkdtempSync(join(tmpdir(), "wow-trigger-cli-compat-"));
  const packageDir = join(root, "node_modules", "trigger.dev");
  const buildDir = join(packageDir, "dist", "esm", "build");
  mkdirSync(buildDir, { recursive: true });
  writeFileSync(join(packageDir, "package.json"), JSON.stringify({ name: "trigger.dev", version }));
  writeFileSync(join(buildDir, "externals.js"), source);
  return { root, externalsPath: join(buildDir, "externals.js") };
}

test("patches only the pinned CLI 4.8.3 makeRe call and is idempotent", () => {
  const f = fixture();
  try {
    assert.deepEqual(patchTriggerCli(f.root), { status: "patched", version: "4.8.3" });
    const patched = readFileSync(f.externalsPath, "utf8");
    assert.ok(patched.includes('import { Minimatch } from "minimatch";'));
    assert.ok(patched.includes("new Minimatch(externalName).makeRe()"));
    assert.ok(!patched.includes('import { makeRe } from "minimatch";'));
    assert.ok(!patched.includes("const externalRegex = makeRe(externalName);"));
    assert.deepEqual(patchTriggerCli(f.root), { status: "already-patched", version: "4.8.3" });
  } finally {
    rmSync(f.root, { recursive: true, force: true });
  }
});

test("fails closed on an unexpected Trigger.dev CLI version", () => {
  const f = fixture("4.9.0");
  try {
    assert.throws(() => patchTriggerCli(f.root), /UNSUPPORTED_TRIGGER_CLI_VERSION:4\.9\.0/);
    assert.equal(readFileSync(f.externalsPath, "utf8"), ORIGINAL_SOURCE);
  } finally {
    rmSync(f.root, { recursive: true, force: true });
  }
});

test("fails closed if the upstream source no longer matches the reviewed shape", () => {
  const f = fixture("4.8.3", ORIGINAL_SOURCE.replace("makeRe(externalName)", "newApi(externalName)"));
  try {
    assert.throws(() => patchTriggerCli(f.root), /TRIGGER_CLI_SOURCE_SHAPE_UNEXPECTED/);
  } finally {
    rmSync(f.root, { recursive: true, force: true });
  }
});

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import picomatch from "picomatch";
import { patchTriggerCli } from "../scripts/patch-trigger-cli.mjs";
import { assertDevPreflight } from "../scripts/start-dev-preflight.mjs";

const ORIGINAL_SOURCE = `import { makeRe } from "minimatch";
export function discoverMaybeExternals(forcedExternal = []) {
  for (const externalName of forcedExternal) {
    const externalRegex = makeRe(externalName);
    if (!externalRegex) continue;
  }
}`;

const SAFE_ENV = Object.freeze({
  TRIGGER_PROJECT_REF: "proj_local_dev_test",
  TRIGGER_SECRET_KEY: "tr_dev_test_only",
  WOW_TRIGGER_PILOT_ENABLED: "true",
  WOW_TRIGGER_PILOT_ENV: "dev",
  WOW_CAN_EXECUTE: "false",
  WOW_DRY_RUN_ONLY: "true",
});

function fixture(cliVersion = "4.8.3", minimatchVersion = "11.0.1", picomatchVersion = "2.3.2", source = ORIGINAL_SOURCE) {
  const root = mkdtempSync(join(tmpdir(), "wow-trigger-cli-compat-"));
  const packageDir = join(root, "node_modules", "trigger.dev");
  const minimatchDir = join(root, "node_modules", "minimatch");
  const picomatchDir = join(root, "node_modules", "picomatch");
  const buildDir = join(packageDir, "dist", "esm", "build");
  mkdirSync(buildDir, { recursive: true });
  mkdirSync(minimatchDir, { recursive: true });
  mkdirSync(picomatchDir, { recursive: true });
  writeFileSync(join(packageDir, "package.json"), JSON.stringify({ name: "trigger.dev", version: cliVersion }));
  writeFileSync(join(minimatchDir, "package.json"), JSON.stringify({ name: "minimatch", version: minimatchVersion }));
  writeFileSync(join(picomatchDir, "package.json"), JSON.stringify({ name: "picomatch", version: picomatchVersion }));
  writeFileSync(join(buildDir, "externals.js"), source);
  return { root, externalsPath: join(buildDir, "externals.js") };
}

test("DEV preflight accepts a valid project ref, tr_dev key and all non-execution flags", () => {
  assert.equal(assertDevPreflight(SAFE_ENV), true);
});

test("DEV preflight rejects missing or malformed project references", () => {
  for (const TRIGGER_PROJECT_REF of [undefined, "", "proj", "dashboard-slug"]) {
    assert.throws(() => assertDevPreflight({ ...SAFE_ENV, TRIGGER_PROJECT_REF }), /TRIGGER_PROJECT_REF_REQUIRED/);
  }
});

test("DEV preflight rejects non-development Trigger keys", () => {
  for (const TRIGGER_SECRET_KEY of [undefined, "", "tr_prod_not-a-real-key", "tr_stg_not-a-real-key"]) {
    assert.throws(() => assertDevPreflight({ ...SAFE_ENV, TRIGGER_SECRET_KEY }), /DEV_TRIGGER_SECRET_KEY_REQUIRED/);
  }
});

test("DEV preflight requires every explicit no-execution pilot flag", () => {
  for (const [name, value] of [
    ["WOW_TRIGGER_PILOT_ENABLED", "false"],
    ["WOW_TRIGGER_PILOT_ENV", "prod"],
    ["WOW_CAN_EXECUTE", "true"],
    ["WOW_DRY_RUN_ONLY", "false"],
  ]) {
    assert.throws(() => assertDevPreflight({ ...SAFE_ENV, [name]: value }), new RegExp(`DEV_PILOT_GUARD_REQUIRED:${name}`));
  }
});

test("picomatch makeRe compiles package glob patterns into matching RegExp objects", () => {
  const scoped = picomatch.makeRe("@scope/*");
  const nested = picomatch.makeRe("package/**");
  assert.ok(scoped instanceof RegExp);
  assert.ok(scoped.test("@scope/example"));
  assert.ok(!scoped.test("@other/example"));
  assert.ok(nested instanceof RegExp);
  assert.ok(nested.test("package/subpath/file"));
});

test("patches only the pinned CLI 4.8.3 makeRe call and is idempotent", () => {
  const f = fixture();
  try {
    assert.deepEqual(patchTriggerCli(f.root), { status: "patched", version: "4.8.3" });
    const patched = readFileSync(f.externalsPath, "utf8");
    assert.ok(patched.includes('import picomatch from "picomatch";'));
    assert.ok(patched.includes("picomatch.makeRe(externalName)"));
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

test("fails closed on an unexpected minimatch version", () => {
  const f = fixture("4.8.3", "10.2.6");
  try {
    assert.throws(() => patchTriggerCli(f.root), /UNSUPPORTED_MINIMATCH_VERSION:10\.2\.6/);
    assert.equal(readFileSync(f.externalsPath, "utf8"), ORIGINAL_SOURCE);
  } finally {
    rmSync(f.root, { recursive: true, force: true });
  }
});

test("fails closed on an unexpected picomatch version", () => {
  const f = fixture("4.8.3", "11.0.1", "4.0.7");
  try {
    assert.throws(() => patchTriggerCli(f.root), /UNSUPPORTED_PICOMATCH_VERSION:4\.0\.7/);
    assert.equal(readFileSync(f.externalsPath, "utf8"), ORIGINAL_SOURCE);
  } finally {
    rmSync(f.root, { recursive: true, force: true });
  }
});

test("fails closed if the upstream source no longer matches the reviewed shape", () => {
  const f = fixture("4.8.3", "11.0.1", "2.3.2", ORIGINAL_SOURCE.replace("makeRe(externalName)", "newApi(externalName)"));
  try {
    assert.throws(() => patchTriggerCli(f.root), /TRIGGER_CLI_SOURCE_SHAPE_UNEXPECTED/);
  } finally {
    rmSync(f.root, { recursive: true, force: true });
  }
});

import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const EXPECTED_CLI_VERSION = "4.8.3";
const EXPECTED_MINIMATCH_VERSION = "11.0.1";
const EXPECTED_PICOMATCH_VERSION = "2.3.2";
const OLD_IMPORT = 'import { makeRe } from "minimatch";';
const NEW_IMPORT = 'import picomatch from "picomatch";';
const OLD_CALL = "const externalRegex = makeRe(externalName);";
const NEW_CALL = "const externalRegex = picomatch.makeRe(externalName);";

/**
 * Apply a narrow, version-pinned compatibility patch to Trigger.dev CLI 4.8.3.
 *
 * minimatch 11 removed makeRe() entirely. The CLI's only use is to compile
 * package-glob patterns into a RegExp for esbuild. picomatch 2.3.2 provides
 * makeRe() for that purpose. Pin and verify both package versions and the
 * exact upstream source shape; fail closed on any drift.
 *
 * This modifies only the local DEVELOPMENT CLI file under node_modules.
 */
export function patchTriggerCli(rootDir = process.cwd()) {
  const cliPackageJsonPath = join(rootDir, "node_modules", "trigger.dev", "package.json");
  const minimatchPackageJsonPath = join(rootDir, "node_modules", "minimatch", "package.json");
  const picomatchPackageJsonPath = join(rootDir, "node_modules", "picomatch", "package.json");
  const externalsPath = join(rootDir, "node_modules", "trigger.dev", "dist", "esm", "build", "externals.js");

  let cliPackage;
  try {
    cliPackage = JSON.parse(readFileSync(cliPackageJsonPath, "utf8"));
  } catch (error) {
    throw new Error("TRIGGER_CLI_PACKAGE_NOT_INSTALLED", { cause: error });
  }
  if (cliPackage.version !== EXPECTED_CLI_VERSION) {
    throw new Error(`UNSUPPORTED_TRIGGER_CLI_VERSION:${String(cliPackage.version)}`);
  }

  let minimatchPackage;
  try {
    minimatchPackage = JSON.parse(readFileSync(minimatchPackageJsonPath, "utf8"));
  } catch (error) {
    throw new Error("MINIMATCH_PACKAGE_NOT_INSTALLED", { cause: error });
  }
  if (minimatchPackage.version !== EXPECTED_MINIMATCH_VERSION) {
    throw new Error(`UNSUPPORTED_MINIMATCH_VERSION:${String(minimatchPackage.version)}`);
  }

  let picomatchPackage;
  try {
    picomatchPackage = JSON.parse(readFileSync(picomatchPackageJsonPath, "utf8"));
  } catch (error) {
    throw new Error("PICOMATCH_PACKAGE_NOT_INSTALLED", { cause: error });
  }
  if (picomatchPackage.version !== EXPECTED_PICOMATCH_VERSION) {
    throw new Error(`UNSUPPORTED_PICOMATCH_VERSION:${String(picomatchPackage.version)}`);
  }

  let source;
  try {
    source = readFileSync(externalsPath, "utf8");
  } catch (error) {
    throw new Error("TRIGGER_CLI_EXTERNALS_MODULE_NOT_FOUND", { cause: error });
  }

  const alreadyPatched =
    source.includes(NEW_IMPORT) &&
    source.includes(NEW_CALL) &&
    !source.includes(OLD_IMPORT) &&
    !source.includes(OLD_CALL);
  if (alreadyPatched) {
    return { status: "already-patched", version: cliPackage.version };
  }

  const importCount = source.split(OLD_IMPORT).length - 1;
  const callCount = source.split(OLD_CALL).length - 1;
  if (importCount !== 1 || callCount !== 1) {
    throw new Error("TRIGGER_CLI_SOURCE_SHAPE_UNEXPECTED");
  }

  const patched = source.replace(OLD_IMPORT, NEW_IMPORT).replace(OLD_CALL, NEW_CALL);
  writeFileSync(externalsPath, patched, "utf8");
  return { status: "patched", version: cliPackage.version };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const result = patchTriggerCli();
    console.log(`TRIGGER_CLI_COMPAT_${result.status.toUpperCase()}_V${result.version}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : "TRIGGER_CLI_COMPAT_PATCH_FAILED");
    process.exitCode = 1;
  }
}

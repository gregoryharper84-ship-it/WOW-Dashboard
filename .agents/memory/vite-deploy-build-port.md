---
name: Vite deploy build vs PORT/BASE_PATH
description: Why legacy platform scaffold vite.config.ts files break the deploy build, and the serve-time guard fix.
---

# Vite scaffold configs must not throw at build time

The legacy platform vite scaffold (`artifacts/*/vite.config.ts`) reads `PORT` and
`BASE_PATH` at config-load time and `throw`s if either is missing. These vars are
provided by the **workflow** (dev serve) and are NOT present during the
deployment build, which runs `pnpm run build` → `pnpm -r run build` → `vite build`
for every artifact. Result: publish fails with
`Error: PORT environment variable is required but was not provided.`

**Why:** `PORT`/`BASE_PATH` are serve-time concerns; a static `vite build` bundle
binds no port. Throwing on them at module load couples the build to runtime env.

**Fix (applied to mockup-sandbox + flask-scoring-api):** wrap the config in the
function form and gate the throws behind `command === "serve"`, defaulting
`port` to 5173 and `base` to `"/"` for build:

```ts
import { defineConfig, type UserConfig } from "vite";
export default defineConfig(async ({ command }): Promise<UserConfig> => {
  const isServe = command === "serve";
  const rawPort = process.env.PORT;
  if (isServe && !rawPort) throw new Error("PORT ... required");
  const port = rawPort ? Number(rawPort) : 5173;
  const basePath = process.env.BASE_PATH ?? "/";
  return { base: basePath, /* ...rest... */ };
});
```

**Gotchas:**
- The async function returning a plain object fails TS overload resolution
  (TS2769). Annotate the return as `Promise<UserConfig>` so it matches the
  `UserConfigFnPromise` overload.
- `flask-scoring-api` is a Python/gunicorn app but ALSO ships a vite React
  frontend (built to `dist/public`, served by Flask). Its `vite build` runs in
  the deploy build too, so it hit the same throw. Don't assume "Python artifact"
  means "no vite build step."
- `command === "serve"` is true for dev; build/preview are not "serve", so dev
  still strictly enforces PORT/BASE_PATH as before.

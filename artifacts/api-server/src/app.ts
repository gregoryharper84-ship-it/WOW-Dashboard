import express, { type Express, type ErrorRequestHandler } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";
import path from "node:path";
import { fileURLToPath } from "node:url";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.get("/api", (_req, res) => {
  res.json({ status: "ok" });
});

app.use("/api", router);

// Serve the reviewable production build from the same origin as its API.
const rawBase = process.env.BASE_PATH ?? "/final-lock/";
if (!/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(rawBase)) {
  throw new Error("BASE_PATH must be / or an absolute path ending in /.");
}
const base = rawBase === "/" ? "" : rawBase.slice(0, -1);
const publicDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../final-lock/dist/public");
app.use(base || "/", express.static(publicDir));
app.get(`${base}/*splat`, (_req, res) => { res.sendFile(path.join(publicDir, "index.html")); });
app.get(base || "/", (_req, res) => { res.sendFile(path.join(publicDir, "index.html")); });

const apiError: ErrorRequestHandler = (error, req, res, next) => {
  if (!req.path.startsWith("/api/")) return next(error);
  const invalidInput = [400, 413].includes(Number(error?.status));
  return res.status(invalidInput ? Number(error.status) : 500).json({
    ok: false,
    code: invalidInput ? "DASHBOARD_INPUT_INVALID" : "DASHBOARD_SERVICE_FAILURE",
    message: invalidInput ? "The request body is invalid or exceeds the supported size." : "The dashboard service could not complete this request.",
    can_execute: false,
  });
};
app.use(apiError);

export default app;

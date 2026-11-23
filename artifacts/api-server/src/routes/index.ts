import { Router, type IRouter } from "express";
import healthRouter from "./health";
import scoringProxy from "./scoring-proxy";
import devSimulator from "./dev-simulator";
import { createV17Proxy, requireDashboardKey } from "./v17-proxy";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/v17", createV17Proxy());
router.use(requireDashboardKey(process.env.WOW_DASHBOARD_API_KEY));
// V17 uses its own governed backend and never requires the legacy database.
// Avoid importing @workspace/db (which requires DATABASE_URL) in that mode.
if (process.env.DATABASE_URL) {
  const [{ default: propsAdapter }, { default: postmortem }] = await Promise.all([
    import("./props-adapter"), import("./postmortem"),
  ]);
  router.use("/props", propsAdapter);
  router.use("/postmortem", postmortem);
} else {
  router.use(["/props", "/postmortem"], (_req, res) => {
    res.status(503).json({ ok: false, code: "LEGACY_DATABASE_NOT_CONFIGURED", error: "This legacy V16 service requires DATABASE_URL. V17 requests use the governed backend separately.", can_execute: false });
  });
}
router.use("/dev",        devSimulator);   // source-status-sim
router.use("/admin",      devSimulator);   // smoke-test
router.use(scoringProxy);

export default router;

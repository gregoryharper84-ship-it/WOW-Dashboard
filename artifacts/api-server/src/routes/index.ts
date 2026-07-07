import { Router, type IRouter } from "express";
import healthRouter from "./health";
import scoringProxy from "./scoring-proxy";
import propsAdapter from "./props-adapter";
import devSimulator from "./dev-simulator";
import postmortem from "./postmortem";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/props",      propsAdapter);
router.use("/dev",        devSimulator);   // source-status-sim
router.use("/admin",      devSimulator);   // smoke-test
router.use("/postmortem", postmortem);
router.use(scoringProxy);

export default router;

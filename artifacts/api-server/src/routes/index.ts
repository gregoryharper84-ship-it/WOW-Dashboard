import { Router, type IRouter } from "express";
import healthRouter from "./health";
import scoringProxy from "./scoring-proxy";

const router: IRouter = Router();

router.use(healthRouter);
router.use(scoringProxy);

export default router;

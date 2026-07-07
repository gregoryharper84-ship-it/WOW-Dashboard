import { Router, type IRouter } from "express";
import healthRouter from "./health";
import scoringProxy from "./scoring-proxy";
import propsAdapter from "./props-adapter";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/props", propsAdapter);
router.use(scoringProxy);

export default router;

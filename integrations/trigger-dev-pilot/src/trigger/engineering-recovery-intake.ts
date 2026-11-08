import { AbortTaskRunError, task, wait } from "@trigger.dev/sdk";
import {
  assertEngineeringAgentSafety,
  EngineeringIntakeError,
  fetchIncidentSnapshot,
  sameIncidentSnapshot,
  validateIncidentNumber,
} from "../lib/engineering-recovery-intake.mjs";

/**
 * First durable Engineering Recovery Agent vertical.
 *
 * This task is intentionally DEV-only and read-only. It captures an exact
 * incident snapshot, optionally crosses a Trigger.dev checkpoint/resume
 * boundary, and verifies the source did not change while paused.
 *
 * It does not call an LLM, execute shell/code, dispatch GitHub Actions, write
 * GitHub/Supabase/Render, merge, deploy, or touch sporting model routes.
 */
export const wowV17EngineeringRecoveryIntake = task({
  id: "wow-v17-engineering-recovery-intake",
  concurrency: { total: 1 },
  maxDuration: 180,
  retry: {
    maxAttempts: 3,
    minTimeoutInMs: 1000,
    maxTimeoutInMs: 8000,
    factor: 2,
    randomize: false,
  },
  run: async (
    payload: { incidentNumber: number; checkpointProbe?: boolean },
    { ctx },
  ) => {
    try {
      assertEngineeringAgentSafety({ triggerEnvironmentType: ctx.environment.type });
      validateIncidentNumber(payload?.incidentNumber);
      if (payload?.checkpointProbe !== undefined && typeof payload.checkpointProbe !== "boolean") {
        throw new EngineeringIntakeError("INVALID_CHECKPOINT_PROBE_FLAG");
      }
    } catch (error) {
      const code = error instanceof EngineeringIntakeError ? error.code : "ENGINEERING_INTAKE_PREFLIGHT_FAILED";
      throw new AbortTaskRunError(code);
    }

    let initial;
    try {
      initial = await fetchIncidentSnapshot({ incidentNumber: payload.incidentNumber });
    } catch (error) {
      if (error instanceof EngineeringIntakeError && !error.retryable) {
        throw new AbortTaskRunError(error.code);
      }
      throw error;
    }

    let recheck = null;
    if (payload.checkpointProbe === true) {
      // A wait longer than one minute exercises Trigger.dev's durable
      // checkpoint/resume path without consuming compute while suspended.
      await wait.for({ seconds: 65 });
      try {
        recheck = await fetchIncidentSnapshot({ incidentNumber: payload.incidentNumber });
      } catch (error) {
        if (error instanceof EngineeringIntakeError && !error.retryable) {
          throw new AbortTaskRunError(error.code);
        }
        throw error;
      }
    }

    const sourceUnchanged = recheck ? sameIncidentSnapshot(initial, recheck) : null;
    const checkpointProved = recheck !== null && sourceUnchanged === true;

    return {
      schema_version: "wow.v17.trigger.engineering-intake.v1",
      task_id: "wow-v17-engineering-recovery-intake",
      status: recheck === null
        ? "INTAKE_CAPTURED_READ_ONLY"
        : checkpointProved
          ? "DURABLE_CHECKPOINT_RESUME_PROVED"
          : "SOURCE_CHANGED_DURING_CHECKPOINT",
      incident: initial,
      source_recheck: recheck,
      checkpoint: {
        requested: payload.checkpointProbe === true,
        resumed_after_wait: recheck !== null,
        source_unchanged: sourceUnchanged,
        checkpoint_resume_proved: checkpointProved,
      },
      handoff: {
        dispatched: false,
        next_owner: "EXISTING_GITHUB_ACTIONS_ENGINEERING_WORKER",
        reason: "THIS_DEV_PILOT_HAS_NO_DISPATCH_OR_WRITE_AUTHORITY",
      },
      governance: {
        custom_gpt_identity: "WOW_BETTING_ENGINE",
        runtime_generation: "UNVERIFIED_BY_READ_ONLY_INTAKE",
        terminal_authority: "V17_TERMINAL_REDUCER",
        terminal_authority_verified: false,
        probability_authority: "NONE",
        can_execute: false,
        dry_run_only: true,
        production_verified: false,
        model_qualified: false,
      },
    };
  },
});

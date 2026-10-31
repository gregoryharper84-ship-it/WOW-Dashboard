"""WOW V17 two-sided learning and engineering recurrence audit (read-only).

Keep settled outcomes, pregame signals, engineering incidents, and proposed
changes in separate evidence namespaces. Nothing here updates a model, opens
an engineering job, rewrites a terminal decision, or executes a wager.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from json import dumps
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
AUTHORITY = "ADVISORY_ONLY"


def signal_polarity_diagnostics(
    memories: Iterable[Any], *, min_samples: int = 30,
) -> list[dict[str, Any]]:
    """Describe pregame signal prevalence on *both* settled outcome polarities.

    This is association, NOT a success/failure cause and NOT a calibration
    cohort partition: the outcome and signal bucket must never be used to
    select a serving calibrator. Deduplicate the same frozen observation.
    """
    if min_samples < 1:
        raise ValueError("INVALID_MIN_SAMPLES")
    contexts: dict[str, dict[str, Any]] = {}
    for row in memories:
        y = getattr(row, "outcome_target", None)
        p = getattr(row, "model_probability", None)
        if y not in (0, 1) or isinstance(y, bool):
            continue
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 < p < 1:
            continue
        name = "|".join((
            str(getattr(row, "source_prediction_kind", "") or ""),
            str(getattr(row, "sport", "") or ""),
            str(getattr(row, "market_family", "") or ""),
            str(getattr(row, "stat_type", "") or ""),
            str(getattr(row, "specialist_id", "") or getattr(row, "model_family", "") or ""),
            str(getattr(row, "signal_namespace", "") or ""),
            str(getattr(row, "signal_name", "") or ""),
        ))
        bucket = str(getattr(row, "signal_bucket", "") or "")
        observation_id = str(getattr(row, "source_prediction_id", "") or "")
        if not bucket or not observation_id:
            continue
        context = contexts.setdefault(name, {})
        unique = context.setdefault(bucket, {})
        if observation_id in unique:
            # Multiple copies of a prediction are one sporting observation.
            continue
        unique[observation_id] = (y, float(p))

    out = []
    for context, buckets in sorted(contexts.items()):
        # Prediction may occur in only one bucket per signal definition.
        seen: dict[str, str] = {}
        for bucket, values in buckets.items():
            for prediction_id in values:
                if prediction_id in seen and seen[prediction_id] != bucket:
                    raise ValueError("SIGNAL_BUCKET_IDENTITY_CONFLICT")
                seen[prediction_id] = bucket
        total_wins = sum(y for vals in buckets.values() for y, _ in vals.values())
        total_losses = sum(1-y for vals in buckets.values() for y, _ in vals.values())
        for bucket, vals in sorted(buckets.items()):
            wins = sum(y for y, _ in vals.values())
            losses = len(vals) - wins
            n = len(vals)
            mean_p = sum(p for _, p in vals.values()) / n
            out.append({
                "context_key": context,
                "signal_bucket": bucket,
                "n": n,
                "wins": wins,
                "losses": losses,
                "p_bucket_given_win": wins / total_wins if total_wins else None,
                "p_bucket_given_loss": losses / total_losses if total_losses else None,
                "observed_rate": wins / n,
                "mean_pregame_probability": mean_p,
                "brier": sum((p-y)**2 for y, p in vals.values()) / n,
                "calibration_bias": wins / n - mean_p,
                "review_ready": n >= min_samples,
                "interpretation": "PREGAME_SIGNAL_ASSOCIATION_ONLY_NOT_CAUSAL",
                "serving_cohort_eligible": False,
                "can_execute": False,
            })
    return out


def margin_class_diagnostics(legs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Descriptive postsettlement outcome strata; never feed as pregame features."""
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"rows": 0, "wins": 0, "losses": 0, "pushes_voids": 0}
    )
    for leg in legs:
        label = str(leg.get("margin_class") or "NOT_APPLICABLE")
        result = str(leg.get("settled_result") or "").upper()
        counts[label]["rows"] += 1
        counts[label]["wins" if result == "WIN" else
                      "losses" if result == "LOSS" else "pushes_voids"] += 1
    return {
        "strata": dict(sorted(counts.items())),
        "interpretation": "POSTSETTLEMENT_DESCRIPTIVE_ONLY_NOT_CALIBRATION_COHORT",
        "causal_attribution": False,
        "can_execute": False,
    }


def audit_engineering_incidents(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Quantify *explicitly classified* root-cause recurrence, not PR volume.

    Legacy records without typed root cause cannot enter recurrence denominators.
    A legacy VERIFIED_CLOSED record missing a production receipt is an evidence
    gap; this audit must not rewrite a historic closure or deploy anything.
    """
    records = ledger.get("records") or []
    if not isinstance(records, list):
        raise ValueError("INCIDENT_LEDGER_SCHEMA_INVALID")
    unique: set[str] = set()
    classified: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_domain: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "classified_incidents": 0, "repeated_incidents": 0, "unclassified": 0,
    })
    gaps = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("INCIDENT_LEDGER_RECORD_INVALID")
        pid = str(record.get("postmortem_id") or "")
        if not pid or pid in unique:
            raise ValueError("INCIDENT_LEDGER_DUPLICATE_OR_MISSING_ID")
        unique.add(pid)
        domain = str(record.get("domain") or "UNCLASSIFIED_DOMAIN").strip().upper()
        learning = record.get("learning") if isinstance(record.get("learning"), Mapping) else {}
        key = str(record.get("root_cause_key") or learning.get("failure_category") or "").strip().upper()
        if key and str(record.get("root_cause_status") or "CONFIRMED").upper() == "CONFIRMED":
            classified[(domain, key)].append(pid)
            by_domain[domain]["classified_incidents"] += 1
        else:
            by_domain[domain]["unclassified"] += 1
        fixes = record.get("engineering_fixes") or {}
        if not isinstance(fixes, Mapping):
            raise ValueError("INCIDENT_FIXES_SCHEMA_INVALID")
        for fid, fix in fixes.items():
            if not isinstance(fix, Mapping) or fix.get("state") != "VERIFIED_CLOSED":
                continue
            change_scope = str(fix.get("change_scope") or "PRODUCTION_OR_UNCLASSIFIED").upper()
            release = record.get("release") if isinstance(record.get("release"), Mapping) else {}
            issues = []
            if not fix.get("merge_commit"):
                issues.append("MERGE_COMMIT_MISSING")
            if change_scope in ("OFFLINE_RESEARCH", "DOCS_ONLY", "TEST_ONLY"):
                if str(fix.get("qa_status") or record.get("qa", {}).get("status") or "") != "PASS":
                    issues.append("NONPRODUCTION_VERIFICATION_MISSING")
            else:
                if not (fix.get("production_deploy") or release.get("deployment_id")):
                    issues.append("DEPLOYMENT_ID_MISSING")
                if release.get("production_verified") is not True:
                    issues.append("INDEPENDENT_PRODUCTION_VERIFICATION_MISSING")
                if not release.get("production_receipt_ids"):
                    issues.append("PRODUCTION_RECEIPT_MISSING")
            if issues:
                gaps.append({
                    "postmortem_id": pid,
                    "fix_id": str(fid),
                    "missing": issues,
                    "status": "EVIDENCE_GAP_REVIEW",
                })
    for (domain, key), ids in classified.items():
        by_domain[domain]["repeated_incidents"] += max(0, len(ids)-1)
    rates = {
        domain: {
            **data,
            "recurrence_rate": (
                data["repeated_incidents"] / data["classified_incidents"]
                if data["classified_incidents"] else None
            ),
            "metric_scope": "EXPLICIT_ROOT_CAUSE_CLASS_ONLY",
        }
        for domain, data in sorted(by_domain.items())
    }
    recurring = [
        {"domain": domain, "root_cause_key": key, "independent_incidents": len(ids),
         "repeat_n": len(ids)-1,
         "recommended_escalation": (
             "REDESIGN_REVIEW" if len(ids)-1 >= 4 else
             "ESCALATE_P0_REVIEW" if len(ids)-1 >= 3 else
             "DIAGNOSIS_REQUIRED" if len(ids)-1 >= 1 else "NO_RECURRING_PATTERN"
         ),
         "postmortem_ids": ids}
        for (domain, key), ids in sorted(classified.items())
    ]
    return {
        "incident_n": len(unique),
        "explicitly_classified_n": sum(len(ids) for ids in classified.values()),
        "same_root_cause_recurrence_rate_by_domain": rates,
        "root_cause_groups": recurring,
        "verified_closure_evidence_gaps": gaps,
        "closure_evidence_gap_n": len(gaps),
        "legacy_missing_provenance_is_not_new_proof_of_failure": True,
        "can_execute": False,
    }


def compile_engineering_refinements(
    items: Iterable[Any], *,
    postmortem_id: str,
) -> dict[str, Any]:
    """Compile typed postmortem work proposals but never enqueue free text.

    Ordinary postmortem \`refine_items\` are user-facing sports observations.
    They must NOT silently become Engineering tickets, model candidates, or
    serving changes. Only explicit incident evidence supports an engineering
    proposal. Preserve counts of blocked items instead of losing rows.
    """
    proposals = []
    blocked = []
    required = (
        "domain", "root_cause_key", "proposed_change",
        "evidence_refs", "guard_test_path",
    )
    for i, item in enumerate(items):
        if not isinstance(item, Mapping) or any(not item.get(k) for k in required):
            blocked.append({"source_index": i, "reason": "REFINE_ITEM_NOT_TYPED_ENGINEERING_EVIDENCE"})
            continue
        fingerprint = sha256(dumps({
            "postmortem_id": postmortem_id, "source_index": i,
            "root_cause_key": item["root_cause_key"],
            "proposed_change": item["proposed_change"],
        }, sort_keys=True).encode()).hexdigest()
        proposals.append({
            "proposal_id": fingerprint,
            "proposal_namespace": "ENGINEERING",
            "proposal_type": "ENGINEERING_REGRESSION_CHALLENGER",
            "trigger_source": "POSTMORTEM_REFINE",
            "trigger_id": postmortem_id,
            "source_index": i,
            "domain": str(item["domain"]),
            "root_cause_key": str(item["root_cause_key"]),
            "recipe": {
                "minimal_change": str(item["proposed_change"]),
                "guard_test_path": str(item["guard_test_path"]),
                "preserve": list(item.get("preserve") or []),
                "evidence_refs": list(item["evidence_refs"]),
                "required_validation": [
                    "ROOT_CAUSE_REPRODUCED",
                    "GUARD_FAILS_PRE_FIX",
                    "GUARD_PASSES_POST_FIX",
                    "NEIGHBOR_REGRESSION",
                    "CORRECT_ENVIRONMENT_VERIFIED",
                    "INDEPENDENT_ACCEPTANCE",
                ],
            },
            "status": "CANDIDATE_EVIDENCE_PENDING",
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        })
    return {
        "input_n": len(proposals) + len(blocked),
        "proposals": proposals,
        "blocked": blocked,
        "automatic_queue_admission": False,
        "can_execute": False,
    }

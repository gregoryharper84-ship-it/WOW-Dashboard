"""
validation/reporters/markdown_reporter.py

Emit a concise Markdown comparison report from a JSON report dict.
"""
from __future__ import annotations

import os
from typing import Any, Optional


def _fmt(v: Optional[float], digits: int = 4) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _verdict_icon(v: str) -> str:
    return {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INSUFFICIENT_DATA": "⬜"}.get(v, v)


def build_markdown(report: dict) -> str:
    hv  = report.get("harness_version", "?")
    sha = report.get("frozen_commit", "?")
    ts  = report.get("generated_at", "?")
    hoe = "**YES — BLIND HOLDOUT EVALUATED**" if report.get("holdout_evaluated") else "No (tuning phase only)"

    lines = [
        f"# WOW Prediction Validation Harness v{hv}",
        f"",
        f"**Frozen commit:** `{sha}`  ",
        f"**Generated:** {ts}  ",
        f"**Holdout evaluated:** {hoe}",
        f"",
    ]

    # ── Split manifest ─────────────────────────────────────────────────────
    sm = report.get("split_manifest") or {}
    lines += [
        "## Data Split",
        "",
        f"| Set | Count | Date range |",
        f"|---|---|---|",
        f"| Train | {sm.get('train_count','—')} | {sm.get('train_date_range', ('',''))[0]} → {sm.get('train_date_range', ('',''))[1]} |",
        f"| Validation | {sm.get('validation_count','—')} | {sm.get('validation_date_range', ('',''))[0]} → {sm.get('validation_date_range', ('',''))[1]} |",
        f"| True Holdout | {sm.get('holdout_count','—')} | {sm.get('holdout_date_range', ('',''))[0]} → {sm.get('holdout_date_range', ('',''))[1]} |",
        f"| Excluded | {sm.get('excluded_count','—')} | — |",
        "",
    ]

    # ── Gate verdicts ─────────────────────────────────────────────────────
    verdicts = report.get("verdicts") or {}
    lines += ["## Gate Verdicts (Validation Set)", ""]

    for k, v in verdicts.items():
        icon    = _verdict_icon(v.get("verdict", ""))
        score   = _fmt(v.get("score") or v.get("ece"))
        lines.append(f"- {icon} **{k}**: {score}")

    lines.append("")

    # ── Model vs baselines ────────────────────────────────────────────────
    mr = report.get("model_results") or {}
    br = report.get("baseline_results") or {}

    val_model_brier   = _fmt((mr.get("validation") or {}).get("brier", {}).get("score"))
    val_model_logloss = _fmt((mr.get("validation") or {}).get("log_loss", {}).get("score"))
    val_model_ece     = _fmt((mr.get("validation") or {}).get("calibration", {}).get("ece"))
    val_model_n       = (mr.get("validation") or {}).get("coverage", {}).get("n_with_prob", "—")

    lines += [
        "## Model vs Baselines (Validation Set)",
        "",
        "| Model | Brier ↓ | Log Loss ↓ | ECE ↓ | N |",
        "|---|---|---|---|---|",
        f"| **WOW_LEAN_1IP** | {val_model_brier} | {val_model_logloss} | {val_model_ece} | {val_model_n} |",
    ]

    for bid, splits in br.items():
        bscore = _fmt((splits.get("validation") or {}).get("brier", {}).get("score"))
        ll     = _fmt((splits.get("validation") or {}).get("log_loss", {}).get("score"))
        n      = (splits.get("validation") or {}).get("coverage", {}).get("n_with_prob", "—")
        lines.append(f"| {bid} | {bscore} | {ll} | — | {n} |")

    lines.append("")

    # Baseline comparison summary
    cmp = report.get("baseline_comparison") or []
    if cmp:
        lines += ["### Model beats all baselines?", ""]
        all_beat = all(c.get("beats_model") is True for c in cmp if c.get("beats_model") is not None)
        icon = "✅" if all_beat else "❌"
        lines.append(f"{icon} Model beats all baselines on Brier: **{'YES' if all_beat else 'NO'}**")
        lines.append("")
        for c in cmp:
            bv = c.get("beats_model")
            row_icon = "✅" if bv is True else "❌" if bv is False else "⬜"
            lines.append(f"  - {row_icon} vs **{c['baseline_id']}**: model Brier {val_model_brier} vs baseline {_fmt(c.get('brier_validation'))}")
        lines.append("")

    # ── Calibration buckets ────────────────────────────────────────────────
    cal = (mr.get("validation") or {}).get("calibration") or {}
    bins = cal.get("bins") or []
    if bins:
        lines += ["## Calibration Buckets (Validation Set)", ""]
        lines += ["| Bucket | Count | Observed Rate | Mean Predicted | Status |",
                  "|---|---|---|---|---|"]
        for b in bins:
            bucket = f"{b['lower']:.2f}–{b['upper']:.2f}"
            obs    = _fmt(b.get("observed_rate"), 3)
            pred   = _fmt(b.get("mean_predicted_probability"), 3)
            st     = b.get("status", "")
            icon   = "⚠️ " if st == "SPARSE" else ""
            lines.append(f"| {bucket} | {b['count']} | {obs} | {pred} | {icon}{st} |")
        lines.append("")

    # ── Line slices ────────────────────────────────────────────────────────
    slices = (mr.get("validation") or {}).get("line_slices") or {}
    if slices:
        lines += ["## Line-Specific Slices (Validation Set)", ""]
        lines += ["| Line | Brier | N | Status |", "|---|---|---|---|"]
        for ln, s in sorted(slices.items(), key=lambda x: float(x[0])):
            icon = "⚠️ " if s.get("status") == "SPARSE" else ""
            lines.append(f"| {ln} | {_fmt(s.get('brier'))} | {s.get('n','—')} | {icon}{s.get('status','')} |")
        lines.append("")

    # ── Ablation ──────────────────────────────────────────────────────────
    abl = report.get("ablation") or {}
    if abl:
        lines += ["## Feature Ablation", ""]
        lines += ["| Feature | Status | Brier Full | Brier Ablated | Delta | Interpretation |",
                  "|---|---|---|---|---|---|"]
        for fid, a in abl.items():
            st   = a.get("status", "")
            icon = "⬜ " if st == "UNAVAILABLE" else ""
            bf   = _fmt(a.get("brier_full"))
            ba   = _fmt(a.get("brier_ablated"))
            d    = _fmt(a.get("brier_delta"))
            interp = a.get("interpretation") or a.get("unavailable_reason") or "—"
            if len(interp) > 50:
                interp = interp[:47] + "..."
            lines.append(f"| {fid} | {icon}{st} | {bf} | {ba} | {d} | {interp} |")
        lines.append("")

    # ── Limitations ───────────────────────────────────────────────────────
    limitations = report.get("limitations") or []
    if limitations:
        lines += ["## Known Limitations", ""]
        for lim in limitations:
            lines.append(f"- {lim}")
        lines.append("")

    return "\n".join(lines)


def write_markdown_report(report: dict, path: str) -> str:
    """Write Markdown report to path and return path."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    md = build_markdown(report)
    with open(path, "w") as f:
        f.write(md)
    return path

"""Shared math/guards for the research-only chronological challengers (self-contained).

SEMANTICS WARNING: every `*_wilson90_lower` value here is a statistical lower confidence bound on
the OBSERVED HIT RATE of a group of selections. It is NOT the V17 per-selection
`calibrated_lower_bound`, cannot satisfy any calibration or rank-eligibility gate, and must never
be copied into a field with that name. Research only; can_execute=false.
"""
from __future__ import annotations

import hashlib, math, subprocess
from pathlib import Path

import numpy as np

CAN_EXECUTE = False
COHORT_BOUND_SEMANTICS = "OBSERVED_COHORT_HIT_RATE_LOWER_BOUND__NOT_A_CALIBRATED_LOWER_BOUND"
CALIBRATED_LOWER_BOUND = None  # no fitted per-selection calibrated lower bound exists in this research code
RANK_ELIGIBLE = False


class EvidenceError(ValueError):
    """Typed, fail-closed input-evidence failure."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fit_logit(X, y, l2=1.0, iters=50):
    Xb = np.c_[np.ones(len(X)), X]; b = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ b)); W = p * (1 - p)
        H = Xb.T @ (Xb * W[:, None]) + l2 * np.eye(len(b)); H[0, 0] -= l2
        g = Xb.T @ (p - y) + l2 * np.r_[0, b[1:]]
        step = np.linalg.solve(H, g); b -= step
        if np.abs(step).max() < 1e-9: break
    return b


def predict(b, X): return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), X] @ b)))


def logit(p): p = np.clip(p, 1e-6, 1 - 1e-6); return np.log(p / (1 - p))


def wilson_lower(k, n, z=1.645):
    if n <= 0: return float("nan")
    p = k / n; d = 1 + z * z / n
    c = p + z * z / (2 * n); a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


def metrics(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return dict(n=int(len(y)), brier=float(np.mean((p - y) ** 2)),
                log_loss=float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
                calibration_bias=float(np.mean(p) - np.mean(y)), accuracy=float(np.mean((p > .5) == (y > .5))))


def reliability(p, y, edges=(0, .4, .5, .6, .65, .7, .8, 1.0001)):
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            k = int(y[m].sum()); out.append(dict(bin=f"[{lo},{min(hi,1)})", n=int(m.sum()), forecast=float(p[m].mean()),
                                                observed=k / int(m.sum()), cohort_hit_rate_wilson90_lower=wilson_lower(k, int(m.sum()))))
    return out


def code_provenance(repo_dir: Path | None = None) -> dict:
    """Exact code SHA or fail closed; a dirty tracked tree is not reproducible evidence."""
    cwd = str(repo_dir or Path(__file__).resolve().parent)
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=cwd).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], text=True,
                                             stderr=subprocess.DEVNULL, cwd=cwd).strip())
    except Exception as exc:
        raise EvidenceError("CODE_SHA_UNAVAILABLE") from exc
    if len(sha) != 40: raise EvidenceError("CODE_SHA_UNAVAILABLE")
    if dirty: raise EvidenceError("CODE_TREE_DIRTY_NOT_REPRODUCIBLE")
    return {"code_sha": sha}


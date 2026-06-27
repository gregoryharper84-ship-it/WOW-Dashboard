from enum import Enum


class PropLabel(str, Enum):
    RESEARCH_INTEREST        = "RESEARCH_INTEREST"
    MODEL_QUALIFIED_HOLD     = "MODEL_QUALIFIED_HOLD"
    MARKET_VERIFIED_HOLD     = "MARKET_VERIFIED_HOLD"
    MONEY_QUALIFIED          = "MONEY_QUALIFIED"
    FINAL_APPROVED           = "FINAL_APPROVED"
    REJECT_NO_EDGE           = "REJECT_NO_EDGE"
    REJECT_BAD_STRUCTURE     = "REJECT_BAD_STRUCTURE"
    REJECT_DATA_QUALITY      = "REJECT_DATA_QUALITY"
    SOURCE_CONFLICT          = "SOURCE_CONFLICT"
    SLATE_PURGE              = "SLATE_PURGE"
    DUPLICATE_EXPOSURE_BLOCK = "DUPLICATE_EXPOSURE_BLOCK"
    NO_PLAY                  = "NO_PLAY"
    # Module B — Data Contract
    DATA_CONTRACT_FAIL                  = "DATA_CONTRACT_FAIL"
    # Module C — Payout Context
    MARKET_QUALIFIED_BUT_SLIP_NEGATIVE  = "MARKET_QUALIFIED_BUT_SLIP_NEGATIVE"
    FORMAT_PENDING                      = "FORMAT_PENDING"
    # Module G — Directional Exposure
    DIRECTIONAL_EXPOSURE_BLOCK          = "DIRECTIONAL_EXPOSURE_BLOCK"
    SESSION_EXPOSURE_WARNING            = "SESSION_EXPOSURE_WARNING"
    SESSION_DIRECTIONAL_EXPOSURE_BLOCK  = "SESSION_DIRECTIONAL_EXPOSURE_BLOCK"


class DataStatus(str, Enum):
    RETRIEVED        = "RETRIEVED"
    RECONSTRUCTED    = "RECONSTRUCTED"
    PROXY_ONLY       = "PROXY_ONLY"
    FAILED           = "FAILED"
    SOURCE_CONFLICT  = "SOURCE_CONFLICT"
    INPUT_FAILURE    = "INPUT_FAILURE"
    DATA_UNOBTAINABLE = "DATA_UNOBTAINABLE"
    NOT_CALLED       = "NOT_CALLED"


APPROVAL_LABELS = {PropLabel.FINAL_APPROVED, PropLabel.MONEY_QUALIFIED}
REJECT_LABELS   = {
    PropLabel.REJECT_NO_EDGE,
    PropLabel.REJECT_BAD_STRUCTURE,
    PropLabel.REJECT_DATA_QUALITY,
    PropLabel.SOURCE_CONFLICT,
    PropLabel.SLATE_PURGE,
    PropLabel.DUPLICATE_EXPOSURE_BLOCK,
    PropLabel.NO_PLAY,
}

GATE_ORDER = [
    "board_intake",
    "slate_validation",
    "status_role",
    "l5_l10_ledger",
    "outlier_gate",
    "market_gate",
    "ev_gate",
    "slip_structure",
    "exposure_gate",
    "classifier",
]

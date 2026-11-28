from types import SimpleNamespace

import pytest

from basketball_certification_write import (
    CAN_EXECUTE,
    CERTIFICATION_TABLE,
    WRITE_NOT_OBSERVED,
    BasketballCertificationWriteError,
    read_certification_row,
    verify_certification_write,
)

MODEL_VERSION = "NBA_TEAM_EVENT_LOGISTIC_V1_20261127T215620Z"
CALIBRATOR_VERSION = "NBA_TEAM_EVENT_PLATT_V1_20261127T215620Z"


class _Query:
    def __init__(self, response):
        self.response = response
        self.selections = []
        self.filters = []
        self.limits = []

    def select(self, columns):
        self.selections.append(columns)
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, count):
        self.limits.append(count)
        return self

    def execute(self):
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(data=self.response)


class _Client:
    def __init__(self, response):
        self.query = _Query(response)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.query


def _row(**overrides):
    row = {
        "model_artifact_version": MODEL_VERSION,
        "calibrator_version": CALIBRATOR_VERSION,
        "capability_status": "SHADOW",
        "reason_code": "CERTIFICATION_REPLAY_REQUIRED",
    }
    row.update(overrides)
    return row


def test_matching_row_is_returned_from_the_single_sport_row():
    client = _Client([_row()])
    observed = verify_certification_write(
        client,
        sport="NBA",
        model_artifact_version=MODEL_VERSION,
        calibrator_version=CALIBRATOR_VERSION,
    )

    assert observed["model_artifact_version"] == MODEL_VERSION
    assert client.tables == [CERTIFICATION_TABLE]
    assert client.query.selections == [
        "model_artifact_version,calibrator_version,capability_status,reason_code"
    ]
    assert client.query.filters == [("sport", "NBA")]
    assert client.query.limits == [1]


def test_stale_row_fails_closed_and_names_both_versions():
    client = _Client([_row(model_artifact_version="NBA_TEAM_EVENT_LOGISTIC_V1_20261124T133404Z")])

    with pytest.raises(BasketballCertificationWriteError) as excinfo:
        verify_certification_write(
            client, sport="NBA", model_artifact_version=MODEL_VERSION
        )

    message = str(excinfo.value)
    assert excinfo.value.code == WRITE_NOT_OBSERVED
    assert f"expected={MODEL_VERSION}" in message
    assert "observed=NBA_TEAM_EVENT_LOGISTIC_V1_20261124T133404Z" in message
    assert "capability_status=SHADOW" in message
    assert "reason_code=CERTIFICATION_REPLAY_REQUIRED" in message


def test_absent_row_fails_closed():
    client = _Client([])

    with pytest.raises(BasketballCertificationWriteError) as excinfo:
        verify_certification_write(
            client, sport="NBA", model_artifact_version=MODEL_VERSION
        )

    assert excinfo.value.code == WRITE_NOT_OBSERVED
    assert "observed=NONE" in str(excinfo.value)


def test_calibrator_mismatch_fails_closed():
    client = _Client([_row(calibrator_version="NBA_TEAM_EVENT_PLATT_V1_OLD")])

    with pytest.raises(BasketballCertificationWriteError) as excinfo:
        verify_certification_write(
            client,
            sport="NBA",
            model_artifact_version=MODEL_VERSION,
            calibrator_version=CALIBRATOR_VERSION,
        )

    assert f"calibrator_expected={CALIBRATOR_VERSION}" in str(excinfo.value)


def test_single_object_response_is_accepted():
    client = _Client(_row())

    assert read_certification_row(client, sport="NBA")["model_artifact_version"] == MODEL_VERSION


def test_read_transport_failure_propagates_unchanged():
    error = RuntimeError("PGRST: connection reset")
    client = _Client(error)

    with pytest.raises(RuntimeError, match="connection reset"):
        read_certification_row(client, sport="NBA")


def test_frozen_forward_candidate_must_stay_unchanged():
    registered = "NBA_TEAM_EVENT_LOGISTIC_V1_20261124T133404Z"
    client = _Client([_row(model_artifact_version=registered)])

    observed = verify_certification_write(
        client,
        sport="NBA",
        model_artifact_version=registered,
        unchanged=True,
    )
    assert observed["model_artifact_version"] == registered


def test_frozen_candidate_mismatch_names_the_unchanged_expectation():
    registered = "NBA_TEAM_EVENT_LOGISTIC_V1_20261124T133404Z"
    client = _Client([_row(model_artifact_version="NBA_TEAM_EVENT_LOGISTIC_V1_20261128T134626Z")])

    with pytest.raises(BasketballCertificationWriteError) as excinfo:
        verify_certification_write(
            client,
            sport="NBA",
            model_artifact_version=registered,
            unchanged=True,
        )

    message = str(excinfo.value)
    assert f"unchanged={registered}" in message
    assert "observed=NBA_TEAM_EVENT_LOGISTIC_V1_20261128T134626Z" in message


def test_verifier_never_grants_execution():
    assert CAN_EXECUTE is False

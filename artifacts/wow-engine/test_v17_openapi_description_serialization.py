from pathlib import Path

import yaml
from openapi_spec_validator import validate_spec


def test_canonical_v17_openapi_validates_and_daily_description_is_single_field():
    path = Path(__file__).parent / "v17" / "openapi.wow-betting-engine.v17.yaml"
    spec = yaml.safe_load(path.read_text())

    validate_spec(spec)

    response = spec["paths"]["/v17/daily-snapshot-run"]["post"]["responses"]["200"]
    assert response == {
        "description": "Terminal bounded Daily receipt, including held rows"
    }

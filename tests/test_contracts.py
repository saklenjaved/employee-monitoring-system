"""Contract tests (TAD 10, Step 3): the API contract and event schema must be valid, and
the agent's real output must match them."""

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest
import yaml

from winagent.events import Event, to_wire

DOCS = Path(__file__).resolve().parent.parent / "docs"
SIX_PATHS = {
    "/devices/register/": "post",
    "/config/": "get",
    "/events/batch/": "post",
    "/screenshots/authorize/": "post",
    "/screenshots/{screenshot_id}/complete/": "post",
    "/heartbeat/": "post",
}
# Pairing (docs/employee-pairing-plan.md): a PC with no credential yet asks to connect. These two calls
# carry no device credential by design. They are protected by a secret poll token and rate limits.
PAIRING_PATHS = {"/pairing/requests/", "/pairing/requests/{request_id}/"}
PRE_CREDENTIAL_PATHS = PAIRING_PATHS | {"/devices/register/"}
T =datetime(2026, 11, 1, 9, 0, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def contract():
    return yaml.safe_load((DOCS / "api-contract.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema():
    return json.loads((DOCS / "event-schema.json").read_text(encoding="utf-8"))


def test_contract_has_exactly_the_six_tad_capabilities(contract):
    found = {(p, op) for p, ops in contract["paths"].items() for op in ops}
    assert {(p, op) for p, op in SIX_PATHS.items()} <= found
    assert set(contract["paths"]) - PAIRING_PATHS - {"/devices/credential/renew/"} == set(SIX_PATHS)


def test_every_operation_has_responses_and_ids(contract):
    ids = []
    for ops in contract["paths"].values():
        for op in ops.values():
            assert op.get("responses"), "each operation needs responses"
            ids.append(op["operationId"])
    assert len(ids) == len(set(ids))


def test_only_register_and_security_schemes_skip_auth(contract):
    for path, ops in contract["paths"].items():
        for op in ops.values():
            if path not in PRE_CREDENTIAL_PATHS:
                assert "security" not in op or op["security"], f"{path} must require the device credential"


def test_no_request_schema_accepts_org_or_employee_ids(contract):
    banned = {"organization_id", "employee_id"}
    for name, sch in contract["components"]["schemas"].items():
        props = set(sch.get("properties", {}))
        assert not (props & banned), f"{name} must not accept {props & banned}"


def test_contract_urls_are_https_placeholder(contract):
    for server in contract["servers"]:
        assert server["url"].startswith("https://")


def test_event_schema_is_valid_draft_2020_12(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


def sample_events():
    return [
        Event("app_focus", T, {"process_name": "chrome.exe", "duration_seconds": 30.0}),
        Event("app_focus", T, {"process_name": "code.exe", "window_title": "notes", "duration_seconds": 30.0}),
        Event("activity_sample", T, {"state": "idle", "idle_seconds": 310.2, "sample_interval_seconds": 30.0}),
        Event("session_state", T, {"session_type": "console", "state": "monitoring"}),
    ]


@pytest.mark.parametrize("event", sample_events(), ids=lambda e: e.event_type)
def test_real_wire_output_validates_against_schema(schema, event):
    wire = to_wire(event)
    jsonschema.validate(wire, schema)


def test_timestamps_are_utc_iso8601_with_z():
    wire = to_wire(Event("activity_sample", T, {"state": "active", "idle_seconds": 0.0, "sample_interval_seconds": 30.0}))
    assert wire["captured_at"] == "2026-11-01T09:00:30.000000Z"


def test_extra_field_rejected_by_schema(schema):
    wire = to_wire(Event("activity_sample", T, {"state": "active", "idle_seconds": 0.0, "sample_interval_seconds": 30.0}))
    wire["payload"]["typed_text"] = "hello"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wire, schema)


def test_organization_id_in_event_rejected(schema):
    wire = to_wire(Event("app_focus", T, {"process_name": "a.exe", "duration_seconds": 1.0}))
    wire["organization_id"] = "org-1"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wire, schema)


def test_non_uuid4_event_id_rejected():
    with pytest.raises(ValueError):
        Event("app_focus", T, {"process_name": "a.exe", "duration_seconds": 1.0}, event_id="00000000-0000-1000-8000-000000000000")


def test_window_title_is_not_in_default_app_events(schema):
    # Titles are opt-in; the default event shape has no window_title key.
    wire = to_wire(Event("app_focus", T, {"process_name": "a.exe", "duration_seconds": 1.0}))
    assert "window_title" not in wire["payload"]


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        Event("app_focus", datetime(2026, 1, 1), {"process_name": "a.exe", "duration_seconds": 1.0})

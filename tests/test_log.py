import json
import logging

import pytest
from pydantic import SecretStr

from catalyst_mcp import log

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJl"


def test_redact_hides_secret_fields_tokens_and_secretstr() -> None:
    value = {
        "actor": "alice",
        "access_token": "abc",
        "headers": {"Authorization": "Bearer abc", "Accept": "application/json"},
        "note": f"got Bearer xyz.123 and {JWT}",
        "client_secret": SecretStr("s"),
        "nested": [{"password": "p"}, SecretStr("q"), "plain"],
    }
    assert log.redact(value) == {
        "actor": "alice",
        "access_token": log.REDACTED,
        "headers": {"Authorization": log.REDACTED, "Accept": "application/json"},
        "note": f"got Bearer {log.REDACTED} and {log.REDACTED}",
        "client_secret": log.REDACTED,
        "nested": [{"password": log.REDACTED}, log.REDACTED, "plain"],
    }


def test_json_records_carry_the_event_and_its_fields_and_no_secret(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure("INFO", "json")
    log.event(log.get("auth"), "authz.decision", actor="alice", criterion="c1", action="update", token=JWT)
    line = capsys.readouterr().err.strip()
    doc = json.loads(line)
    assert doc["event"] == "authz.decision"
    assert doc["logger"] == "catalyst_mcp.auth"
    assert doc["level"] == "info"
    assert (doc["actor"], doc["criterion"], doc["action"]) == ("alice", "c1", "update")
    assert doc["token"] == log.REDACTED
    assert JWT not in line


def test_console_records_and_level(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure("WARNING", "console")
    logger = log.get("watch")
    log.event(logger, "dropped", level=logging.INFO, criterion="c1")
    log.event(logger, "watch.failed", level=logging.WARNING, criterion="c1", reason=f"Bearer {JWT}")
    err = capsys.readouterr().err
    assert "dropped" not in err
    assert err.startswith('warning watch.failed criterion="c1"')
    assert JWT not in err


def test_records_go_to_stderr_never_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure()
    log.event(log.get("server"), "server.start")
    out = capsys.readouterr()
    assert out.out == ""
    assert "server.start" in out.err

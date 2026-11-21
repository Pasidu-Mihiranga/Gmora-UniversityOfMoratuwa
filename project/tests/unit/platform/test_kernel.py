import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr, ValidationError

from suwapath.platform.clock import FixedClock, OffsetClock, colombo_day_start
from suwapath.platform.config import Settings
from suwapath.platform.ids import new_id, new_trace_id
from suwapath.platform.logging import JsonFormatter


def test_new_id_is_uuid7_and_time_ordered() -> None:
    first, second = new_id(), new_id()
    assert first.version == 7
    assert first.variant == "specified in RFC 4122"
    assert (first.int >> 80) <= (second.int >> 80)


def test_trace_id_is_32_hex() -> None:
    trace = new_trace_id()
    assert len(trace) == 32
    int(trace, 16)


def test_offset_clock_only_moves_forward() -> None:
    clock = OffsetClock()
    before = clock.now()
    after = clock.advance(timedelta(hours=4))
    assert after - before >= timedelta(hours=4)
    with pytest.raises(ValueError):
        clock.advance(timedelta(minutes=-1))


def test_colombo_midnight() -> None:
    at = datetime(2026, 11, 27, 20, 0, tzinfo=UTC)  # 01:30 on 28 Nov in Colombo
    assert colombo_day_start(at) == datetime(2026, 11, 27, 18, 30, tzinfo=UTC)


def test_fixed_clock_requires_timezone() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 27))  # noqa: DTZ001


def test_logs_drop_args_and_unknown_extras() -> None:
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "patient said %s", ("I feel dizzy",), None
    )
    record.case_id = "abc"
    record.patient_text = "I feel dizzy"
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "patient said %s"
    assert line["case_id"] == "abc"
    assert "dizzy" not in json.dumps(line)


def test_provider_resolution() -> None:
    assert Settings(_env_file=None).resolved_model_provider == "fake"
    assert Settings(_env_file=None).resolved_decision_provider == "none"
    with_keys = Settings(
        _env_file=None, google_application_credentials="/x.json", openrouter_api_key=SecretStr("k")
    )
    assert with_keys.resolved_model_provider == "gemini"
    assert with_keys.resolved_decision_provider == "decisions"


def test_production_requires_secrets_and_no_demo_password() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, suwapath_env="production")
    with pytest.raises(ValidationError, match="DEMO_PASSWORD"):
        Settings(
            _env_file=None,
            suwapath_env="production",
            jwt_secret=SecretStr("s"),
            suwapath_master_key=SecretStr("k"),
            demo_password=SecretStr("d"),
        )

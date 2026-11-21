"""Structural guards on the module ports (SEC-11)."""

import importlib
import inspect
import pkgutil
from typing import Protocol

import pytest

import suwapath.contracts.ports as ports_pkg

# Methods that legitimately take a patient id without a caller principal: worker entry points
# and system tasks that run as the module itself, never on behalf of a person.
SYSTEM_ONLY = {
    "RecordService.patient_for_user",
    "BookingService.release_holds_for_case",
}


def _service_protocols() -> list[type]:
    found = []
    for info in pkgutil.iter_modules(ports_pkg.__path__):
        module = importlib.import_module(f"{ports_pkg.__name__}.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ == module.__name__ and Protocol in obj.__mro__[1:]:
                found.append(obj)
    return found


PROTOCOLS = _service_protocols()


def test_every_module_has_a_port() -> None:
    names = {p.__name__ for p in PROTOCOLS}
    expected = {
        "IdentityService",
        "AuditLog",
        "AccessPolicy",
        "ConsentService",
        "RecordService",
        "SafetyService",
        "NavigationService",
        "BookingService",
        "CareService",
        "ImagingService",
        "ImagingModel",
        "ReportService",
        "KnowledgeService",
        "IntentClassifier",
        "PrivateConsultService",
        "NotificationService",
        "NotificationChannel",
        "CaseService",
        "Verifier",
        "VerifierCheck",
        "AgentRuntime",
        "OpsService",
    }
    assert expected <= names, expected - names


@pytest.mark.parametrize("protocol", PROTOCOLS, ids=lambda p: p.__name__)
def test_methods_with_patient_data_take_a_principal(protocol: type) -> None:
    for name, member in inspect.getmembers(protocol, inspect.isfunction):
        if name.startswith("_") or f"{protocol.__name__}.{name}" in SYSTEM_ONLY:
            continue
        params = inspect.signature(member).parameters
        if "patient_id" in params:
            assert "principal" in params, (
                f"{protocol.__name__}.{name} takes patient_id without principal"
            )

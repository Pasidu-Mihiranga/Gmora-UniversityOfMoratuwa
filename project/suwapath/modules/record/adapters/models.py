"""record tables: patients, keys, clinical resources, provenance, documents (CON-7 to SEC-20)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Float, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suwapath.platform.columns import TZ, Encrypted, Timestamps, UuidKey
from suwapath.platform.db import Base


class PatientKeyRow(Base):
    __tablename__ = "record_patient_keys"
    patient_id: Mapped[UUID] = mapped_column(primary_key=True)
    wrapped_dek: Mapped[bytes | None] = mapped_column(Encrypted)
    kek_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    destroyed_at: Mapped[datetime | None] = mapped_column(TZ)


class PatientRow(UuidKey, Timestamps, Base):
    __tablename__ = "record_patients"
    user_id: Mapped[UUID | None] = mapped_column(unique=True)
    age_band: Mapped[str] = mapped_column(String(8))
    gender: Mapped[str] = mapped_column(String(10))
    language: Mapped[str] = mapped_column(String(2))
    district: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    data_enc: Mapped[bytes] = mapped_column(Encrypted)  # name, birth_date, telecom, address, nic
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class PractitionerRow(UuidKey, Timestamps, Base):
    __tablename__ = "record_practitioners"
    user_id: Mapped[UUID | None] = mapped_column(unique=True)
    search_name: Mapped[str] = mapped_column(String(120))  # directory data, not PHI
    qualifications: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(2)), server_default="{}")
    __table_args__ = (
        Index(
            "ix_record_practitioners_search_name_trgm",
            "search_name",
            postgresql_using="gin",
            postgresql_ops={"search_name": "gin_trgm_ops"},
        ),
    )


class _ClinicalRow(UuidKey, Timestamps):
    patient_id: Mapped[UUID]
    data_enc: Mapped[bytes] = mapped_column(Encrypted)
    provenance_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class ConditionRow(_ClinicalRow, Base):
    __tablename__ = "record_conditions"
    clinical_status: Mapped[str] = mapped_column(String(16))
    __table_args__ = (Index("ix_record_conditions_patient", "patient_id", "created_at"),)


class AllergyRow(_ClinicalRow, Base):
    __tablename__ = "record_allergies"
    criticality: Mapped[str] = mapped_column(String(20))
    __table_args__ = (Index("ix_record_allergies_patient", "patient_id", "created_at"),)


class MedicationStatementRow(_ClinicalRow, Base):
    __tablename__ = "record_medication_statements"
    status: Mapped[str] = mapped_column(String(16))
    timing: Mapped[dict[str, Any]] = mapped_column(JSONB)  # slots and times, needed for scheduling
    __table_args__ = (Index("ix_record_medication_statements_patient", "patient_id", "status"),)


class ObservationRow(_ClinicalRow, Base):
    __tablename__ = "record_observations"
    effective_at: Mapped[datetime] = mapped_column(TZ)
    interpretation: Mapped[str | None] = mapped_column(String(10))
    derived_from: Mapped[UUID | None]
    __table_args__ = (Index("ix_record_observations_patient", "patient_id", "effective_at"),)


class ProvenanceRow(UuidKey, Base):
    __tablename__ = "record_provenance"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    activity: Mapped[str] = mapped_column(String(20))
    agent: Mapped[str] = mapped_column(String(80))
    recorded_at: Mapped[datetime] = mapped_column(TZ)
    entity_enc: Mapped[bytes] = mapped_column(Encrypted)


class DocumentRow(UuidKey, Base):
    __tablename__ = "record_documents"
    patient_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))  # report, imaging
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))

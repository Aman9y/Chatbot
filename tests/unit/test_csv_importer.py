from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.importer import LeadCsvImporter
from app.models.consent import ConsentRecord
from app.models.enums import ConsentStatus, LifecycleState, MinorStatus, RoleHint
from app.models.household import Household
from app.models.lead import Lead
from tests.helpers import FIXTURES

CSV = (FIXTURES / "leads_sample.csv").read_text()


@pytest.fixture
def importer(session):
    return LeadCsvImporter(session, get_settings())


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_import_summary_counts(importer, session):
    summary = await importer.import_text(CSV, source="legacy_db")
    assert summary.processed == 6
    assert summary.skipped_invalid_phone == 1
    assert summary.created == 7  # 5 valid students + 2 parents
    assert summary.minors_flagged == 2  # Aisha (age 17), Vikram (dob 2011)
    assert summary.opt_outs == 1  # Rahul consent=no
    assert summary.parents_created == 2
    assert summary.households_created == 3  # FAM001, FAM003, FAM005


async def test_invalid_phone_row_skipped(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    invalid = await session.scalar(select(Lead).where(Lead.full_name == "Invalid Row"))
    assert invalid is None


async def test_consent_recorded_unverified(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345670"))
    assert lead.consent_status == ConsentStatus.OPTED_IN
    assert lead.consent_verified is False  # critique A1 — audit pending
    rec = await session.scalar(select(ConsentRecord).where(ConsentRecord.lead_id == lead.id))
    assert rec.method.value == "imported_csv_assertion"
    assert rec.consent_text is None


async def test_opted_out_lead_transitioned(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    rahul = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345671"))
    assert rahul.consent_status == ConsentStatus.OPTED_OUT
    assert rahul.lifecycle_state == LifecycleState.OPTED_OUT
    assert rahul.opted_out_at is not None


async def test_minor_flag_and_policy(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    aisha = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345672"))
    assert aisha.is_minor == MinorStatus.YES
    assert aisha.age_years == 17
    assert aisha.minor_policy_status.value == "pending_review"  # from MINOR_DEFAULT_POLICY


async def test_household_links_student_and_parent(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    student = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345670"))
    parent = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345699"))
    assert student.household_id is not None
    assert parent.household_id == student.household_id
    assert parent.role_hint == RoleHint.PARENT
    household = await session.get(Household, student.household_id)
    assert household.external_ref == "FAM001"


async def test_reimport_is_idempotent(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    leads_after_first = await _count(session, Lead)
    consent_after_first = await _count(session, ConsentRecord)

    summary2 = await importer.import_text(CSV, source="legacy_db")
    assert await _count(session, Lead) == leads_after_first
    assert await _count(session, ConsentRecord) == consent_after_first
    assert summary2.created == 0
    assert summary2.updated >= 5


async def test_dry_run_rolls_back(importer, session):
    summary = await importer.import_text(CSV, source="legacy_db", dry_run=True)
    assert summary.created >= 5
    assert await _count(session, Lead) == 0


async def test_dob_populates_birthdate(importer, session):
    await importer.import_text(CSV, source="legacy_db")
    vikram = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345674"))
    assert vikram.date_of_birth == date(2011, 6, 20)
    assert vikram.is_minor == MinorStatus.YES

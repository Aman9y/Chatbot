"""Lead CSV importer for the ~1,200 legacy NEET leads.

Design points:
  * Phone is normalized to E.164; invalid rows are collected, not fatal.
  * Dedup is database-level (unique phone_e164) + merge-on-reimport: existing
    fields are only *filled*, never overwritten, so re-running is safe and
    idempotent (critique D6).
  * Every imported lead gets a ConsentRecord with method
    ``imported_csv_assertion`` and ``verified=False`` — the wording was not
    captured, so outreach stays blocked until the Phase 1 audit (critique A1).
  * DOB / age -> is_minor -> minor_policy_status (critique A2/D1).
  * ``parent_phone`` / ``family_id`` columns create + link Households (critique D3).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging_config import get_logger
from app.models.consent import ConsentRecord
from app.models.enums import (
    ConsentMethod,
    ConsentStatus,
    LifecycleEvent,
    MinorStatus,
    NeetCategory,
    RoleHint,
)
from app.models.household import Household
from app.models.lead import Lead
from app.services import consent as consent_service
from app.services import leads as leads_service
from app.services import state_machine
from app.services.eligibility import compute_eligibility
from app.services.phone import PhoneNormalizationError, normalize_phone

logger = get_logger(__name__)

# header alias -> canonical field
_COLUMN_ALIASES: dict[str, str] = {
    "name": "name",
    "full name": "name",
    "student name": "name",
    "lead name": "name",
    "phone": "phone",
    "mobile": "phone",
    "mobile number": "phone",
    "phone number": "phone",
    "contact": "phone",
    "whatsapp": "phone",
    "city": "city",
    "town": "city",
    "language": "language",
    "language preference": "language",
    "preferred language": "language",
    "source": "source",
    "lead source": "source",
    "role": "role",
    "type": "role",
    "speaker": "role",
    "dob": "dob",
    "date of birth": "dob",
    "birthdate": "dob",
    "age": "age",
    "neet score": "neet_score",
    "neet": "neet_score",
    "score": "neet_score",
    "neet category": "neet_category",
    "category": "neet_category",
    "caste category": "neet_category",
    "neet year": "neet_year",
    "year": "neet_year",
    "target country": "target_country",
    "country": "target_country",
    "preferred country": "target_country",
    "budget": "budget_band",
    "budget band": "budget_band",
    "intake": "intake_year",
    "intake year": "intake_year",
    "consent": "consent",
    "opt in": "consent",
    "opt-in": "consent",
    "consent status": "consent",
    "consent source": "consent_source",
    "consent captured at": "consent_captured_at",
    "consent date": "consent_captured_at",
    "parent phone": "parent_phone",
    "guardian phone": "parent_phone",
    "parent mobile": "parent_phone",
    "parent name": "parent_name",
    "guardian name": "parent_name",
    "family id": "family_id",
    "household id": "family_id",
    "household": "family_id",
}

_TRUE = {"1", "true", "yes", "y", "opted_in", "opt_in", "opted-in", "consented", "given"}
_FALSE = {"0", "false", "no", "n", "opted_out", "opt_out", "opted-out", "declined", "stop"}

_CATEGORY_MAP = {
    "general": NeetCategory.GENERAL,
    "gen": NeetCategory.GENERAL,
    "ur": NeetCategory.GENERAL,
    "obc": NeetCategory.OBC,
    "obc-ncl": NeetCategory.OBC,
    "sc": NeetCategory.SC,
    "st": NeetCategory.ST,
    "ews": NeetCategory.EWS,
}

_ROLE_MAP = {
    "student": RoleHint.STUDENT,
    "parent": RoleHint.PARENT,
    "guardian": RoleHint.PARENT,
    "father": RoleHint.PARENT,
    "mother": RoleHint.PARENT,
}


@dataclass
class ImportSummary:
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped_invalid_phone: int = 0
    households_created: int = 0
    households_linked: int = 0
    parents_created: int = 0
    minors_flagged: int = 0
    opt_outs: int = 0
    dry_run: bool = False
    errors: list[tuple[int, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["errors"] = [{"row": r, "error": e} for r, e in self.errors]
        return d


def _parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(value: str) -> int | None:
    value = value.strip().replace(",", "")
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    value = value.strip().replace("%", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_consent(value: str) -> ConsentStatus:
    v = value.strip().lower()
    if v in _TRUE:
        return ConsentStatus.OPTED_IN
    if v in _FALSE:
        return ConsentStatus.OPTED_OUT
    return ConsentStatus.UNKNOWN


class LeadCsvImporter:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def import_path(
        self, path: str | Path, *, source: str | None = None, dry_run: bool = False
    ) -> ImportSummary:
        text = Path(path).read_text(encoding="utf-8-sig")
        return await self.import_text(text, source=source, dry_run=dry_run)

    async def import_text(
        self, text: str, *, source: str | None = None, dry_run: bool = False
    ) -> ImportSummary:
        summary = ImportSummary(dry_run=dry_run)
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            return summary

        colmap = self._resolve_columns(reader.fieldnames)

        for lineno, raw_row in enumerate(reader, start=2):
            row = {colmap[k]: (v or "").strip() for k, v in raw_row.items() if k in colmap}
            summary.processed += 1
            try:
                await self._import_row(lineno, row, source, summary)
            except PhoneNormalizationError as exc:
                summary.skipped_invalid_phone += 1
                summary.errors.append((lineno, f"invalid phone: {exc}"))
            except Exception as exc:  # noqa: BLE001
                summary.errors.append((lineno, f"unexpected: {exc}"))
                logger.exception("import row %d failed", lineno)

        if dry_run:
            await self._session.rollback()
        else:
            await self._session.commit()
        return summary

    # -- internals -----------------------------------------------------
    def _resolve_columns(self, fieldnames: list[str]) -> dict[str, str]:
        colmap: dict[str, str] = {}
        for name in fieldnames:
            key = name.strip().lower()
            if key in _COLUMN_ALIASES:
                colmap[name] = _COLUMN_ALIASES[key]
        if "phone" not in colmap.values():
            accepted = sorted({k for k, v in _COLUMN_ALIASES.items() if v == "phone"})
            raise ValueError(f"CSV needs a phone column (one of {accepted})")
        return colmap

    async def _import_row(
        self,
        lineno: int,
        row: dict[str, str],
        source: str | None,
        summary: ImportSummary,
    ) -> None:
        phone = normalize_phone(row.get("phone", ""), self._settings.default_phone_region)
        lead, created = await leads_service.find_or_create(
            self._session, phone, source=source
        )

        had_consent = bool(
            await self._session.scalar(
                select(ConsentRecord.id).where(ConsentRecord.lead_id == lead.id)
            )
        )

        self._apply_fields(lead, row, source)
        self._apply_minor(lead, row, summary)
        self._apply_eligibility(lead, row)

        if created:
            summary.created += 1
        else:
            summary.updated += 1

        if not had_consent:
            await self._apply_consent(lead, row, source, summary)

        await self._apply_household(lead, row, source, summary)

    def _apply_fields(self, lead: Lead, row: dict[str, str], source: str | None) -> None:
        def fill(attr: str, value: Any) -> None:
            if value in (None, "", NeetCategory.UNKNOWN, RoleHint.UNKNOWN):
                return
            if getattr(lead, attr) in (None, "", NeetCategory.UNKNOWN, RoleHint.UNKNOWN):
                setattr(lead, attr, value)

        fill("full_name", row.get("name"))
        fill("city", row.get("city"))
        fill("language_preference", row.get("language"))
        fill("source", row.get("source") or source)
        fill("target_country", row.get("target_country"))
        fill("budget_band", row.get("budget_band"))
        fill("intake_year", _parse_int(row.get("intake_year", "")))

        role = _ROLE_MAP.get(row.get("role", "").strip().lower())
        if role and lead.role_hint == RoleHint.UNKNOWN:
            lead.role_hint = role

    def _apply_minor(self, lead: Lead, row: dict[str, str], summary: ImportSummary) -> None:
        dob = _parse_date(row.get("dob", ""))
        age = _parse_int(row.get("age", ""))
        status, resolved_age = consent_service.evaluate_minor(dob, age)
        if dob and lead.date_of_birth is None:
            lead.date_of_birth = dob
        if resolved_age is not None and lead.age_years is None:
            lead.age_years = resolved_age
        if status != MinorStatus.UNKNOWN:
            lead.is_minor = status
        consent_service.apply_minor_policy(lead, self._settings)
        if lead.is_minor == MinorStatus.YES:
            summary.minors_flagged += 1

    def _apply_eligibility(self, lead: Lead, row: dict[str, str]) -> None:
        score = _parse_int(row.get("neet_score", ""))
        if score is not None and lead.neet_score is None:
            lead.neet_score = score
        cat = _CATEGORY_MAP.get(row.get("neet_category", "").strip().lower())
        if cat and lead.neet_category == NeetCategory.UNKNOWN:
            lead.neet_category = cat
        year = _parse_int(row.get("neet_year", "")) or self._settings.neet_year
        if year and lead.neet_year is None:
            lead.neet_year = year
        pcb = _parse_float(row.get("pcb_percentage", ""))
        if pcb is not None and lead.pcb_percentage is None:
            lead.pcb_percentage = pcb
        lead.eligibility_flag = compute_eligibility(
            lead.neet_score, lead.neet_category, lead.pcb_percentage, self._settings
        )

    async def _apply_consent(
        self,
        lead: Lead,
        row: dict[str, str],
        source: str | None,
        summary: ImportSummary,
    ) -> None:
        status = _parse_consent(row.get("consent", ""))
        captured = _parse_date(row.get("consent_captured_at", ""))
        await consent_service.record_consent(
            self._session,
            lead,
            status=status,
            method=ConsentMethod.IMPORTED_CSV_ASSERTION,
            verified=False,
            consent_text=None,
            source_reference=row.get("consent_source") or source,
            captured_at=datetime.combine(captured, datetime.min.time()) if captured else None,
            actor="import",
            notes=(
                "Imported assertion; exact consent wording not captured. "
                "Outreach stays blocked until the Phase 1 consent audit "
                "(critique A1) marks this lead verified."
            ),
        )
        if status == ConsentStatus.OPTED_OUT:
            await state_machine.apply_event(
                self._session,
                lead,
                LifecycleEvent.OPT_OUT,
                actor="import",
                reason="csv marked opted-out",
            )
            summary.opt_outs += 1

    async def _apply_household(
        self,
        lead: Lead,
        row: dict[str, str],
        source: str | None,
        summary: ImportSummary,
    ) -> None:
        family_id = row.get("family_id", "").strip()
        parent_phone_raw = row.get("parent_phone", "").strip()

        household: Household | None = None
        if lead.household_id is not None:
            household = await self._session.get(Household, lead.household_id)

        if family_id:
            household = household or await self._get_or_create_household(
                external_ref=family_id, summary=summary
            )
        elif parent_phone_raw and household is None:
            household = await self._get_or_create_household(
                external_ref=f"auto:{lead.phone_e164}", summary=summary
            )

        if household is not None and lead.household_id != household.id:
            lead.household_id = household.id
            summary.households_linked += 1

        if parent_phone_raw:
            try:
                parent_phone = normalize_phone(
                    parent_phone_raw, self._settings.default_phone_region
                )
            except PhoneNormalizationError:
                summary.errors.append((-1, f"invalid parent phone {parent_phone_raw!r}"))
                return
            parent, parent_created = await leads_service.find_or_create(
                self._session, parent_phone, source=source
            )
            if parent_created:
                summary.parents_created += 1
                summary.created += 1
            if parent.role_hint == RoleHint.UNKNOWN:
                parent.role_hint = RoleHint.PARENT
            if not parent.full_name and row.get("parent_name"):
                parent.full_name = row["parent_name"]
            if household is not None and parent.household_id != household.id:
                parent.household_id = household.id
            if parent_created:
                await consent_service.record_consent(
                    self._session,
                    parent,
                    status=ConsentStatus.UNKNOWN,
                    method=ConsentMethod.IMPORTED_CSV_ASSERTION,
                    verified=False,
                    source_reference=source,
                    actor="import",
                    notes="Parent contact from CSV parent_phone column; consent not asserted.",
                )

    async def _get_or_create_household(
        self, *, external_ref: str, summary: ImportSummary
    ) -> Household:
        household = await self._session.scalar(
            select(Household).where(Household.external_ref == external_ref)
        )
        if household is None:
            household = Household(external_ref=external_ref)
            self._session.add(household)
            await self._session.flush()
            summary.households_created += 1
        return household

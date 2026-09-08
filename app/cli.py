"""Operator CLI.

    leadbot import-leads   <file.csv> [--source X] [--dry-run]
    leadbot replay-webhook <payload.json> [--url ...] [--secret ...]
    leadbot show-lead      <phone>
    leadbot check-config
    leadbot send-template  <phone> <template> [--lang en] [--var k=v ...]
    leadbot simulate       <phone> <message>
    leadbot run-sweeps
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import typer

from app.config import get_settings
from app.security.signature import sign_body


def _force_utf8_streams() -> None:
    """Windows consoles default to cp1252, which cannot encode "₹" (and other
    symbols that legitimately appear in config / lead data)."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable
            pass


_force_utf8_streams()

app = typer.Typer(add_completion=False, help="MBBS Abroad Lead Bot operator CLI")


def _session_scope():
    from app.db.session import get_sessionmaker, reset_engine_cache

    reset_engine_cache()
    return get_sessionmaker()()


@app.command("check-config")
def check_config() -> None:
    """Print resolved configuration and unresolved Phase-1 items."""

    settings = get_settings()
    typer.echo(f"app_env               : {settings.app_env}")
    typer.echo(f"database_url          : {settings.database_url}")
    typer.echo(f"redis_url             : {settings.redis_url}")
    typer.echo(f"whatsapp_client       : {settings.whatsapp_client}")
    typer.echo(f"service_window_hours  : {settings.service_window_hours}")
    typer.echo(f"require_verified_consent : {settings.outreach_require_verified_consent}")
    typer.echo(f"consent_gate_enabled  : {settings.consent_gate_enabled}")
    typer.echo(f"consent_ask_sweep     : {settings.consent_ask_sweep_enabled}")
    typer.echo(f"minor_default_policy  : {settings.minor_default_policy.value}")
    typer.echo(f"neet_year             : {settings.neet_year}")
    typer.echo(f"neet_cutoff_general   : {settings.neet_cutoff_general}")
    typer.echo(f"neet_cutoff_obc       : {settings.neet_cutoff_obc}")
    typer.echo(f"stateable_cost_range  : {settings.stateable_cost_range}")
    typer.echo(f"stateable_countries   : {settings.stateable_cost_countries}")
    typer.echo(f"india_compare_range   : {settings.india_compare_cost_range}")
    typer.echo("")
    from app.services.llm.factory import classifier_model, reply_model

    typer.echo(f"llm_provider          : {settings.llm_provider}")
    typer.echo(f"reply_model           : {reply_model(settings)}")
    typer.echo(f"classifier_model      : {classifier_model(settings)}")
    typer.echo(f"bot_autoreply_enabled : {settings.bot_autoreply_enabled}")
    typer.echo(f"guard_enabled         : {settings.guard_enabled}")
    typer.echo(f"guard_regenerate_attempts : {settings.guard_regenerate_attempts}")
    typer.echo(f"kb_path               : {settings.kb_path}")
    typer.echo("")
    unresolved = settings.unresolved_phase1_items
    if not unresolved:
        typer.secho("All tracked Phase-1 items resolved.", fg=typer.colors.GREEN)
        return
    typer.secho(f"{len(unresolved)} unresolved Phase-1 item(s):", fg=typer.colors.YELLOW)
    for item in unresolved:
        typer.echo(f"  - {item}")
    raise typer.Exit(code=1)


@app.command("import-leads")
def import_leads(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    source: str = typer.Option("legacy_db", help="value stored on lead.source"),
    dry_run: bool = typer.Option(False, help="parse + validate, roll back at the end"),
) -> None:
    async def _run() -> None:
        from app.importer import LeadCsvImporter

        settings = get_settings()
        async with _session_scope() as session:
            importer = LeadCsvImporter(session, settings)
            summary = await importer.import_path(path, source=source, dry_run=dry_run)
        typer.echo(json.dumps(summary.as_dict(), indent=2, default=str))

    asyncio.run(_run())


@app.command("replay-webhook")
def replay_webhook(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    url: str = typer.Option("http://localhost:8000/webhook/whatsapp"),
    secret: str = typer.Option("", help="app secret; defaults to META_APP_SECRET"),
) -> None:
    """POST a saved webhook payload with a valid X-Hub-Signature-256 header."""

    body = path.read_bytes()
    app_secret = secret or get_settings().meta_app_secret
    if not app_secret:
        typer.secho("no app secret (set META_APP_SECRET or pass --secret)", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    signature = sign_body(app_secret, body)
    response = httpx.post(
        url,
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
        timeout=30,
    )
    typer.echo(f"HTTP {response.status_code}")
    typer.echo(response.text)
    raise typer.Exit(code=0 if response.status_code < 400 else 1)


@app.command("show-lead")
def show_lead(phone: str = typer.Argument(...)) -> None:
    async def _run() -> None:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.models.lead import Lead
        from app.services.phone import normalize_phone

        settings = get_settings()
        norm = normalize_phone(phone, settings.default_phone_region)
        async with _session_scope() as session:
            lead = await session.scalar(
                select(Lead)
                .where(Lead.phone_e164 == norm.e164)
                .options(
                    selectinload(Lead.messages),
                    selectinload(Lead.consent_records),
                    selectinload(Lead.transitions),
                )
            )
            if lead is None:
                typer.secho(f"no lead for {norm.e164}", fg=typer.colors.RED)
                raise typer.Exit(code=1)
            typer.echo(f"lead {lead.id}  {lead.phone_e164}  ({lead.full_name or '-'})")
            typer.echo(
                f"  state       : {lead.lifecycle_state.value}  "
                f"(funnel: {lead.funnel_stage.value}, phase: {lead.engagement_phase()})"
            )
            typer.echo(
                f"  consent     : {lead.consent_status.value}  verified={lead.consent_verified}"
            )
            typer.echo(
                f"  gate        : {lead.consent_gate.value}  "
                f"reasks={lead.gate_reask_count}  asks_sent={lead.consent_ask_count}"
            )
            typer.echo(
                f"  minor       : {lead.is_minor.value}  policy={lead.minor_policy_status.value}"
            )
            typer.echo(
                f"  eligibility : {lead.eligibility_flag.value}  "
                f"neet={lead.neet_score} {lead.neet_category.value}"
            )
            typer.echo(
                f"  score       : {lead.lead_score.value}  "
                f"temp={lead.interest_temperature.value}  "
                f"({lead.lead_score_reason or '-'})"
            )
            typer.echo(
                f"  qualifiers  : country={lead.target_country or '-'} "
                f"budget={lead.budget_band or '-'} urgency={lead.urgency.value} "
                f"parent_in_loop={lead.parent_in_loop}"
            )
            typer.echo(
                f"  window open : {lead.service_window_open()}  "
                f"expires={lead.service_window_expires_at}"
            )
            typer.echo(f"  human_owned : {lead.human_owned}")
            typer.echo(f"  messages    : {len(lead.messages)}")
            for m in lead.messages[-10:]:
                preview = (m.body or m.template_name or "")[:60]
                typer.echo(
                    f"    [{m.direction.value:8}] {m.message_type.value:9} "
                    f"{m.status.value:9} {preview}"
                )
            typer.echo(f"  transitions : {len(lead.transitions)}")
            for t in lead.transitions[-10:]:
                typer.echo(
                    f"    {t.from_state.value} -> {t.to_state.value}  "
                    f"on {t.event.value}  ({t.actor})"
                )

    asyncio.run(_run())


@app.command("send-template")
def send_template(
    phone: str = typer.Argument(...),
    template: str = typer.Argument(...),
    lang: str = typer.Option("en"),
    var: list[str] = typer.Option([], help="body variables as k=v (repeatable, ordered)"),
) -> None:
    async def _run() -> None:
        import redis.asyncio as redis_asyncio
        from sqlalchemy import select

        from app.models.lead import Lead
        from app.services.outreach import OutreachService
        from app.services.phone import normalize_phone
        from app.services.whatsapp.factory import build_whatsapp_client

        settings = get_settings()
        norm = normalize_phone(phone, settings.default_phone_region)
        redis = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
        wa = build_whatsapp_client(settings)
        try:
            async with _session_scope() as session:
                lead = await session.scalar(select(Lead).where(Lead.phone_e164 == norm.e164))
                if lead is None:
                    typer.secho(f"no lead for {norm.e164}", fg=typer.colors.RED)
                    raise typer.Exit(code=1)
                body_vars = [v.split("=", 1)[1] if "=" in v else v for v in var]
                service = OutreachService(session, redis, settings, wa)
                msg = await service.send_template(
                    lead,
                    template_name=template,
                    language=lang,
                    variables={"body": body_vars} if body_vars else None,
                )
                typer.echo(f"sent: message={msg.id} wamid={msg.wa_message_id}")
        finally:
            await redis.aclose()
            await wa.aclose()

    asyncio.run(_run())


@app.command("simulate")
def simulate(
    phone: str = typer.Argument(..., help="lead phone (created + engaged if new)"),
    message: str = typer.Argument(..., help="the inbound message text to simulate"),
) -> None:
    """Run one conversation turn locally: feed an inbound message to the engine
    and print the bot's reply + the decision trace."""

    async def _run() -> None:
        import redis.asyncio as redis_asyncio
        from sqlalchemy import select

        from app.models.conversation_trace import ConversationTrace
        from app.models.enums import (
            LifecycleState,
            MessageDirection,
            MessageStatus,
            MessageType,
            SentBy,
        )
        from app.models.message import Message
        from app.services import leads as leads_service
        from app.services.conversation.engine import ConversationEngine
        from app.services.knowledge.yaml_kb import load_knowledge_base
        from app.services.llm.factory import build_llm_client
        from app.services.phone import normalize_phone
        from app.services.timeutils import utcnow
        from app.services.whatsapp.factory import build_whatsapp_client
        from app.services.windows import WindowService

        settings = get_settings()
        norm = normalize_phone(phone, settings.default_phone_region)
        redis = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
        wa = build_whatsapp_client(settings)
        llm = build_llm_client(settings)
        kb = load_knowledge_base(settings.kb_path, strict=True)
        try:
            async with _session_scope() as session:
                lead, created = await leads_service.find_or_create(
                    session, norm, source="simulate"
                )
                if created or lead.lifecycle_state == LifecycleState.NEVER_CONTACTED:
                    lead.lifecycle_state = LifecycleState.ENGAGED
                    lead.first_engaged_at = lead.first_engaged_at or utcnow()
                # `simulate` exercises the sales flow; skip the consent+age gate
                # (test it via replay-webhook or the gate unit tests instead)
                from app.models.enums import ConsentGate

                if lead.consent_gate != ConsentGate.CLEARED:
                    lead.consent_gate = ConsentGate.CLEARED
                await WindowService(redis, settings).touch(lead)
                inbound = Message(
                    lead_id=lead.id,
                    direction=MessageDirection.INBOUND,
                    message_type=MessageType.TEXT,
                    body=message,
                    status=MessageStatus.RECEIVED,
                    sent_by=SentBy.LEAD,
                    wa_message_id=f"wamid.SIM-{utcnow().timestamp()}",
                    status_history=[],
                )
                session.add(inbound)
                await session.commit()

                engine = ConversationEngine(
                    session, redis, settings, llm=llm, kb=kb, wa_client=wa
                )
                result = await engine.handle_inbound(lead, inbound)

                typer.echo(f"action     : {result.action}")
                if result.skipped_reason:
                    typer.echo(f"skipped    : {result.skipped_reason}")
                if result.reply_text:
                    typer.secho(f"bot reply  : {result.reply_text}", fg=typer.colors.GREEN)
                typer.echo(f"booking    : {result.booking_detected}")

                trace = await session.scalar(
                    select(ConversationTrace).where(ConversationTrace.id == result.trace_id)
                )
                if trace:
                    typer.echo(
                        f"trace      : attempts={trace.draft_attempts} "
                        f"verdict={trace.guard_verdict} "
                        f"kb={trace.kb_chunk_ids} "
                        f"tokens={trace.llm_input_tokens}/{trace.llm_output_tokens}"
                    )
                    typer.echo(
                        f"score      : {trace.lead_score} ({trace.interest_temperature}) "
                        f"qualifiers={trace.extracted_qualifiers or {}}"
                    )
                    if trace.guard_violations:
                        typer.secho(
                            f"violations : {trace.guard_violations}", fg=typer.colors.YELLOW
                        )
        finally:
            await redis.aclose()
            await wa.aclose()
            await llm.aclose()

    asyncio.run(_run())


@app.command("run-sweeps")
def run_sweeps() -> None:
    """Run all scheduler sweeps once (expire_windows, advance_engagement,
    send_in_window_nudges, run_reengagement) against the configured DB/Redis.
    The Celery beat worker does this on a schedule; this is for manual runs."""

    from app.scheduler.runner import run_all_sweeps_once

    for result in run_all_sweeps_once():
        typer.echo(json.dumps(result))


if __name__ == "__main__":
    app()

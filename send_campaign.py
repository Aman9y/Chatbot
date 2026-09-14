"""Outreach Campaign & Template Sender for MBBS Lead Bot.

Usage examples:
    # 1. Test with 1 or 2 numbers directly:
    python send_campaign.py --numbers "+918767424644, +919876543210"

    # 2. Test with 1 number (interactive preview & confirmation):
    python send_campaign.py --numbers "+918767424644"

    # 3. Send to a list from CSV or text file:
    python send_campaign.py --file leads.csv

    # 4. Dry run (validate numbers and preview without sending):
    python send_campaign.py --numbers "+918767424644" --dry-run

    # 5. Interactive prompt (just run the script with no arguments):
    python send_campaign.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

from app.config import get_settings
from app.db.session import get_sessionmaker, reset_engine_cache
from app.models.enums import ConsentGate, ConsentStatus, LifecycleState, SentBy
from app.models.lead import Lead
from app.redis_client import build_redis_client
from app.services import leads as leads_service
from app.services.outreach import OutreachService
from app.services.phone import PhoneNormalizationError, normalize_phone
from app.services.whatsapp.factory import build_whatsapp_client


def parse_input_numbers(raw_numbers: list[str]) -> list[str]:
    """Parse comma/space/newline-separated phone numbers."""
    cleaned = []
    for item in raw_numbers:
        for part in item.replace(",", " ").split():
            p = part.strip()
            if p:
                cleaned.append(p)
    return cleaned


def read_numbers_from_file(file_path: Path) -> list[str]:
    """Read phone numbers from a CSV, Excel export, or text file.
    Only extracts phone numbers. No names are read or stored.
    """
    if not file_path.exists():
        print(f"[Error] File not found: {file_path}")
        sys.exit(1)

    results: list[str] = []
    suffix = file_path.suffix.lower()

    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        with open(file_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows = [r for r in reader if r and any(cell.strip() for cell in r)]
            if not rows:
                return []

            # Check if there is a header containing phone/mobile/number
            first_row = [c.strip().lower() for c in rows[0]]
            phone_idx = -1
            for i, col in enumerate(first_row):
                if col in ("phone", "phone_number", "mobile", "number", "whatsapp", "contact"):
                    phone_idx = i
                    break

            start_row = 1 if phone_idx != -1 else 0
            target_idx = phone_idx if phone_idx != -1 else 0

            for row in rows[start_row:]:
                if len(row) > target_idx and row[target_idx].strip():
                    results.append(row[target_idx].strip())
    else:
        # Plain text file (one phone number per line)
        with open(file_path, mode="r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    phone = line.split(",")[0].strip()
                    if phone:
                        results.append(phone)

    return results


async def execute_campaign(
    contacts: list[str],
    *,
    template_name: str | None = None,
    custom_message: str | None = None,
    delay_seconds: float = 2.0,
    dry_run: bool = False,
    auto_confirm: bool = False,
) -> None:
    settings = get_settings()
    target_template = template_name or settings.consent_ask_template_name
    template_text = custom_message or settings.opening_message

    print("=" * 65)
    print(" 🚀 MBBS LEAD BOT - OUTREACH CAMPAIGN DISPATCHER")
    print("=" * 65)
    print(f" • WhatsApp Client    : {settings.whatsapp_client.upper()}")
    print(f" • Template Name      : {target_template}")
    print(f" • Total Recipients   : {len(contacts)}")
    print(f" • Delay Between Sends: {delay_seconds}s")
    print(f" • Mode               : {'DRY RUN (No real sends)' if dry_run else 'LIVE SEND'}")
    print("-" * 65)
    print(" 💬 TEMPLATE MESSAGE PREVIEW:")
    print(f"   \"{template_text}\"")
    print("=" * 65)

    # Validate numbers
    valid_contacts = []
    invalid_contacts = []

    for raw_phone in contacts:
        try:
            norm = normalize_phone(raw_phone, settings.default_phone_region)
            valid_contacts.append(norm)
        except PhoneNormalizationError as e:
            invalid_contacts.append((raw_phone, str(e)))

    if invalid_contacts:
        print(f"\n⚠️  {len(invalid_contacts)} invalid phone number(s) skipped:")
        for p, err in invalid_contacts:
            print(f"   - {p}: {err}")

    if not valid_contacts:
        print("\n❌ No valid phone numbers to send to.")
        return

    print(f"\n✅ {len(valid_contacts)} valid contact(s) ready to receive template.")
    for idx, norm in enumerate(valid_contacts[:5], 1):
        print(f"   {idx}. {norm.e164}")
    if len(valid_contacts) > 5:
        print(f"   ... and {len(valid_contacts) - 5} more.")

    if dry_run:
        print("\n🔍 DRY RUN completed. No messages were dispatched.")
        return

    # User confirmation before sending (unless --yes / auto_confirm)
    if not auto_confirm:
        confirm = input(f"\n👉 Proceed with sending to {len(valid_contacts)} number(s)? [Y/n]: ").strip().lower()
        if confirm and confirm not in ("y", "yes"):
            print("❌ Campaign cancelled by user.")
            return

    print("\n⏳ Starting dispatch...\n")

    reset_engine_cache()
    sessionmaker = get_sessionmaker()
    redis = build_redis_client(settings)
    import random
    from datetime import datetime

    wa_client = build_whatsapp_client(settings)

    success_count = 0
    fail_count = 0
    report_rows = []

    try:
        async with sessionmaker() as session:
            outreach = OutreachService(session, redis, settings, wa_client)

            for i, norm in enumerate(valid_contacts, 1):
                timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                percent = int((i / len(valid_contacts)) * 100)
                bar_len = 20
                filled = int(bar_len * i / len(valid_contacts))
                bar = "█" * filled + "░" * (bar_len - filled)

                try:
                    # 1. Find or create lead (phone only, NO name stored)
                    lead, created = await leads_service.find_or_create(
                        session, norm, source="campaign_outreach"
                    )

                    # Initialize consent gate for outreach
                    if lead.consent_gate != ConsentGate.CLEARED:
                        lead.consent_gate = ConsentGate.PENDING_OPT_IN

                    # 2. Dispatch template
                    vars_dict = {"body": [template_text]} if custom_message else None

                    msg = await outreach.send_template(
                        lead,
                        template_name=target_template,
                        language=settings.consent_ask_template_language,
                        variables=vars_dict,
                        purpose="consent_ask",
                        reason=f"campaign:{target_template}",
                        idempotency_key=f"campaign:{lead.id}:{target_template}:{int(time.time() * 1000)}",
                    )

                    lead.consent_ask_count += 1
                    await session.commit()

                    print(f"[{i}/{len(valid_contacts)}] {bar} {percent}% | ✅ {norm.e164} -> SENT")
                    success_count += 1
                    report_rows.append({
                        "phone": norm.e164,
                        "status": "SENT",
                        "timestamp": timestamp_str,
                        "msg_id": msg.wa_message_id,
                        "error": "",
                    })

                except Exception as exc:
                    await session.rollback()
                    print(f"[{i}/{len(valid_contacts)}] {bar} {percent}% | ❌ {norm.e164} -> FAILED: {exc}")
                    fail_count += 1
                    report_rows.append({
                        "phone": norm.e164,
                        "status": "FAILED",
                        "timestamp": timestamp_str,
                        "msg_id": "",
                        "error": str(exc),
                    })

                # Smart human-like jitter interval between sends (e.g. 3 to 6 sec)
                if i < len(valid_contacts) and delay_seconds > 0:
                    # Randomize between 80% and 140% of delay_seconds to simulate human typing/delay
                    jitter_sleep = round(random.uniform(delay_seconds * 0.8, delay_seconds * 1.4), 1)
                    print(f"   ⏳ Waiting {jitter_sleep}s (smart interval)...")
                    await asyncio.sleep(jitter_sleep)

    finally:
        await redis.aclose()
        await wa_client.aclose()

    # Save campaign report CSV
    report_file = Path("campaign_report.csv")
    with open(report_file, "w", newline="", encoding="utf-8-sig") as rf:
        writer = csv.DictWriter(rf, fieldnames=["phone", "status", "timestamp", "msg_id", "error"])
        writer.writeheader()
        writer.writerows(report_rows)

    print("\n" + "=" * 65)
    print(" 🏁 CAMPAIGN SUMMARY")
    print("=" * 65)
    print(f" • Total Processed : {len(valid_contacts)}")
    print(f" • Successfully Sent: {success_count}")
    print(f" • Failed / Blocked : {fail_count}")
    print("=" * 65)
    print("💡 What happens next:")
    print("   When recipients reply to this template on WhatsApp, your bot")
    print("   will automatically receive their message and continue the conversation")
    print("   using the MBBS counseling knowledge base and AI guidance!")
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Send template messages to 1 or more leads on WhatsApp.")
    parser.add_argument(
        "--numbers", "-n",
        type=str,
        help="Comma- or space-separated phone numbers (e.g. '+918767424644, 9876543210')",
    )
    parser.add_argument(
        "--file", "-f",
        type=Path,
        help="Path to CSV or TXT file containing phone numbers",
    )
    parser.add_argument(
        "--template", "-t",
        type=str,
        default=None,
        help="WhatsApp template name (defaults to CONSENT_ASK_TEMPLATE_NAME from .env)",
    )
    parser.add_argument(
        "--message", "-m",
        type=str,
        default=None,
        help="Custom introductory text message to send as the template content",
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=2.0,
        help="Seconds delay between consecutive sends (default: 2.0s to avoid spam flags)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate numbers and preview template without sending any messages",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip interactive confirmation prompt and send immediately",
    )

    args = parser.parse_args()

    contacts: list[str] = []

    if args.numbers:
        contacts = parse_input_numbers([args.numbers])
    elif args.file:
        contacts = read_numbers_from_file(args.file)
    else:
        # Interactive prompt if run with no args
        print("=" * 65)
        print(" MBBS LEAD BOT - TEMPLATE CAMPAIGN LAUNCHER")
        print("=" * 65)
        user_input = input("\nEnter phone number(s) separated by commas, or path to a CSV file:\n> ").strip()
        if not user_input:
            print("No input provided. Exiting.")
            sys.exit(0)

        path_candidate = Path(user_input)
        if path_candidate.exists() and path_candidate.is_file():
            contacts = read_numbers_from_file(path_candidate)
        else:
            contacts = parse_input_numbers([user_input])

    if not contacts:
        print("No contacts found to send to.")
        sys.exit(0)

    asyncio.run(
        execute_campaign(
            contacts,
            template_name=args.template,
            custom_message=args.message,
            delay_seconds=args.delay,
            dry_run=args.dry_run,
            auto_confirm=args.yes,
        )
    )


if __name__ == "__main__":
    main()

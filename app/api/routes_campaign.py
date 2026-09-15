"""Live Automation Campaign Manager & Dashboard.

Handles 2-set batch outreach with 4-5 minute human gaps and 35-45 minute cooldown.
Runs directly on Railway cloud so laptops can be closed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import random
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.logging_config import get_logger, mask_phone
from app.models.enums import ConsentGate
from app.models.lead import Lead
from app.models.message import Message
from app.redis_client import build_redis_client
from app.services import leads as leads_service
from app.services.outreach import OutreachService
from app.services.phone import normalize_phone
from app.services.whatsapp.factory import build_whatsapp_client

logger = get_logger(__name__)
router = APIRouter(tags=["campaign"])

# 50 Clean Unique Numbers from verified list (divided into 2 sets of 25)
SET_1_NUMBERS = [
    "+918591059881", "+919004062366", "+919870247113", "+919321787426", "+919168832255",
    "+919902316263", "+918425074341", "+918097810892", "+919136776835", "+918459484068",
    "+919987302386", "+919076331316", "+919324282521", "+917350503727", "+919818519437",
    "+918657439898", "+918828696284", "+917718858798", "+918850407207", "+917021972474",
    "+919702119037", "+919653476866", "+917420923466", "+918433827180", "+918890865542",
]

SET_2_NUMBERS = [
    "+918975501301", "+918657213530", "+918779004980", "+919867911801", "+918355812875",
    "+919022672403", "+918291521489", "+919892658052", "+919833974479", "+919819326868",
    "+919967351199", "+918422900755", "+919870713035", "+919833846427", "+919920873155",
    "+919969400417", "+917021041984", "+919820384971", "+919969308385", "+918433876334",
    "+919987822945", "+919594117878", "+919321597050", "+919619688364", "+919942297862",
]

class CampaignState:
    def __init__(self):
        self.status = "idle"  # idle | running | paused | cooldown | completed
        self.total_leads = len(SET_1_NUMBERS) + len(SET_2_NUMBERS)
        self.sent_count = 0
        self.failed_count = 0
        self.current_set = 1
        self.current_lead_index = 0
        self.status_text = "Ready to start campaign."
        self.countdown_seconds = 0
        self.last_sent_phone = ""
        self.last_sent_time = ""
        self.task: asyncio.Task | None = None
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.history: list[dict[str, Any]] = []

state = CampaignState()


async def _run_campaign_task():
    global state
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    redis = build_redis_client(settings)
    wa_client = build_whatsapp_client(settings)
    batches = [(1, SET_1_NUMBERS), (2, SET_2_NUMBERS)]

    logger.info("Campaign task started: 50 leads in 2 sets of 25.")

    try:
        for set_idx, batch_numbers in batches:
            state.current_set = set_idx
            state.status = "running"

            for i, phone in enumerate(batch_numbers, 1):
                # Check pause
                await state.pause_event.wait()
                if state.status == "idle":
                    return

                state.current_lead_index = i
                state.status_text = f"Sending message to {phone} (Set {set_idx} - {i}/{len(batch_numbers)})..."
                timestamp_str = datetime.now().strftime("%I:%M:%S %p")

                norm = normalize_phone(phone, settings.default_phone_region)

                send_success = False
                err_msg = ""
                msg_id = ""

                async with sessionmaker() as session:
                    outreach = OutreachService(session, redis, settings, wa_client)
                    try:
                        lead, _ = await leads_service.find_or_create(session, norm, source="cloud_campaign")
                        if lead.consent_gate != ConsentGate.CLEARED:
                            lead.consent_gate = ConsentGate.PENDING_OPT_IN

                        msg = await outreach.send_template(
                            lead,
                            template_name=settings.consent_ask_template_name,
                            language=settings.consent_ask_template_language,
                            variables={"body": [settings.opening_message]},
                            purpose="consent_ask",
                            reason=f"cloud_campaign:set_{set_idx}",
                            idempotency_key=f"cloud:{lead.id}:{int(time.time() * 1000)}",
                        )
                        lead.consent_ask_count += 1
                        await session.commit()
                        send_success = True
                        msg_id = msg.wa_message_id or ""
                    except Exception as e:
                        await session.rollback()
                        err_msg = str(e)
                        logger.error("Campaign send failed for %s: %s", mask_phone(phone), err_msg)

                if send_success:
                    state.sent_count += 1
                    status_label = "SENT"
                else:
                    state.failed_count += 1
                    status_label = "FAILED"

                state.last_sent_phone = phone
                state.last_sent_time = timestamp_str
                state.history.insert(0, {
                    "phone": phone,
                    "set": set_idx,
                    "index": i,
                    "status": status_label,
                    "time": timestamp_str,
                    "error": err_msg[:60] if err_msg else "",
                })

                # If more leads remain in this set, wait 4 to 5 minutes with natural jitter
                is_last_in_set = (i == len(batch_numbers))
                if not is_last_in_set:
                    gap_seconds = random.randint(240, 300)  # 4 to 5 minutes
                    state.status_text = f"Pacing: waiting {gap_seconds // 60}m {gap_seconds % 60}s before next contact..."
                    state.countdown_seconds = gap_seconds

                    for _ in range(gap_seconds):
                        await state.pause_event.wait()
                        if state.status == "idle":
                            return
                        await asyncio.sleep(1)
                        state.countdown_seconds -= 1

            # End of Set 1 -> Enter Cooldown if next set exists
            if set_idx == 1:
                cooldown_seconds = random.randint(2100, 2700)  # 35 to 45 minutes
                state.status = "cooldown"
                state.status_text = f"☕ Set 1 Complete (25/25)! Cooling down for {cooldown_seconds // 60} minutes before Set 2..."
                state.countdown_seconds = cooldown_seconds

                for _ in range(cooldown_seconds):
                    await state.pause_event.wait()
                    if state.status == "idle":
                        return
                    await asyncio.sleep(1)
                    state.countdown_seconds -= 1

        state.status = "completed"
        state.status_text = "🎉 All 50 leads contacted successfully!"
        state.countdown_seconds = 0
    except asyncio.CancelledError:
        state.status = "idle"
        state.status_text = "Campaign stopped."
    except Exception as exc:
        state.status = "error"
        state.status_text = f"Campaign error: {str(exc)}"
        logger.exception("Campaign task crashed: %s", exc)


@router.get("/campaign", response_class=HTMLResponse)
async def campaign_dashboard():
    """Live Automation Dashboard UI."""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Live Campaign Automation — Stellar Lead Bot</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 30, 49, 0.75);
      --border: rgba(255, 255, 255, 0.08);
      --primary: #6366f1;
      --primary-glow: rgba(99, 102, 241, 0.35);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }
    body {
      background: radial-gradient(circle at 50% 0%, #171d33 0%, #090d16 80%);
      color: var(--text);
      min-height: 100vh;
      padding: 20px;
    }
    .container { max-width: 900px; margin: 0 auto; }
    .header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 16px 0 24px; border-bottom: 1px solid var(--border); margin-bottom: 24px;
    }
    .logo { font-size: 1.4rem; font-weight: 700; background: linear-gradient(135deg, #a5b4fc, #6366f1); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .badge-cloud { background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 6px 12px; border-radius: 9999px; font-size: 0.85rem; font-weight: 500; border: 1px solid rgba(16, 185, 129, 0.3); }
    .hero {
      background: var(--card-bg); backdrop-filter: blur(12px); border: 1px solid var(--border);
      border-radius: 18px; padding: 24px; margin-bottom: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.3);
      position: relative; overflow: hidden;
    }
    .hero::after {
      content: ''; position: absolute; top: -50px; right: -50px; width: 150px; height: 150px;
      background: var(--primary-glow); filter: blur(60px); border-radius: 50%; pointer-events: none;
    }
    .status-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
    .pulse-dot { width: 12px; height: 12px; border-radius: 50%; background: #64748b; }
    .pulse-dot.running { background: var(--success); box-shadow: 0 0 12px var(--success); animation: pulse 1.8s infinite; }
    .pulse-dot.cooldown { background: var(--warning); box-shadow: 0 0 12px var(--warning); animation: pulse 1.8s infinite; }
    .pulse-dot.paused { background: var(--danger); }
    @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.2); } 100% { opacity: 1; transform: scale(1); } }
    .status-title { font-size: 1.1rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
    .status-msg { color: var(--text-muted); font-size: 0.95rem; margin-bottom: 20px; }
    .clock-box {
      background: rgba(0, 0, 0, 0.3); border: 1px solid var(--border); border-radius: 12px;
      padding: 16px; display: inline-flex; align-items: center; gap: 12px; margin-bottom: 20px;
    }
    .clock-time { font-size: 2rem; font-weight: 700; color: #e0e7ff; font-variant-numeric: tabular-nums; }
    .clock-label { font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; }
    .controls { display: flex; gap: 12px; flex-wrap: wrap; }
    button {
      padding: 12px 24px; border-radius: 10px; font-weight: 600; font-size: 0.95rem; cursor: pointer;
      border: none; transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 8px;
    }
    .btn-start { background: linear-gradient(135deg, #4f46e5, #6366f1); color: #fff; box-shadow: 0 4px 14px var(--primary-glow); }
    .btn-start:hover { transform: translateY(-1px); filter: brightness(1.1); }
    .btn-pause { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .btn-pause:hover { background: rgba(245, 158, 11, 0.25); }
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .stat-card {
      background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px;
      padding: 18px; backdrop-filter: blur(8px);
    }
    .stat-val { font-size: 1.8rem; font-weight: 700; margin-bottom: 4px; }
    .stat-label { font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
    .table-container {
      background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px;
      overflow: hidden; backdrop-filter: blur(8px);
    }
    .table-header { padding: 18px 24px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 1.05rem; }
    table { width: 100%; border-collapse: collapse; text-align: left; }
    th { padding: 14px 24px; color: var(--text-muted); font-weight: 500; font-size: 0.85rem; border-bottom: 1px solid var(--border); }
    td { padding: 14px 24px; font-size: 0.95rem; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
    tr:last-child td { border-bottom: none; }
    .tag-sent { background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; display: inline-block; }
    .tag-failed { background: rgba(239, 68, 68, 0.15); color: #f87171; padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; display: inline-block; }
    .tag-set { background: rgba(99, 102, 241, 0.15); color: #a5b4fc; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">⚡ Stellar Lead Bot — Automation Reader</div>
      <div class="badge-cloud">☁️ Running on Railway Cloud (Safe to close laptop)</div>
    </div>

    <div class="hero">
      <div class="status-row">
        <div id="pulseDot" class="pulse-dot"></div>
        <div id="statusTitle" class="status-title">IDLE</div>
      </div>
      <div id="statusMsg" class="status-msg">Click 'Start Campaign' below. Railway will dispatch 2 sets of 25 leads with 4-5 min gaps and 35-45 min resting.</div>

      <div class="clock-box" id="clockBox" style="display: none;">
        <div>
          <div id="clockTime" class="clock-time">00:00</div>
          <div id="clockLabel" class="clock-label">Next Action In</div>
        </div>
      </div>

      <div class="controls">
        <button class="btn-start" id="startBtn" onclick="triggerStart()">🚀 Start 50-Lead Campaign</button>
        <button class="btn-pause" id="pauseBtn" onclick="triggerTogglePause()" style="display: none;">⏸️ Pause</button>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-val" id="statSent" style="color: #34d399;">0 / 50</div>
        <div class="stat-label">Total Contacts Sent</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="statSet" style="color: #a5b4fc;">Set 1 of 2</div>
        <div class="stat-label">Current Active Set</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="statFailed" style="color: #f87171;">0</div>
        <div class="stat-label">Failed / Non-WhatsApp</div>
      </div>
    </div>

    <div class="table-container">
      <div class="table-header">Live Contact Log (Auto-Updating)</div>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Phone Number</th>
            <th>Batch / Set</th>
            <th>Status</th>
            <th>Timestamp</th>
          </tr>
        </thead>
        <tbody id="historyBody">
          <tr>
            <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 32px;">No leads dispatched yet. Click 'Start 50-Lead Campaign' above.</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    let isPaused = false;

    function formatSeconds(sec) {
      if (sec <= 0) return "00:00";
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    async function updateStatus() {
      try {
        const res = await fetch('/api/campaign/status');
        const data = await res.json();

        const pulse = document.getElementById('pulseDot');
        const title = document.getElementById('statusTitle');
        const msg = document.getElementById('statusMsg');
        const clockBox = document.getElementById('clockBox');
        const clockTime = document.getElementById('clockTime');
        const clockLabel = document.getElementById('clockLabel');
        const startBtn = document.getElementById('startBtn');
        const pauseBtn = document.getElementById('pauseBtn');

        pulse.className = 'pulse-dot ' + (data.status || 'idle');
        title.innerText = (data.status || 'idle').toUpperCase();
        msg.innerText = data.status_text;

        document.getElementById('statSent').innerText = `${data.sent_count} / ${data.total_leads}`;
        document.getElementById('statSet').innerText = `Set ${data.current_set} of 2`;
        document.getElementById('statFailed').innerText = data.failed_count;

        if (data.status === 'running' || data.status === 'cooldown') {
          clockBox.style.display = 'inline-flex';
          clockTime.innerText = formatSeconds(data.countdown_seconds);
          clockLabel.innerText = data.status === 'cooldown' ? 'Cooldown Remaining' : 'Next Message Countdown';
          startBtn.style.display = 'none';
          pauseBtn.style.display = 'inline-flex';
          pauseBtn.innerText = '⏸️ Pause';
        } else if (data.status === 'paused') {
          clockBox.style.display = 'inline-flex';
          clockLabel.innerText = 'PAUSED';
          startBtn.style.display = 'none';
          pauseBtn.style.display = 'inline-flex';
          pauseBtn.innerText = '▶️ Resume';
        } else if (data.status === 'completed') {
          clockBox.style.display = 'none';
          startBtn.style.display = 'inline-flex';
          startBtn.innerText = '✅ Campaign Completed';
          pauseBtn.style.display = 'none';
        } else {
          clockBox.style.display = 'none';
          startBtn.style.display = 'inline-flex';
          pauseBtn.style.display = 'none';
        }

        // Update history table
        const tbody = document.getElementById('historyBody');
        if (data.history && data.history.length > 0) {
          tbody.innerHTML = data.history.map((row, idx) => `
            <tr>
              <td>${data.history.length - idx}</td>
              <td style="font-weight: 600;">${row.phone}</td>
              <td><span class="tag-set">Set ${row.set}</span></td>
              <td><span class="${row.status === 'SENT' ? 'tag-sent' : 'tag-failed'}">${row.status}</span></td>
              <td style="color: var(--text-muted); font-size: 0.85rem;">${row.time}</td>
            </tr>
          `).join('');
        }
      } catch (err) {
        console.error('Status fetch error:', err);
      }
    }

    async function triggerStart() {
      if (!confirm('Start the 50-lead campaign on Railway Cloud now? You can safely close your laptop afterwards.')) return;
      await fetch('/api/campaign/start', { method: 'POST' });
      updateStatus();
    }

    async function triggerTogglePause() {
      await fetch('/api/campaign/toggle-pause', { method: 'POST' });
      updateStatus();
    }

    // Auto poll every 1.5 seconds for live countdown
    setInterval(updateStatus, 1500);
    updateStatus();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)


@router.get("/api/campaign/status")
async def get_campaign_status():
    global state
    return JSONResponse(content={
        "status": state.status,
        "total_leads": state.total_leads,
        "sent_count": state.sent_count,
        "failed_count": state.failed_count,
        "current_set": state.current_set,
        "current_lead_index": state.current_lead_index,
        "status_text": state.status_text,
        "countdown_seconds": max(0, state.countdown_seconds),
        "last_sent_phone": state.last_sent_phone,
        "last_sent_time": state.last_sent_time,
        "history": state.history[:50],
    })


@router.post("/api/campaign/start")
async def start_campaign():
    global state
    if state.status in ("running", "cooldown"):
        return {"ok": True, "message": "Campaign is already running."}

    state.status = "running"
    state.pause_event.set()
    state.task = asyncio.create_task(_run_campaign_task())
    return {"ok": True, "message": "Campaign task launched in background."}


@router.post("/api/campaign/toggle-pause")
async def toggle_pause():
    global state
    if state.status == "paused":
        state.status = "running"
        state.pause_event.set()
        state.status_text = "Resumed campaign."
    elif state.status in ("running", "cooldown"):
        state.status = "paused"
        state.pause_event.clear()
        state.status_text = "Campaign paused by user."
    return {"ok": True, "status": state.status}


@router.get("/api/campaign/lead-check")
async def lead_check(phone: str = "7304377739"):
    sessionmaker = get_sessionmaker()
    suffix = phone.strip()[-10:]
    async with sessionmaker() as session:
        stmt = select(Lead).where(
            (Lead.phone_e164.like(f"%{suffix}%")) | (Lead.phone_raw.like(f"%{suffix}%"))
        )
        res = await session.execute(stmt)
        leads = res.scalars().all()

        out_leads = []
        for l in leads:
            msg_stmt = (
                select(Message)
                .where((Message.lead_id == l.id) | (Message.counterparty_phone.like(f"%{suffix}%")))
                .order_by(Message.created_at.asc())
            )
            msg_res = await session.execute(msg_stmt)
            msgs = msg_res.scalars().all()
            out_leads.append({
                "id": str(l.id),
                "phone_e164": l.phone_e164,
                "neet_score": l.neet_score,
                "pcb_percentage": l.pcb_percentage,
                "target_country": l.target_country,
                "country_still_deciding": l.country_still_deciding,
                "eligibility_flag": str(l.eligibility_flag),
                "lifecycle_state": str(l.lifecycle_state),
                "consent_gate": str(l.consent_gate),
                "created_at": str(l.created_at),
                "last_inbound_at": str(l.last_inbound_at),
                "last_outbound_at": str(l.last_outbound_at),
                "total_messages": len(msgs),
                "messages": [
                    {
                        "direction": str(m.direction),
                        "created_at": str(m.created_at),
                        "status": str(m.status),
                        "body": m.body,
                    }
                    for m in msgs
                ],
            })
        return {"count": len(out_leads), "phone": phone, "leads": out_leads}

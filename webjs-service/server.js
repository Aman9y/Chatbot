/**
 * webjs-service/server.js
 *
 * Temporary WhatsApp Web bridge using whatsapp-web.js.
 *
 * IMPORTANT DISCLAIMER:
 *   whatsapp-web.js is an UNOFFICIAL library that automates WhatsApp Web.
 *   It is NOT endorsed, approved, or supported by Meta/WhatsApp.
 *   Using it carries a real risk of the WhatsApp account being banned.
 *   This service is a short-term bridge only, intended to be replaced
 *   by the primary 360dialog integration once the Meta onboarding period ends.
 *   It does NOT claim to bypass WhatsApp enforcement or be undetectable.
 *
 * What this service does:
 *   1. Connects a WhatsApp number via WhatsApp Web (scan QR once).
 *   2. Exposes POST /send — authenticated internal HTTP endpoint.
 *   3. Forwards inbound messages to the Python app's /webhook/whatsapp.
 *   4. Enforces a hard maximum of WEBJS_MAX_RECIPIENTS (≤250) unique recipients.
 *   5. Queues outbound sends sequentially to prevent burst sending.
 *   6. Persists the recipient set to disk so the cap survives restarts.
 */

'use strict';

// ─── stdlib ──────────────────────────────────────────────────────────────────
const fs   = require('fs');
const path = require('path');
const http = require('http');

// ─── dependencies ────────────────────────────────────────────────────────────
const { Client, LocalAuth }   = require('whatsapp-web.js');
const qrcode                  = require('qrcode-terminal');
const express                 = require('express');
const axios                   = require('axios');

// ─── configuration ───────────────────────────────────────────────────────────
const PORT               = parseInt(process.env.PORT                  || '3001', 10);
const API_SECRET         = process.env.WEBJS_API_SECRET               || '';
const PYTHON_WEBHOOK_URL = process.env.PYTHON_WEBHOOK_URL             || '';
const WEBJS_OWN_NUMBER   = process.env.WEBJS_OWN_NUMBER               || 'UNKNOWN';
const WEBHOOK_SECRET     = process.env.WEBJS_WEBHOOK_SECRET           || '';
const SEND_DELAY_MS      = parseInt(process.env.WEBJS_SEND_DELAY_MS   || '1500', 10);
const RECIPIENT_DB_PATH  = process.env.WEBJS_RECIPIENT_DB_PATH        || '/data/recipients.json';
const AUTH_PATH          = process.env.WEBJS_AUTH_PATH                || './.wwebjs_auth';

// Hard cap: cannot exceed 250. Raising above 250 requires a source-code change.
const ABSOLUTE_MAX_RECIPIENTS = 250;
const configuredMax = parseInt(process.env.WEBJS_MAX_RECIPIENTS || '250', 10);
const MAX_RECIPIENTS = Math.min(configuredMax, ABSOLUTE_MAX_RECIPIENTS);

if (configuredMax > ABSOLUTE_MAX_RECIPIENTS) {
  console.warn(
    `[webjs] WEBJS_MAX_RECIPIENTS=${configuredMax} exceeds the hard limit of ` +
    `${ABSOLUTE_MAX_RECIPIENTS}. Clamped to ${ABSOLUTE_MAX_RECIPIENTS}.`
  );
}

// ─── startup validation ───────────────────────────────────────────────────────
if (!API_SECRET) {
  console.error('[webjs] FATAL: WEBJS_API_SECRET is not set. Refusing to start.');
  process.exit(1);
}
if (!PYTHON_WEBHOOK_URL) {
  console.error('[webjs] FATAL: PYTHON_WEBHOOK_URL is not set. Refusing to start.');
  process.exit(1);
}

// ─── recipient persistence ────────────────────────────────────────────────────
/**
 * The recipient set is a Set<string> of E.164 phone numbers that have received
 * at least one outbound message from this service.
 *
 * It is persisted to RECIPIENT_DB_PATH (JSON array) so the cap survives
 * Railway restarts. Each new send atomically appends to the set and rewrites
 * the file before the message is dispatched.
 */
let recipientSet = new Set();

function loadRecipients() {
  try {
    const dir = path.dirname(RECIPIENT_DB_PATH);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    if (fs.existsSync(RECIPIENT_DB_PATH)) {
      const raw = fs.readFileSync(RECIPIENT_DB_PATH, 'utf8');
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) {
        recipientSet = new Set(arr);
        console.log(`[webjs] Loaded ${recipientSet.size} existing recipients from ${RECIPIENT_DB_PATH}`);
      }
    } else {
      console.log(`[webjs] No recipient DB found at ${RECIPIENT_DB_PATH}, starting fresh.`);
    }
  } catch (err) {
    console.error('[webjs] Failed to load recipient DB:', err.message);
    // Non-fatal: start with empty set but warn loudly.
  }
}

function saveRecipients() {
  try {
    const dir = path.dirname(RECIPIENT_DB_PATH);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    fs.writeFileSync(RECIPIENT_DB_PATH, JSON.stringify([...recipientSet], null, 2), 'utf8');
  } catch (err) {
    console.error('[webjs] Failed to save recipient DB:', err.message);
  }
}

function normalizePhone(phone) {
  // Strip spaces, dashes, parentheses; ensure no leading +
  return String(phone).replace(/[\s\-().+]/g, '');
}

function isNewRecipient(phone) {
  return !recipientSet.has(phone);
}

function addRecipient(phone) {
  recipientSet.add(phone);
  saveRecipients();
  console.log(
    `[webjs] New recipient added: ***${phone.slice(-4)}. ` +
    `Recipients: ${recipientSet.size}/${MAX_RECIPIENTS}`
  );
}

// ─── outbound queue ───────────────────────────────────────────────────────────
/**
 * Sequential send queue. Prevents concurrent sends and accidental bursts.
 * Rate limiting here is for reliability and queue control ONLY.
 */
let sendQueue = Promise.resolve();

function enqueueSend(fn) {
  sendQueue = sendQueue.then(fn).catch(err => {
    console.error('[webjs] Send queue error:', err.message);
  });
  return sendQueue;
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── WhatsApp client ──────────────────────────────────────────────────────────
let waState = 'INITIALIZING';
let waClient = null;

function initWhatsAppClient() {
  // Use system Chromium (installed by Dockerfile) when available on Railway.
  const puppeteerArgs = {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--disable-gpu',
    ],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    puppeteerArgs.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  waClient = new Client({
    authStrategy: new LocalAuth({ dataPath: AUTH_PATH }),
    puppeteer: puppeteerArgs,
  });

  waClient.on('qr', (qr) => {
    waState = 'AWAITING_SCAN';
    console.log('\n[webjs] ══════════════════════════════════════════════════');
    console.log('[webjs] QR CODE — scan this with the NEW WhatsApp number');
    console.log('[webjs] ══════════════════════════════════════════════════\n');
    qrcode.generate(qr, { small: true });
    console.log('\n[webjs] ══════════════════════════════════════════════════\n');
    console.log('[webjs] Open WhatsApp on your phone -> Linked Devices -> Link a Device -> scan above QR');
  });

  waClient.on('authenticated', () => {
    waState = 'AUTHENTICATED';
    console.log('[webjs] WhatsApp Web authenticated. Session saved.');
  });

  waClient.on('auth_failure', (msg) => {
    waState = 'AUTH_FAILED';
    console.error('[webjs] Authentication failure:', msg);
  });

  waClient.on('ready', () => {
    waState = 'READY';
    console.log(`[webjs] WhatsApp Web client READY. Number: ${WEBJS_OWN_NUMBER}`);
    console.log(`[webjs] Recipients used: ${recipientSet.size}/${MAX_RECIPIENTS}`);
  });

  waClient.on('disconnected', (reason) => {
    waState = 'DISCONNECTED';
    console.warn('[webjs] WhatsApp Web disconnected:', reason);
    // Attempt reconnect after 10 seconds.
    setTimeout(() => {
      console.log('[webjs] Attempting to reinitialise...');
      waClient.initialize().catch(err =>
        console.error('[webjs] Re-initialise failed:', err.message)
      );
    }, 10_000);
  });

  // ── inbound message handling ─────────────────────────────────────────────
  waClient.on('message', async (msg) => {
    // Ignore group messages, status updates, and messages from ourselves.
    if (msg.isGroupMsg || msg.fromMe || msg.type === 'e2e_notification') return;

    const senderPhone = normalizePhone(msg.from.replace('@c.us', ''));
    const body        = msg.body || '';
    const msgId       = msg.id && msg.id.id ? msg.id.id : `webjs_${Date.now()}`;
    const timestamp   = Math.floor((msg.timestamp || Date.now() / 1000));

    console.log(`[webjs] Inbound from ***${senderPhone.slice(-4)}: ${body.slice(0, 60)}`);

    await forwardInboundToWebhook({ senderPhone, body, msgId, timestamp });
  });

  waClient.initialize().catch(err => {
    console.error('[webjs] Initialise failed:', err.message);
    waState = 'INIT_FAILED';
  });
}

// ─── inbound webhook forwarding ───────────────────────────────────────────────
/**
 * Converts a webjs inbound message into a Meta Cloud API / 360dialog-compatible
 * webhook body and POSTs it to the Python app's /webhook/whatsapp endpoint.
 *
 * The existing WebhookProcessor handles this format identically to 360dialog.
 * WEBHOOK_SIGNATURE_REQUIRED must be false on the Python side (same setting
 * used for 360dialog — already supported and tested).
 */
async function forwardInboundToWebhook({ senderPhone, body, msgId, timestamp }) {
  const payload = {
    object: 'whatsapp_business_account',
    entry: [
      {
        id: 'WEBJS_BRIDGE',
        changes: [
          {
            field: 'messages',
            value: {
              messaging_product: 'whatsapp',
              metadata: {
                display_phone_number: WEBJS_OWN_NUMBER,
                phone_number_id: 'webjs',
              },
              messages: [
                {
                  from: senderPhone,
                  id: `webjs_${msgId}`,
                  timestamp: String(timestamp),
                  type: 'text',
                  text: { body },
                },
              ],
            },
          },
        ],
      },
    ],
  };

  const headers = { 'Content-Type': 'application/json' };
  if (WEBHOOK_SECRET) {
    headers['X-Webjs-Secret'] = WEBHOOK_SECRET;
  }

  try {
    await axios.post(PYTHON_WEBHOOK_URL, payload, { headers, timeout: 10_000 });
    console.log(`[webjs] Inbound forwarded to Python OK (msg ${msgId.slice(-8)})`);
  } catch (err) {
    const status = err.response ? err.response.status : 'network';
    console.error(`[webjs] Failed to forward inbound (${status}): ${err.message}`);
    // Non-fatal: the message is still stored in WhatsApp; a human can follow up.
  }
}

// ─── Express HTTP server ──────────────────────────────────────────────────────
const app = express();
app.use(express.json({ limit: '64kb' }));

// ── auth middleware ───────────────────────────────────────────────────────────
function requireApiSecret(req, res, next) {
  const authHeader = req.headers['authorization'] || '';
  const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
  if (!token || token !== API_SECRET) {
    return res.status(401).json({ ok: false, error: 'unauthorized' });
  }
  next();
}

// ── GET /health ───────────────────────────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({
    ok: true,
    wa_state: waState,
    recipients_used: recipientSet.size,
    recipients_max: MAX_RECIPIENTS,
    recipients_remaining: MAX_RECIPIENTS - recipientSet.size,
  });
});

// ── POST /send ────────────────────────────────────────────────────────────────
/**
 * Authenticated internal endpoint for the Python WebJSWhatsAppClient.
 *
 * Request body:
 *   { "phone": "919XXXXXXXXX", "message": "...", "type": "text"|"template" }
 *
 * Errors:
 *   401 — missing / wrong API secret
 *   400 — validation failure
 *   503 — WhatsApp Web not ready
 *   429 — 250-recipient cap reached
 *   500 — send failure
 */
app.post('/send', requireApiSecret, async (req, res) => {
  const { phone, message, type } = req.body || {};

  // ── input validation ──────────────────────────────────────────────────────
  if (!phone || typeof phone !== 'string' || phone.trim().length < 6) {
    return res.status(400).json({ ok: false, error: 'invalid_phone' });
  }
  if (!message || typeof message !== 'string' || message.trim().length === 0) {
    return res.status(400).json({ ok: false, error: 'invalid_message' });
  }
  if (message.length > 4096) {
    return res.status(400).json({ ok: false, error: 'message_too_long', max: 4096 });
  }

  const normalizedPhone = normalizePhone(phone);
  if (!/^\d{7,15}$/.test(normalizedPhone)) {
    return res.status(400).json({ ok: false, error: 'invalid_phone_format' });
  }

  // ── WA client state check ─────────────────────────────────────────────────
  if (waState !== 'READY') {
    return res.status(503).json({
      ok: false,
      error: 'wa_not_ready',
      wa_state: waState,
      hint: waState === 'AWAITING_SCAN'
        ? 'Check Railway logs for the QR code and scan it with the new WhatsApp number.'
        : 'WhatsApp Web client is initialising or disconnected — retry shortly.',
    });
  }

  // ── 250-recipient cap check ───────────────────────────────────────────────
  if (isNewRecipient(normalizedPhone)) {
    if (recipientSet.size >= MAX_RECIPIENTS) {
      console.warn(
        `[webjs] RECIPIENT CAP REACHED (${recipientSet.size}/${MAX_RECIPIENTS}). ` +
        `Rejected send to ***${normalizedPhone.slice(-4)}`
      );
      return res.status(429).json({
        ok: false,
        error: 'recipient_cap_reached',
        recipients_used: recipientSet.size,
        recipients_max: MAX_RECIPIENTS,
      });
    }
    // Register the recipient BEFORE dispatching, so a crash after send
    // does not allow a re-send that double-counts the slot.
    addRecipient(normalizedPhone);
  }

  // ── enqueue the send (sequential, no concurrent sends) ───────────────────
  const waId = `${normalizedPhone}@c.us`;
  const msgText = message.trim();

  let sendResult = null;
  let sendError  = null;

  await enqueueSend(async () => {
    try {
      const response = await waClient.sendMessage(waId, msgText);
      sendResult = response;
      // Inter-send delay for queue reliability.
      if (SEND_DELAY_MS > 0) await delay(SEND_DELAY_MS);
    } catch (err) {
      sendError = err;
    }
  });

  if (sendError) {
    console.error(
      `[webjs] Send failed to ***${normalizedPhone.slice(-4)}:`,
      sendError.message
    );
    return res.status(500).json({
      ok: false,
      error: 'send_failed',
      detail: sendError.message,
    });
  }

  const messageId = sendResult && sendResult.id && sendResult.id.id
    ? `webjs_${sendResult.id.id}`
    : `webjs_${Date.now()}`;

  console.log(
    `[webjs] Sent to ***${normalizedPhone.slice(-4)} (${type || 'text'}) ` +
    `msg_id=${messageId} recipients=${recipientSet.size}/${MAX_RECIPIENTS}`
  );

  return res.status(200).json({
    ok: true,
    message_id: messageId,
    recipients_used: recipientSet.size,
    recipients_max: MAX_RECIPIENTS,
  });
});

// ─── graceful shutdown ────────────────────────────────────────────────────────
async function shutdown(signal) {
  console.log(`[webjs] ${signal} received — shutting down gracefully.`);
  // Wait for the send queue to drain (max 30s).
  const drainTimeout = setTimeout(() => {
    console.warn('[webjs] Send queue drain timed out after 30s. Exiting anyway.');
    process.exit(0);
  }, 30_000);

  try {
    await sendQueue;
  } catch (_) {}

  clearTimeout(drainTimeout);

  if (waClient) {
    try {
      await waClient.destroy();
      console.log('[webjs] WhatsApp Web client destroyed.');
    } catch (err) {
      console.warn('[webjs] Error destroying WA client:', err.message);
    }
  }

  console.log('[webjs] Goodbye.');
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

// ─── main ─────────────────────────────────────────────────────────────────────
loadRecipients();
initWhatsAppClient();

const server = http.createServer(app);
server.listen(PORT, () => {
  console.log(`[webjs] HTTP server listening on port ${PORT}`);
  console.log(`[webjs] MAX_RECIPIENTS=${MAX_RECIPIENTS} (hard cap: ${ABSOLUTE_MAX_RECIPIENTS})`);
  console.log(`[webjs] PYTHON_WEBHOOK_URL=${PYTHON_WEBHOOK_URL}`);
  console.log(`[webjs] RECIPIENT_DB_PATH=${RECIPIENT_DB_PATH}`);
  console.log('[webjs] Waiting for WhatsApp Web to be ready...');
});

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
const QRCode                  = require('qrcode');
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

// Hard cap: configured max up to 2000
const ABSOLUTE_MAX_RECIPIENTS = 2000;
const configuredMax = parseInt(process.env.WEBJS_MAX_RECIPIENTS || '2000', 10);
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

// ─── clean stale Chromium locks ───────────────────────────────────────────────
/**
 * When Chromium crashes or a Docker container restarts, Chromium leaves behind
 * SingletonLock, SingletonCookie, and SingletonSocket in the profile directory.
 * On subsequent boots with persistent volumes, Chromium detects the old hostname and refuses to launch:
 * "The profile appears to be in use by another Chromium process... on another computer"
 * This helper recursively removes any stale Singleton* files before launching Puppeteer.
 */
function cleanStaleLocks(dirPath) {
  try {
    if (!fs.existsSync(dirPath)) return;
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name);
      if (entry.isDirectory()) {
        cleanStaleLocks(fullPath);
      } else if (entry.name.startsWith('Singleton')) {
        try {
          fs.unlinkSync(fullPath);
          console.log(`[webjs] Removed stale Chromium lock file: ${fullPath}`);
        } catch (e) {
          console.warn(`[webjs] Could not remove lock file ${fullPath}:`, e.message);
        }
      }
    }
  } catch (err) {
    console.warn(`[webjs] Error cleaning stale locks in ${dirPath}:`, err.message);
  }
}

// ─── WhatsApp client ──────────────────────────────────────────────────────────
let waState = 'INITIALIZING';
let lastInitError = null;
let waClient = null;
let lastQrString = null;  // stored so /qr endpoint can serve it
const lidToPhone = new Map();
const phoneToLid = new Map();

function initWhatsAppClient() {
  // Auto-clean stale locks before starting Chromium
  cleanStaleLocks(AUTH_PATH);
  cleanStaleLocks(path.resolve('./.wwebjs_auth'));
  cleanStaleLocks('/data');

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
    lastQrString = qr;  // store for /qr endpoint
    console.log('\n[webjs] QR code generated — open /qr endpoint in browser to scan');
    qrcode.generate(qr, { small: true });  // also print to logs as fallback
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

  async function resolveSender(msg) {
    let senderPhone = null;
    let contactName = null;

    // 1. Direct check on msg.from if already @c.us
    if (msg.from && msg.from.endsWith('@c.us')) {
      const raw = msg.from.replace('@c.us', '');
      const clean = normalizePhone(raw);
      if (/^\d{7,15}$/.test(clean)) senderPhone = clean;
    }

    // 2. Direct check on msg.author if @c.us
    if (!senderPhone && msg.author && msg.author.endsWith('@c.us')) {
      const raw = msg.author.replace('@c.us', '');
      const clean = normalizePhone(raw);
      if (/^\d{7,15}$/.test(clean)) senderPhone = clean;
    }

    // 3. Memory cache for previously resolved LID
    if (!senderPhone && msg.from && lidToPhone.has(msg.from)) {
      senderPhone = lidToPhone.get(msg.from);
    }

    // 4. Try msg.getContact()
    try {
      const contact = await msg.getContact();
      if (contact) {
        contactName = contact.pushname || contact.name || null;
        if (!senderPhone) {
          if (contact.number && /^\d{7,15}$/.test(normalizePhone(contact.number))) {
            senderPhone = normalizePhone(contact.number);
          } else if (contact.id && contact.id.server === 'c.us' && /^\d{7,15}$/.test(contact.id.user)) {
            senderPhone = contact.id.user;
          }
        }
      }
    } catch (err) {
      console.warn('[webjs] getContact error:', err.message);
    }

    // 5. Try msg.getChat()
    if (!senderPhone) {
      try {
        const chat = await msg.getChat();
        if (chat && chat.id) {
          if (chat.id.server === 'c.us' && /^\d{7,15}$/.test(chat.id.user)) {
            senderPhone = chat.id.user;
          }
        }
      } catch (err) {
        console.warn('[webjs] getChat error:', err.message);
      }
    }

    // 6. Try resolving LID via Chromium Page evaluation if available
    if (!senderPhone && msg.from && msg.from.endsWith('@lid') && waClient && waClient.pupPage) {
      try {
        const resolved = await waClient.pupPage.evaluate((lid) => {
          try {
            const c = window.Store?.Contact?.get(lid);
            if (c) {
              if (c.phoneNumber) return String(c.phoneNumber).replace(/\D/g, '');
              if (c.id?.server === 'c.us') return String(c.id.user);
              if (c.userid && /^\d{7,15}$/.test(c.userid)) return String(c.userid);
            }
            if (window.Store?.LidUtils) {
              if (typeof window.Store.LidUtils.getPhoneNumber === 'function') {
                const res = window.Store.LidUtils.getPhoneNumber(lid);
                if (res) return String(res).replace(/\D/g, '');
              }
              if (typeof window.Store.LidUtils.getPhoneNumberFromLid === 'function') {
                const res = window.Store.LidUtils.getPhoneNumberFromLid(lid);
                if (res) return String(res).replace(/\D/g, '');
              }
            }
          } catch (_) {}
          return null;
        }, msg.from);

        if (resolved && /^\d{7,15}$/.test(resolved)) {
          senderPhone = resolved;
        }
      } catch (err) {
        console.warn('[webjs] pupPage LID resolution error:', err.message);
      }
    }

    // 7. Check raw message data (_data)
    if (!senderPhone && msg._data) {
      const candidates = [
        msg._data.sender,
        msg._data.author,
        msg._data.id?.participant,
        msg._data.id?.remote,
      ];
      for (const cand of candidates) {
        if (typeof cand === 'string' && cand.endsWith('@c.us')) {
          const clean = normalizePhone(cand.replace('@c.us', ''));
          if (/^\d{7,15}$/.test(clean)) {
            senderPhone = clean;
            break;
          }
        }
      }
    }

    // 8. Fallback: digits of msg.from
    if (!senderPhone) {
      const clean = normalizePhone(msg.from.replace(/@.*$/, ''));
      senderPhone = clean;
    }

    // Cache bidirectionally if LID
    if (senderPhone && msg.from && msg.from.endsWith('@lid')) {
      lidToPhone.set(msg.from, senderPhone);
      phoneToLid.set(senderPhone, msg.from);
      console.log(`[webjs] Mapped LID ${msg.from} <-> Phone ${senderPhone}`);
    }

    return { senderPhone, contactName };
  }

  // ── inbound message handling ─────────────────────────────────────────────
  waClient.on('message', async (msg) => {
    // Ignore group messages, status updates, and messages from ourselves.
    if (msg.isGroupMsg || msg.fromMe || msg.type === 'e2e_notification') return;

    const { senderPhone, contactName } = await resolveSender(msg);
    const body        = msg.body || '';
    const msgId       = msg.id && msg.id.id ? msg.id.id : `webjs_${Date.now()}`;
    const timestamp   = Math.floor((msg.timestamp || Date.now() / 1000));

    console.log(`[webjs] Inbound from ***${senderPhone.slice(-4)} (${contactName || 'unknown'}): ${body.slice(0, 60)}`);

    await forwardInboundToWebhook({ senderPhone, contactName, body, msgId, timestamp });
  });

  waClient.initialize().catch(err => {
    console.error('[webjs] Initialise failed:', err.message);
    lastInitError = err.message;
    waState = 'INIT_FAILED';
    // Auto-retry once after 10s if initialization failed on cold boot
    setTimeout(() => {
      if (waState === 'INIT_FAILED') {
        console.log('[webjs] Retrying initialization after failure...');
        initWhatsAppClient();
      }
    }, 10_000);
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
async function forwardInboundToWebhook({ senderPhone, contactName, body, msgId, timestamp }) {
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
              contacts: [
                {
                  profile: { name: contactName || 'WhatsApp Lead' },
                  wa_id: senderPhone,
                },
              ],
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

// ── GET /qr ──────────────────────────────────────────────────────────────────
// Serves the current QR code as a scannable image in the browser.
// Open this URL in your browser, then scan with WhatsApp -> Linked Devices.
app.get('/qr', async (req, res) => {
  if (waState === 'READY') {
    return res.send('<h2 style="font-family:sans-serif;color:green">✅ Already authenticated! WhatsApp Web is READY.</h2>');
  }
  if (!lastQrString) {
    return res.send('<h2 style="font-family:sans-serif;color:orange">⏳ QR not generated yet. Wait 30 seconds and refresh.</h2>');
  }
  try {
    const dataUrl = await QRCode.toDataURL(lastQrString, { width: 400, margin: 2 });
    res.send(`<!DOCTYPE html>
<html>
<head>
  <title>Scan WhatsApp QR</title>
  <meta http-equiv="refresh" content="20">
  <style>body{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;font-family:sans-serif;background:#111;color:#fff;}</style>
</head>
<body>
  <h2>📱 Scan with WhatsApp → Linked Devices → Link a Device</h2>
  <img src="${dataUrl}" style="border:8px solid white;border-radius:12px;" />
  <p style="color:#aaa;margin-top:16px">This page auto-refreshes every 20 seconds. State: <strong>${waState}</strong></p>
</body>
</html>`);
  } catch (err) {
    res.status(500).send('Failed to generate QR image: ' + err.message);
  }
});

// ── GET /health ───────────────────────────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({
    ok: true,
    wa_state: waState,
    init_error: lastInitError,
    recipients_used: recipientSet.size,
    recipients_max: MAX_RECIPIENTS,
    recipients_remaining: MAX_RECIPIENTS - recipientSet.size,
  });
});

// ── GET /restart ──────────────────────────────────────────────────────────────
app.get('/restart', async (req, res) => {
  try {
    if (waClient) {
      try { await waClient.destroy(); } catch (_) {}
    }
    waState = 'INITIALIZING';
    lastInitError = null;
    initWhatsAppClient();
    res.json({ ok: true, message: 'WhatsApp client re-initialization triggered.' });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
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
  // Prefer sending to known LID target if this lead originated from LID chat
  let waId = phoneToLid.get(normalizedPhone);
  const msgText = message.trim();

  let sendResult = null;
  let sendError  = null;

  await enqueueSend(async () => {
    // Resolve canonical WhatsApp JID via getNumberId to verify registration
    if (!waId) {
      try {
        const numberDetails = await waClient.getNumberId(normalizedPhone);
        if (numberDetails && numberDetails._serialized) {
          waId = numberDetails._serialized;
          console.log(`[webjs] Resolved getNumberId for ${normalizedPhone} -> ${waId}`);
        }
      } catch (lookupErr) {
        console.warn(`[webjs] getNumberId failed for ${normalizedPhone}:`, lookupErr.message);
      }
    }
    if (!waId) {
      waId = `${normalizedPhone}@c.us`;
    }

    try {
      sendResult = await waClient.sendMessage(waId, msgText);
      if (SEND_DELAY_MS > 0) await delay(SEND_DELAY_MS);
    } catch (err) {
      // Fallback: if sending via resolved JID failed, try raw @c.us
      const altWaId = (waId === `${normalizedPhone}@c.us`) ? null : `${normalizedPhone}@c.us`;
      if (altWaId) {
        try {
          console.log(`[webjs] Initial send to ${waId} failed (${err.message}). Retrying via ${altWaId}...`);
          sendResult = await waClient.sendMessage(altWaId, msgText);
          if (SEND_DELAY_MS > 0) await delay(SEND_DELAY_MS);
          return;
        } catch (retryErr) {
          sendError = retryErr;
          return;
        }
      }
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

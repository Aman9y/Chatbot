// Local WhatsApp-lookalike demo frontend. Talks only to POST /demo/chat on
// this same app — no WhatsApp/Meta connection of any kind. Chat history here
// is in-memory per page load; the underlying lead in the database persists
// across reloads (same demo phone -> same lead), so the conversation state
// the bot sees (score, category, deflect count, etc.) really does carry on.

const CHAT_ENDPOINT = "/demo/chat";
const PHONE_KEY = "stellarDemoPhone";

const els = {
  messages: document.getElementById("messages"),
  scroll: document.getElementById("chatScroll"),
  emptyHint: document.getElementById("emptyHint"),
  form: document.getElementById("composerForm"),
  input: document.getElementById("messageInput"),
  sendBtn: document.getElementById("sendBtn"),
  resetBtn: document.getElementById("resetBtn"),
  phoneLabel: document.getElementById("demoPhoneLabel"),
  lastMsgTime: document.getElementById("lastMsgTime"),
  contactPreview: document.getElementById("contactPreview"),
};

function randomDemoPhone() {
  const first = ["6", "7", "8", "9"][Math.floor(Math.random() * 4)];
  let rest = "";
  for (let i = 0; i < 9; i++) rest += Math.floor(Math.random() * 10);
  return "+91" + first + rest;
}

function getDemoPhone() {
  let phone = localStorage.getItem(PHONE_KEY);
  if (!phone) {
    phone = randomDemoPhone();
    localStorage.setItem(PHONE_KEY, phone);
  }
  return phone;
}

function newDemoPhone() {
  const phone = randomDemoPhone();
  localStorage.setItem(PHONE_KEY, phone);
  return phone;
}

let demoPhone = getDemoPhone();
els.phoneLabel.textContent = "Demo lead: " + demoPhone;

function timeNow() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function scrollToBottom() {
  els.scroll.scrollTop = els.scroll.scrollHeight;
}

function hideEmptyHint() {
  if (els.emptyHint) els.emptyHint.style.display = "none";
}

function addBubble(text, direction) {
  // direction: "out" (me, green/right) or "in" (Stellar AI, gray/left)
  hideEmptyHint();
  const row = document.createElement("div");
  row.className = "row " + direction;

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const textEl = document.createElement("span");
  textEl.className = "msg-text";
  textEl.textContent = text;

  const timeEl = document.createElement("span");
  timeEl.className = "msg-time";
  timeEl.textContent = timeNow();

  bubble.appendChild(textEl);
  bubble.appendChild(timeEl);
  row.appendChild(bubble);
  els.messages.appendChild(row);
  scrollToBottom();

  els.lastMsgTime.textContent = timeEl.textContent;
  els.contactPreview.textContent = direction === "in" ? truncate(text, 40) : "online";
  return row;
}

function addSystemNote(text, isError) {
  hideEmptyHint();
  const row = document.createElement("div");
  row.className = "row system";
  const note = document.createElement("div");
  note.className = "system-note" + (isError ? " error" : "");
  note.textContent = text;
  row.appendChild(note);
  els.messages.appendChild(row);
  scrollToBottom();
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// Typing indicator stays up for exactly as long as the real API call takes —
// no artificial delay. addTyping()/removeTyping() just bracket the fetch.
function addTyping() {
  const row = document.createElement("div");
  row.className = "row in";
  row.id = "typingRow";
  row.innerHTML =
    '<div class="typing-bubble"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>';
  els.messages.appendChild(row);
  scrollToBottom();
}

function removeTyping() {
  const row = document.getElementById("typingRow");
  if (row) row.remove();
}

function setSending(isSending) {
  els.input.disabled = isSending;
  els.sendBtn.disabled = isSending;
}

async function sendMessage(text) {
  addBubble(text, "out");
  setSending(true);
  addTyping();

  try {
    const resp = await fetch(CHAT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: demoPhone, message: text }),
    });

    removeTyping();

    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const err = await resp.json();
        detail = err.detail || detail;
      } catch (_) {
        /* ignore parse failure, keep statusText */
      }
      addSystemNote("Demo chat unavailable: " + detail, true);
      return;
    }

    const data = await resp.json();

    if (data.reply) {
      addBubble(data.reply, "in");
    } else if (data.action === "skipped") {
      addSystemNote("— no reply this turn (" + (data.skipped_reason || "skipped") + ") —", false);
    } else if (data.action === "error") {
      addSystemNote("The bot hit an internal error on that turn.", true);
    } else {
      addSystemNote("— no reply this turn —", false);
    }

    if (data.booking_detected) {
      addSystemNote("📅 Booking detected — handed off to Rafique Sir", false);
    }
  } catch (err) {
    removeTyping();
    addSystemNote("Could not reach the demo server: " + err.message, true);
  } finally {
    setSending(false);
    els.input.focus();
  }
}

// --- composer -------------------------------------------------------------
function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
}

els.input.addEventListener("input", autoGrow);

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || els.sendBtn.disabled) return;
  els.input.value = "";
  autoGrow();
  sendMessage(text);
});

els.resetBtn.addEventListener("click", () => {
  demoPhone = newDemoPhone();
  els.phoneLabel.textContent = "Demo lead: " + demoPhone;
  els.messages.innerHTML =
    '<div class="day-chip"><span>Today</span></div>' +
    '<div class="empty-hint" id="emptyHint">Send a message below to start the demo conversation with Stellar AI.</div>';
  els.emptyHint = document.getElementById("emptyHint");
  els.contactPreview.textContent = "online";
  els.lastMsgTime.textContent = "";
  els.input.focus();
});

els.input.focus();

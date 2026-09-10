"""Safe canned replies used when the guard blocks a draft it cannot fix.

Not one fixed string — a small pool per deflection mode (see
``app/services/conversation/deflection.py``). The engine passes the mode it
resolved from the blocked rules plus the recent bot text, and we pick a phrasing
that has not already been used in this conversation. Repetition is what makes a
deflect read as a script; variety is the whole point.
"""

from __future__ import annotations

import re

from app.config import Settings
from app.services.conversation.deflection import DeflectionPlan, select_deflection

# Phrasing pools keyed by deflection mode number. Each is a complete, compliant
# reply. `{name}` -> counsellor name, `{phone}` -> counsellor phone,
# `{address}` -> office address. Lines that reference a channel are only offered
# when the plan's resolved contact allows it (see `_contact_ok`).
#
# House rules baked into every line:
#  - the reply is from {{bot_name}} ("Stellar AI"); never "I'm the assistant/bot".
#  - direction of contact is always the LEAD reaching out to {name} — never
#    "he'll call you", "I'll set it up", "can I arrange a call".
_POOLS: dict[int, list[str]] = {
    1: [
        "That worry is a fair one, and it's more manageable than most people "
        "assume with the right university. This is where {name} really helps — "
        "worth talking it through with him directly.",
        "This is exactly the kind of thing {name} goes deep on. Worth putting it "
        "to him yourself when you get a chance.",
    ],
    2: [
        "Money questions deserve a proper conversation, not a text — {name} can "
        "look at your specific case and give you exact numbers. You can call him "
        "on {phone} whenever suits you.",
        "I'd rather you got the real figures from {name} directly than a vague "
        "answer from me. His number is {phone} — reach out any time.",
    ],
    3: [
        "I genuinely can't call this well without your full picture, and guessing "
        "would do you a disservice — that's the kind of thing a short chat with "
        "{name} sorts out.",
        "This really depends on your specific situation. {name} can give you a "
        "proper read; worth raising it with him directly.",
    ],
    4: [
        "That's a specific one — rather than give you a half-answer, {name} will "
        "have the accurate detail. Worth checking with him.",
        "I don't want to guess on that. {name} is the one who'd know for sure.",
    ],
    5: [
        "That's outside what we handle directly, so I don't want to guess. If "
        "it's tied to your MBBS plans, {name} can point you the right way.",
    ],
    6: [
        "I know I keep coming up short on this and I'm sorry — it's genuinely not "
        "something I can answer accurately. {name} can, directly: {phone}.",
        "I'm clearly not the one to get you a solid answer here, and I don't want "
        "to keep you going in circles. Call {name} directly on {phone}.",
    ],
    7: [
        "Fair — I'd want a straight answer too. On this I'd only be guessing, and "
        "you deserve better than that. {name} can give you the real answer on a "
        "call: {phone}.",
        "You're right to push. I can't give you an accurate answer on this one — "
        "{name} can, on {phone}.",
    ],
    8: [
        "Completely fair to ask. We're at {address} — you're welcome to walk in "
        "and see the setup before deciding anything.",
        "That's a fair question. Come see us at {address} — meeting the team in "
        "person tends to answer it better than anything I can type.",
    ],
    9: [
        "I hear you — this is your child, of course you're worried. Every parent "
        "we work with feels this, and it's the kind of thing that's better "
        "talked through with {name} directly than over messages.",
        "That concern is completely understandable. {name} talks parents through "
        "exactly this — worth speaking with him about it.",
    ],
    10: [
        "Fair thing to compare. I'd just check whether they work only with "
        "government institutes and whether the full cost is transparent upfront — "
        "those two questions separate most consultants. You're welcome to put the "
        "same to us.",
    ],
    11: [
        "I could send a document, but honestly it won't tell you what applies to "
        "you — and that's the part that matters. A short conversation with {name} "
        "does more.",
        "A generic pack won't answer what's specific to your case. {name} can, "
        "properly — worth a direct chat with him.",
    ],
    12: [
        "I'm not going to put a number on that without context — the honest "
        "answer really does depend on your case. {name} gives you the full "
        "picture properly.",
        "That's not something I can give you a figure for — it varies too much by "
        "situation. {name} covers it accurately with you directly.",
    ],
    13: [
        "Honestly, I don't know that one — and I'd rather say so than make "
        "something up. {name} will know.",
        "I don't want to guess at that. {name} is the one who can give you a "
        "proper answer.",
    ],
}

# nurture phase: never chase, no number push
_NURTURE = [
    "No rush at all. Whenever you want to talk it through, {name}'s number is "
    "{phone}.",
    "Whenever you're ready for a proper look at your options, you can reach "
    "{name} directly — just message here in the meantime.",
]

# --- Hindi / Hinglish pools -------------------------------------------------
# Used when the conversation has been in Hindi, so the safe-fallback doesn't
# switch the lead to English mid-thread. Roman Hindi (matches the common input);
# the bot is "she", so verbs are feminine ("sakti", "chahti", "samajhti").
_POOLS_HI: dict[int, list[str]] = {
    1: [
        "Ye chinta jayaz hai, aur sahi university choose karne par ye kaafi "
        "manageable ho jaata hai. Is baare mein {name} aapko theek se guide kar "
        "sakte hain — unse seedhe baat karna behtar rahega.",
    ],
    2: [
        "Paise se jude sawaal message par nahi, ek proper baat-cheet mein sahi "
        "hote hain. Aap {name} ko {phone} par call ya message kar sakte hain jab "
        "aapke liye theek ho.",
        "Main is par aapko sahi figure nahi de sakti — {name} aapke case ke "
        "hisaab se batayenge. Unka number {phone} hai.",
        "Paise se jude sawaal {name} aapke case ko dekh kar theek se batate hain "
        "— ye ek proper baat-cheet ki cheez hai, message par nahi.",
    ],
    3: [
        "Aapki poori profile jaane bina is par sahi salah dena theek nahi hoga. "
        "Iske liye {name} se ek chhoti si call sabse achhi rahegi.",
    ],
    4: [
        "Ye thoda specific hai — adhoori jaankari dene se behtar hai ki {name} "
        "aapko sahi detail batayen. Unse pooch lena theek rahega.",
    ],
    5: [
        "Ye hamare kaam ke daayre se bahar hai, isliye main andaaza nahi lagana "
        "chahti. Agar ye aapke MBBS plan se juda hai to {name} sahi disha bata "
        "denge.",
    ],
    6: [
        "Mujhe pata hai main is par baar-baar reh gayi hoon, iske liye maafi — "
        "ye sach mein aisi cheez hai jiska theek jawab main nahi de sakti. {name} "
        "de sakte hain, seedhe: {phone}.",
        "Main is par aapki madad nahi kar pa rahi, iske liye maafi. Iska theek "
        "jawab {name} hi de payenge — unse seedhe baat kar lijiye.",
    ],
    7: [
        "Bilkul samajh sakti hoon — aapko seedha jawab chahiye. Is par main sirf "
        "andaaza laga rahi hoongi, aur aap usse behtar ke haqdaar hain. {name} "
        "aapko sahi jawab de sakte hain: {phone}.",
        "Bilkul samajh sakti hoon — aapko seedha jawab chahiye. Is par sahi jawab "
        "{name} hi de payenge, main sirf andaaza laga rahi hoongi.",
    ],
    8: [
        "Poochhna bilkul jayaz hai. Hum {address} par hain — aap aakar khud setup "
        "dekh sakte hain, faisla baad mein kijiye.",
    ],
    9: [
        "Main samajhti hoon — ye aapka bachcha hai, chinta hona swabhavik hai. "
        "Ise messages ke bajaye {name} se seedhe baat karna behtar rahega.",
    ],
    10: [
        "Compare karna theek hai. Bas ye dekh lijiye ki wo sirf government "
        "institutes ke saath kaam karte hain ya nahi, aur poora kharcha shuru se "
        "saaf batate hain ya nahi. Yahi do baatein zyada tar consultants ko alag "
        "karti hain.",
    ],
    11: [
        "Main document bhej sakti hoon, par sach mein wo ye nahi batayega ki aap "
        "par kya laagoo hota hai — aur yahi asli baat hai. {name} se ek chhoti "
        "baat-cheet zyada kaam ki hai.",
    ],
    12: [
        "Bina poori jaankari ke main is par kuch pakka nahi keh sakti — ye sach "
        "mein har case par depend karta hai. {name} aapko poori tasveer theek se "
        "samjha denge.",
    ],
    13: [
        "Sach kahoon to mujhe iska pata nahi — banane se behtar hai keh doon. "
        "{name} ko pata hoga.",
    ],
}
_NURTURE_HI = [
    "Koi jaldi nahi. Jab bhi is par baat karni ho, {name} ka number {phone} hai.",
    "Jab aap options par theek se dekhna chahein, {name} se seedhe baat kar "
    "sakti hain — tab tak yahan message kar sakte hain.",
]

_DEVANAGARI = re.compile("[ऀ-ॿ]")
_HINGLISH = re.compile(
    r"\b(hai|hain|kya|kyaa|aap|aapko|aapke|aapka|nahi|nahin|nhi|kaise|kaisa|kar|"
    r"karo|karoge|karna|karta|karti|karte|karenge|karungi|karunga|sakte|sakta|"
    r"sakti|hoon|hu|hun|ho|de|dete|dena|diya|hua|hui|mera|meri|mere|mujhe|kitna|"
    r"kitni|kharch|kharcha|chahta|chahti|chahiye|chahte|liye|bata|batao|bataye|"
    r"bataiye|batayenge|beta|bete|beti|bachche|bachcha|bahar|padh|padhai|theek|"
    r"acha|achha|haan|abhi|toh|rasta|raha|rahi|rahe|hum|humein|hamein|hamare|"
    r"seedhe|seedha|jaankari|milega|milegi|par|ke|ki|ko|se|aur|ya|main|mein|"
    r"namaste|namaskar|matlab|thoda|zyada|kam|sab|saari|wala|wale)\b",
    re.IGNORECASE,
)


def is_hindi(text: str) -> bool:
    """True when the conversation has been in Hindi / Hinglish."""
    if not text:
        return False
    if _DEVANAGARI.search(text):
        return True
    markers = len(set(m.group(0).lower() for m in _HINGLISH.finditer(text)))
    return markers >= 3


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


def _contact_ok(line: str, plan: DeflectionPlan) -> bool:
    wants_phone = "{phone}" in line
    wants_address = "{address}" in line
    if wants_phone and plan.contact not in ("number",):
        return False
    if wants_address and plan.contact not in ("address", "address_brief"):
        return False
    return True


def _fill(line: str, settings: Settings) -> str:
    name = settings.counselor_name.strip() or "the director"
    return (
        line.replace("{name}", name)
        .replace("{phone}", settings.counselor_phone.strip() or "his direct line")
        .replace("{address}", settings.office_address.strip() or "our office")
    )


def safe_fallback_message(
    settings: Settings,
    *,
    engagement_phase: str = "push",
    plan: DeflectionPlan | None = None,
    blocked_rules: list[str] | None = None,
    prior_bot_text: str = "",
    conversation_text: str = "",
) -> str:
    """Pick a compliant fallback in the right deflection register.

    ``plan`` is normally resolved by the engine; if absent it is derived from
    ``blocked_rules``. ``prior_bot_text`` is the recent bot side of the
    conversation — any phrasing already used there is skipped so we don't repeat.
    ``conversation_text`` (lead + bot, recent) decides the language: a Hindi /
    Hinglish thread gets a Hindi fallback, not an English switch mid-conversation.
    """

    if plan is None:
        plan = select_deflection(
            topic_match=None,
            objection=None,
            guard_blocked_rules=blocked_rules or ["cost_missing_inclusion"],
        )

    hindi = is_hindi(conversation_text or prior_bot_text)
    mode = plan.mode.num if plan else 3
    if engagement_phase == "nurture":
        pool = _NURTURE_HI if hindi else _NURTURE
    elif hindi:
        pool = _POOLS_HI.get(mode) or _POOLS_HI[3]
    else:
        pool = _POOLS.get(mode, _POOLS[3])

    seen = _norm(prior_bot_text)
    candidates = [ln for ln in pool if plan is None or _contact_ok(ln, plan)]
    if not candidates:
        candidates = [
            re.sub(r"\s*(?:on |at |par |ko )?\{(?:phone|address)\}", "", ln)
            for ln in pool
        ]

    for line in candidates:
        filled = _fill(line, settings)
        if _norm(filled)[:60] not in seen:
            return filled

    # everything in the pool has been used — a plain, always-safe line
    name = settings.counselor_name.strip() or "the director"
    if hindi:
        return (
            f"Ye sach mein {name} ke liye hai mere bajaye — sahi jawab ke liye "
            "unse seedhe baat karna behtar rahega."
        )
    return (
        f"This is really one for {name} rather than me — worth raising it with him "
        "directly for an accurate answer."
    )

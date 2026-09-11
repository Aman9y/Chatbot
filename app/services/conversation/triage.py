"""Combined topic matrix + objection/stall triage (topic-matrix.md +
topic-matrix-2.md as ONE reference, plus sales-playbook Parts 3 & 5).

Deterministic keyword classification that injects the *handling level*
(FULL / PARTIAL / SOFT DEFLECT / HARD DEFLECT) and a one-line "how much" note
into the system prompt each turn. The LLM does the writing; the Response Guard
still backstops every non-negotiable regardless of what this picks.

Nothing here relaxes a guard rule. HARD-DEFLECT topics that appear anywhere in
the message (even as the secondary intent of a combo) are surfaced so the reply
hard-deflects that part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Handling = Literal["FULL", "PARTIAL", "SOFT_DEFLECT", "HARD_DEFLECT"]

_PRIORITY: dict[Handling, int] = {
    "HARD_DEFLECT": 3,
    "SOFT_DEFLECT": 2,
    "PARTIAL": 1,
    "FULL": 0,
}


@dataclass(frozen=True)
class TopicRule:
    id: str
    title: str
    handling: Handling
    keywords: tuple[str, ...]
    how_much: str


# --- the combined matrix -------------------------------------------------
# Order is only for readability; matching is by keyword-hit count with the more
# restrictive handling winning ties.
_MATRIX: tuple[TopicRule, ...] = (
    # ---- HARD DEFLECT (build-plan §2) --------------------------------
    TopicRule(
        "financing", "Loans / EMI / financing", "HARD_DEFLECT",
        ("loan", "emi", "instalment", "installment", "financ", "pay later",
         "pay in parts", "education loan", "student loan", "mudra", "kisht",
         "किश्त", "लोन", "byaj", "interest rate"),
        "Never confirm, deny or describe any loan/EMI option. 'Financing is "
        "something the counsellor goes through case by case' -> move to booking.",
    ),
    TopicRule(
        "scholarship", "Scholarships / financial assistance", "HARD_DEFLECT",
        ("scholarship", "financial aid", "fee waiver", "stipend", "grant",
         "concession", "free seat", "sponsored"),
        "Do not confirm or deny scholarships exist. 'Depends entirely on your "
        "situation, the counsellor walks through options honestly.' -> booking.",
    ),
    TopicRule(
        "payment_refund", "Payment schedule / refund / cancellation terms",
        "HARD_DEFLECT",
        ("refund", "refundable", "cancellation", "payment schedule", "payment plan",
         "instalment plan", "deposit back", "money back", "forfeit", "part payment",
         "when do i pay", "payment terms", "cancel and get"),
        "Contractual (build-plan §2 rule 7). State NO schedule, amount, %, or "
        "whether anything is refundable. 'The counsellor puts this in writing and "
        "explains it on the call.'",
    ),
    TopicRule(
        "premium_cost", "Premium-country cost (Germany/UK/US/etc.)", "HARD_DEFLECT",
        ("germany cost", "uk cost", "usa cost", "us cost", "cost for germany",
         "cost in uk", "germany fees", "uk fees", "us fees", "canada cost",
         "australia cost", "how much germany", "how much uk", "how much us"),
        "Never a number, range, 'roughly', 'ballpark', or a figure in words for a "
        "premium country. 'Varies by country and package; the counsellor gives "
        "exact numbers on the call.'",
    ),
    TopicRule(
        "consultancy_fee", "Our consultancy's charges", "HARD_DEFLECT",
        ("your charges", "your fees", "consultancy fee", "how much do you charge",
         "service charge", "what do you charge", "commission", "your cut"),
        "Default: 'Fees depend on what you need — discussed transparently on the "
        "call, no hidden numbers.' No figure.",
    ),
    TopicRule(
        "guarantee", "Admission / seat guarantees", "HARD_DEFLECT",
        ("guarantee", "guaranteed", "assured admission", "100%", "confirm my seat",
         "sure admission", "definitely get admission", "promise me"),
        "'No honest consultancy can guarantee admission before knowing your "
        "profile. What we can do is walk through your specific chances — that's "
        "the call.'",
    ),
    TopicRule(
        "pg_cost", "PG / MD-MS cost", "HARD_DEFLECT",
        ("pg cost", "pg fees", "md cost", "ms cost", "cost of pg", "pg kitna",
         "postgrad cost", "residency cost", "specialisation cost", "specialization cost"),
        "Never mention any PG cost figure (internal). 'PG plans are a call topic — "
        "depends where you want to specialise and which route fits your case.'",
    ),
    # ---- SOFT DEFLECT ------------------------------------------------
    TopicRule(
        "university_pick", "Which university to pick / university comparison",
        "SOFT_DEFLECT",
        ("which university", "which college", "best university", "best college",
         "recommend a university", "compare university", "university a or b",
         "which uni", "shortlist university", "rank the universit"),
        "Zero specific university endorsements or verdicts. General principle only "
        "(the NMC criteria). '20 mins with the counsellor and you'll have 2-3 real "
        "options mapped to your score/budget/country.'",
    ),
    TopicRule(
        "profile_assessment", "Personalised profile assessment", "SOFT_DEFLECT",
        ("assess my profile", "evaluate my profile", "my chances", "review my case",
         "what are my options", "where do i stand", "am i eligible for",
         "suggest for me", "guide me personally"),
        "Hot signal, not a brush-off. 'Can't properly assess a profile over chat — "
        "that's exactly what the call is for.' Then an assumptive close.",
    ),
    TopicRule(
        "special_case", "Special / unusual student case", "SOFT_DEFLECT",
        ("my case is different", "special case", "unusual situation", "gap of",
         "medical condition", "disability", "already studied", "backlog",
         "compartment", "failed 12th", "age limit"),
        "Acknowledge it's genuinely unique; don't attempt a generalised answer. "
        "'Worth looking at properly with the counsellor rather than guessing.'",
    ),
    # ---- PARTIAL --------------------------------------------------
    TopicRule(
        "fees_general", "Fees / budget (general)", "PARTIAL",
        ("fees", "fee", "cost", "how much", "budget", "expensive", "afford",
         "total cost", "package", "kitna", "kharcha", "price"),
        "Kazakhstan/Uzbekistan tier: the approved range only. India-vs-abroad: the "
        "approved comparison ranges. Everything else: no figure. Then 'the real "
        "number comes from matching your NEET score + budget to the right country "
        "on the call.'",
    ),
    TopicRule(
        "country_pick", "Country selection / which country is best", "PARTIAL",
        ("which country", "best country", "country is best", "recommend a country",
         "kaunsa desh", "where should i go", "country for mbbs"),
        "1-2 line neutral characterisation of a named country. Never a "
        "recommendation. 'Best depends on your score, budget and what matters most "
        "to you — that's what the call maps out.'",
    ),
    TopicRule(
        "country_compare", "Country comparison", "PARTIAL",
        ("russia or georgia", "georgia vs", "kazakhstan vs", "compare countries",
         "difference between countries", "x or y country"),
        "Neutral distinguishing factors between the named countries (cost tier, "
        "language, community size, safety profile). No verdict.",
    ),
    TopicRule(
        "private_india_compare", "Private MBBS India vs abroad", "PARTIAL",
        ("private college india", "private mbbs india", "deemed university",
         "management quota", "indian private", "private medical college"),
        "Use the approved cost-gap ranges only; flag that private India fees vary a "
        "lot by state/college — give the range, never a specific college's number.",
    ),
    TopicRule(
        "course_structure", "Course structure / curriculum", "PARTIAL",
        ("course structure", "curriculum", "subjects", "syllabus", "how many years",
         "semester", "duration of mbbs", "years of study"),
        "General shape (5 years + 1 internship, English medium, semester system, "
        "WHO/NMC-aligned). Subject-by-subject detail -> defer.",
    ),
    TopicRule(
        "clinical", "Clinical exposure / hospitals", "PARTIAL",
        ("clinical exposure", "hospital", "practical training in", "bedside",
         "patient exposure", "clinical rotation"),
        "Generic (affiliated/owned hospital, hands-on from ~year 3). Specific "
        "hospital comparisons -> the call.",
    ),
    TopicRule(
        "career_after", "Career after MBBS abroad", "PARTIAL",
        ("career after", "job after mbbs", "practice in india", "work after mbbs",
         "salary after", "scope after", "future after mbbs"),
        "General: practise in India after FMGE + internship, or continue abroad if "
        "licensed there. Salary/hospital-name specifics -> soft deflect.",
    ),
    TopicRule(
        "pg_path", "PG / specialisation path", "PARTIAL",
        ("pg", "post graduate", "postgraduate", "md ms", "md/ms", "next exam",
         "specialis", "specializ", "residency"),
        "Route only (NEET-PG in India, or PG abroad in the same country). NEVER the "
        "PG cost figure. If pushed on cost -> 'a call topic'.",
    ),
    TopicRule(
        "hostel", "Hostel / accommodation", "PARTIAL",
        ("hostel", "accommodation", "where will i stay", "room", "dormitory",
         "living arrangement", "pg accommodation"),
        "General (on-campus hostels standard, 2-3 sharing, Indian sections at most "
        "universities). Specific university -> defer.",
    ),
    TopicRule(
        "food_living", "Food & monthly living cost", "PARTIAL",
        ("indian food", "food there", "mess", "canteen", "living expenses",
         "monthly expense", "cost of living", "grocery", "khana"),
        "Indian mess/canteen usually available + Indian community — give freely. "
        "Do NOT state a monthly living-cost number (not confirmed) — 'the "
        "counsellor gives current figures per university'.",
    ),
    TopicRule(
        "part_time_work", "Part-time work", "PARTIAL",
        ("part time", "part-time", "work while studying", "earn while", "job while",
         "part time job", "student job"),
        "Honest shape only: any part-time work is limited, earnings modest, never a "
        "substitute for budget. Do NOT state an hours/week limit or earnings figure "
        "(not confirmed) — country rules -> the counsellor.",
    ),
    TopicRule(
        "gap_year", "Gap year / dropper", "PARTIAL",
        ("gap year", "dropper", "drop year", "second attempt", "third attempt",
         "reattempt", "2 year gap", "took a gap"),
        "General reassurance: gap years/droppers are common and fine. Specific edge "
        "cases (age limits, multiple-attempt scoring) -> confirm on the call.",
    ),
    TopicRule(
        "parents_concern", "Parent-voice concerns", "PARTIAL",
        ("my son", "my daughter", "my child", "as a parent", "beta", "beti",
         "worried about my", "is it safe for my"),
        "Director review: validate the concern genuinely — don't dismiss it, "
        "don't over-explain either — then position getting proper guidance "
        "from Rafique Sir as exactly how to avoid that risk. Answer briefly.",
    ),
    # ---- FULL --------------------------------------------------------
    TopicRule(
        "eligibility_neet", "Eligibility & NEET", "FULL",
        ("neet", "eligible", "eligibility", "qualifying", "cutoff", "cut off",
         "my score", "i scored", "i got", "marks", "percentile", "category",
         "obc", "general category"),
        "Short factual answer + extract their NEET score in the same message. "
        "Cutoff: state plainly only if asked, framed narrowly (below it, govt + "
        "abroad routes closed for the cycle; private India still open).",
    ),
    TopicRule(
        "nmc_recognition", "NMC recognition & Indian licensing", "FULL",
        ("nmc", "mci", "recognised", "recognized", "valid in india", "indian license",
         "practice in india", "wdoms", "who listed", "is the degree valid"),
        "Director review: when NMC or the admission process is actually asked "
        "about, give the FULL explanation — the 6 NMC criteria, WHO/WDOMS "
        "listing, all of it — never a partial answer. Big trust builder, don't "
        "rush it. But don't volunteer NMC criteria unprompted in replies where "
        "it wasn't asked about — smart, not constant.",
    ),
    TopicRule(
        "admission_process", "Admission process", "FULL",
        ("admission process", "how does admission", "how to apply", "application "
         "process", "steps to admission", "procedure", "how it works"),
        "The 8-step chain + '~20 days for a UG MBBS student applying abroad "
        "end to end — country/university selection, documentation, budget, "
        "tickets, end-to-end support'. Explaining it openly builds trust. No "
        "admission/intake guarantee.",
    ),
    TopicRule(
        "documents", "Documents & requirements", "FULL",
        ("documents", "papers", "certificates", "marksheet", "passport",
         "what do i need", "requirement", "paperwork"),
        "Standard checklist (10th/12th marksheet, passport, NEET scorecard, photos, "
        "birth cert). Country-specific extras -> mention they exist, defer detail.",
    ),
    TopicRule(
        "deadlines", "Application deadlines & intake", "FULL",
        ("deadline", "last date", "intake", "when to apply", "which month",
         "session start", "batch start", "admission close"),
        "Intake months + the ~20-day (UG-abroad) process. The urgency is real in "
        "the calendar — don't manufacture extra.",
    ),
    TopicRule(
        "language_exams", "Entrance exams & language", "FULL",
        ("ielts", "toefl", "language requirement", "entrance exam", "english medium",
         "do i need ielts", "language barrier", "which language"),
        "Most Indian-friendly countries teach in English, no IELTS for the "
        "Russia/CIS/Georgia route. Country-specific detail -> answer if certain, "
        "else defer.",
    ),
    TopicRule(
        "internship", "Internship & practical training", "FULL",
        ("internship", "housemanship", "clinical years", "final year training",
         "paid internship"),
        "Abroad internship is often better structured than India's — a fair place "
        "to be mildly promotional.",
    ),
    TopicRule(
        "fmge_next", "FMGE / NExT licensing exam", "FULL",
        ("fmge", "next exam", "screening test", "licensing exam", "mci screening",
         "pass rate", "how hard is fmge", "attempts", "clear the exam"),
        "Fear-defusing set-piece — never skip. 3 attempts/year now (was 2), 50% "
        "pass on 300, one-year paid internship after. Pass rate depends heavily on "
        "which university — which is why choosing right matters (= the call).",
    ),
    TopicRule(
        "visa", "Visa & immigration", "FULL",
        ("visa", "immigration", "student visa", "visa process", "visa rejection",
         "embassy", "consulate"),
        "Study-visa process, 'we handle documentation', standard timeline. "
        "Country-specific quirks -> say they exist, cover on the call.",
    ),
    TopicRule(
        "travel", "Travel & departure", "FULL",
        ("travel", "flight", "airport", "departure", "reach the university",
         "first time abroad", "group travel", "pickup"),
        "Group travel, airport pickup, first-time-abroad handholding — parents love "
        "this, give it fully and warmly.",
    ),
    TopicRule(
        "safety", "Safety & security", "FULL",
        ("safe", "safety", "security", "crime", "war", "dangerous", "girls safety",
         "is it safe", "political situation", "ragging"),
        "Honest about the country (crime, stability, safety for girls) + what "
        "universities do (secure campus, warden). NEVER guarantee — 'safer than "
        "students expect, here's why'. Honesty here builds trust, dishonesty gets "
        "you sued.",
    ),
    TopicRule(
        "student_life", "Indian student life / lifestyle abroad", "FULL",
        ("indian students", "indian community", "diwali", "holi", "festivals",
         "weekend", "sports", "city life", "weather", "climate", "culture",
         "homesick", "friends there", "seniors"),
        "Comforting community details freely (number of Indians, seniors' assoc, "
        "festivals, food). 'You won't be alone.'",
    ),
    TopicRule(
        "support_services", "Pre-departure / post-arrival / emergency support",
        "FULL",
        ("orientation", "pre departure", "pre-departure", "post arrival", "settling in",
         "buddy system", "helpline", "emergency", "medical emergency", "support system",
         "if something goes wrong", "local contact"),
        "Describe the structure honestly (orientation, airport pickup, senior/buddy "
        "system, helpline, hospital access) WITHOUT guaranteeing crisis outcomes. "
        "Major parent-trust category.",
    ),
    TopicRule(
        "transfer", "Transfer / migration between universities", "FULL",
        ("transfer to another", "change university", "migrate university",
         "switch university", "shift college mid"),
        "State clearly: switching mid-course generally forfeits the 'no transfer' "
        "condition needed for Indian recognition. Real, protective info.",
    ),
    TopicRule(
        "india_vs_abroad", "India MBBS vs MBBS abroad", "FULL",
        ("india vs abroad", "why abroad", "abroad or india", "instead of india",
         "cheaper than india", "compared to india", "vs private college"),
        "Core value proposition. Use the approved cost-gap ranges + honest FMGE "
        "path. Confident — this is the pitch.",
    ),
    TopicRule(
        "agent_selection", "Choosing a consultant", "FULL",
        ("how to choose consultant", "good consultant", "which agent", "trust an agent",
         "reliable consultancy", "choosing an agency"),
        "General checklist (transparent fees, verifiable office, answers hard "
        "questions, no pressure). Never badmouth a named competitor — let them "
        "compare us against the checklist.",
    ),
    TopicRule(
        "scams", "Scams, red flags, fraud prevention", "FULL",
        ("scam", "fraud", "cheated", "red flag", "fake university", "duped",
         "get cheated", "how to avoid fraud", "trust issues"),
        "Trust-building set-piece — answer generously. Real red flags (no written "
        "agreement, pressure to pay before seeing documents, no verifiable "
        "office/counsellor, vague on NMC criteria).",
    ),
    TopicRule(
        "myths", "Common myths & misconceptions", "FULL",
        ("myth", "is it true that", "people say", "heard that", "misconception",
         "rumour", "rumor", "not valid rumour"),
        "Fear-defusing set-piece. Correct the myth directly and clearly, never "
        "condescending about the question.",
    ),
    TopicRule(
        "mistakes", "Mistakes students make", "FULL",
        ("common mistakes", "what goes wrong", "students regret", "biggest mistake",
         "what to avoid"),
        "Real generalised patterns (not verifying NMC criteria before paying, "
        "choosing on price alone, skipping the written agreement). Protective.",
    ),
    TopicRule(
        "counseling_process", "What the counselling call is", "FULL",
        ("what happens on the call", "what is the call about", "how long is the call",
         "is the call free", "who is on the call", "what will counsellor",
         "counselling session"),
        "Answer generously — this reduces booking friction. What happens, how long "
        "(~15 min), free, no obligation, who's on it.",
    ),
    TopicRule(
        "company_info", "Company / counsellor information", "FULL",
        ("who are you", "about your company", "your experience", "how many years",
         "your office", "where are you located", "counsellor name", "your team",
         "are you registered"),
        "Only real facts that are configured. If company/counsellor details are "
        "unset, stay generic ('our team', 'our counsellor', 'our office') — invent "
        "nothing, no years/numbers.",
    ),
    TopicRule(
        "social_proof", "Success stories / testimonials", "FULL",
        ("success stories", "testimonial", "reviews", "student experiences",
         "past students", "any proof", "results", "placements you did"),
        "Only real, provided material — never fabricate a name, story, quote or "
        "outcome. If none provided, stay generic ('many families have gone through "
        "this with us').",
    ),
    TopicRule(
        "decision_making", "Parent-student decision making", "FULL",
        ("parents disagree", "my dad wants", "my mom says", "family not agreeing",
         "convince my parents", "we can't decide"),
        "Validate that disagreement is normal for a decision this big; don't "
        "mediate it in chat. Offer a joint call with both.",
    ),
    TopicRule(
        "high_intent", "High-intent admission questions", "FULL",
        ("how do i pay the fee", "start the documents", "when can we begin",
         "ready to proceed", "want to enroll", "want to admit", "start process now",
         "how do we start", "sign up now", "book my seat"),
        "Late-funnel buying signal. Answer plainly and IMMEDIATELY offer to book a "
        "call/office visit regardless of message number. Match their energy.",
    ),
)

# HARD-DEFLECT topics that must be caught even as a combo's secondary intent.
_HARD_RULES = tuple(r for r in _MATRIX if r.handling == "HARD_DEFLECT")


def _compiled(phrase: str) -> re.Pattern[str]:
    # phrase match, letter boundaries on each end, whitespace between words flexible
    parts = [re.escape(w) for w in phrase.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<![a-z]){body}(?![a-z])", re.IGNORECASE)


_RULE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    r.id: tuple(_compiled(k) for k in r.keywords) for r in _MATRIX
}


@dataclass
class TopicMatch:
    rule: TopicRule
    hits: int
    hard_deflect_topics: list[TopicRule] = field(default_factory=list)
    high_intent: bool = False

    @property
    def handling(self) -> Handling:
        return self.rule.handling


def _rule_bonus(rule: TopicRule) -> int:
    # a direct hit on a non-negotiable topic, or on a late-funnel buying signal,
    # dominates over incidental keyword overlap with a softer category
    if rule.handling == "HARD_DEFLECT":
        return 5
    if rule.id == "high_intent":
        return 4
    return 0


def classify_topic(text: str) -> TopicMatch | None:
    if not text or not text.strip():
        return None
    scored: list[tuple[int, int, int, TopicRule]] = []
    for rule in _MATRIX:
        hits = sum(1 for p in _RULE_PATTERNS[rule.id] if p.search(text))
        if hits:
            scored.append(
                (hits + _rule_bonus(rule), _PRIORITY[rule.handling], hits, rule)
            )
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top = scored[0][3]

    hard = [r for *_, r in scored if r.handling == "HARD_DEFLECT" and r.id != top.id]
    high_intent = any(r.id == "high_intent" for *_, r in scored)
    return TopicMatch(
        rule=top,
        hits=scored[0][2],
        hard_deflect_topics=hard,
        high_intent=high_intent,
    )


# --- objections & stalls (sales-playbook Part 5 + Bucket E) ------------
@dataclass(frozen=True)
class Objection:
    id: str
    keywords: tuple[str, ...]
    script_hint: str


_OBJECTIONS: tuple[Objection, ...] = (
    Objection(
        "think_about_it",
        ("let me think", "need to think", "think about it", "get back to you",
         "i'll get back", "will let you know", "need some time"),
        "Do NOT push back. 'Totally fair, this is a big decision and it should "
        "involve family. The call is designed for exactly that — your parent can "
        "be on it, and the counsellor walks you both through it. Want me to set "
        "it up?' Then let them go; the re-engagement scheduler nudges later.",
    ),
    Objection(
        "talk_to_family",
        ("talk to my parents", "discuss with family", "ask my father", "ask my mother",
         "ask my dad", "ask my mom", "consult my family", "parents first"),
        "'Makes sense. Would it help to do the call with both of you together? "
        "That's the most common way — saves explaining everything twice.'",
    ),
    Objection(
        "legit",
        ("how do i know you", "are you legit", "are you genuine", "are you real",
         "is this genuine", "prove you are real", "verify you"),
        "'Fair to ask.' 1-2 lines of real, configured credibility only (invent "
        "nothing). 'Also why we offer the office visit — come see the setup "
        "before deciding anything.'",
    ),
    Objection(
        "cheaper_elsewhere",
        ("friend's consultant", "cheaper elsewhere", "someone offered", "another agent",
         "getting it cheaper", "less price", "other consultancy cheaper"),
        "Never defend on price. Pivot to what cheap costs: 'Honest thing to check "
        "with them — is the university NMC-recognised, does it meet all six "
        "criteria, and what if things go wrong mid-course? Happy to compare "
        "properly on a call, no pressure.'",
    ),
    Objection(
        "just_researching",
        ("just researching", "just exploring", "early stage", "just looking",
         "just gathering info", "just checking options", "still researching"),
        "Perfect fit for the low-commitment sell: 'Good approach — that's exactly "
        "what the 15-min call is best for: no pressure, just your options mapped "
        "out so your research has a baseline. Want to set that up?'",
    ),
    Objection(
        "scam_fear",
        ("what if we get scammed", "what if i get scammed", "scared of fraud",
         "afraid of being cheated", "how do i not get cheated", "fear of scam"),
        "'Legit fear. Which is why we do the initial call before you pay or "
        "commit to anything — you judge us first, decide after. Want to book?'",
    ),
    Objection(
        "send_everything",
        ("send me details", "send me the details", "send brochure", "send me brochure",
         "send me the brochure", "brochure", "list of universities", "university list",
         "list of colleges", "full details", "send everything", "share the pdf",
         "send pdf", "send me all information", "send me all the information",
         "email me the details", "send me info", "share details"),
        "Stall, not a request. Never send a mega-doc. 'I could send a PDF, but "
        "honestly it won't tell you what applies to YOU — that's what the 15-min "
        "call is for. Want me to set one up?'",
    ),
    Objection(
        "just_tell_me_here",
        ("just tell me on whatsapp", "just tell me here", "tell me over chat",
         "why can't you tell me here", "explain here only", "no call just tell"),
        "'Fair, but honest answer: on WhatsApp I can give general info, not what "
        "fits your case. That's why the call exists — free, no obligation.'",
    ),
    Objection(
        "not_interested",
        ("not interested", "no longer interested", "don't want to proceed",
         "changed my mind", "leave it", "not for me"),
        "Respect it, leave the door open: 'All good, no pressure. If things "
        "change or you'd like a proper look at options later, just message here "
        "anytime. All the best with NEET.' (Lead is scored LOW / cold.)",
    ),
)

_OBJECTION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    o.id: tuple(_compiled(k) for k in o.keywords) for o in _OBJECTIONS
}


def detect_objection(text: str) -> Objection | None:
    if not text:
        return None
    best: tuple[int, Objection] | None = None
    for obj in _OBJECTIONS:
        hits = sum(1 for p in _OBJECTION_PATTERNS[obj.id] if p.search(text))
        if hits and (best is None or hits > best[0]):
            best = (hits, obj)
    return best[1] if best else None

"""Reply playbook: what Claude does with every kind of lead reply, so each one moves toward a booked Zoom.

Goal: a booked meeting (Status Meeting). Every reply gets the next step that is most likely to get a booking:
give before you ask (send the audit we promised when they ask for it), answer what they asked in plain words,
then one clear call to action: the booking link.

intent            what they said                                what we send (automatically)                     Status       next touch
----------------  --------------------------------------------  -----------------------------------------------  -----------  ----------
wants_audit       "yes", "send it", "show me the audit/sample"  the audit itself (3 specific findings from their  Interested   3 days
                                                                site + 1 first move), then link to walk through
                                                                the full plan
ready_to_talk     "let's talk", "happy to chat", "call me"      short thanks + booking link (+ offer the audit    Interested   2 days
                                                                as prep)
time_proposed     "Tuesday 3pm works", "how about next week"    confirm + ask them to lock the slot on the link   Interested   1 day
                                                                (so the Zoom invite lands); ping Azeem
wants_details     "what exactly do you do / how does it work"   2-4 plain sentences on what we do for businesses  Interested   3 days
                                                                like theirs + offer the audit + booking link
price             "how much", "what does it cost"               no numbers invented: depends on scope (videos,    Interested   3 days
                                                                ad spend); free audit first, exact quote on call
says_booked       "booked for Tuesday", "done, see you then"    short thanks + "see you then"; loud ping so     Interested   2 days
                                                                Azeem confirms it in Calendly -> Meeting
not_now           "maybe later", "busy now", "next quarter"     no pressure, ask to check back, offer the audit   Later        30 days
                                                                whenever useful                                   (one check-in)
referral          "talk to X", "send to marketing@"             thanks + ask for intro / the right address; ping   Interested   4 days
                                                                Azeem so the referral can be emailed
not_interested    "no thanks", "remove me", "unsubscribe"       nothing. Never contacted again                    Not interested
sensitive         angry, complaint, legal/spam threats, odd     draft only (Gmail Drafts) + loud ping             Replied      -
other             anything unclear                              draft only + loud ping                            Replied      -

Follow-up replies (the lead writes again after our answer) go through the same playbook with the whole
conversation as context, up to MAX_AUTO_REPLIES per lead; after that it is held for a human.
Leads waiting on a booking get NUDGES[0] after their next-touch date, then NUDGES[1] four days later, then stop.
Booking check: Calendly's booking emails reach Azeem's main Gmail, not the outreach inbox this code reads, so
every email that carries the booking link also sends Azeem a "📅 CHECK: did X book?" ping. He checks and tells
Claude "X booked"; Claude sets Status Meeting.
Claude can also hand-write the next email for any lead: put the text in the lead's "Send next" field in Notion
and the next reply check sends it in the same email thread ("{link}" becomes the booking link).
"""

# intent -> (send automatically?, new Status, days until next touch or None, ping Azeem?)
PLAYBOOK = {
    "wants_audit":    (True,  "Interested",     3,    False),
    "ready_to_talk":  (True,  "Interested",     2,    False),
    "time_proposed":  (True,  "Interested",     1,    True),
    "wants_details":  (True,  "Interested",     3,    False),
    "price":          (True,  "Interested",     3,    False),
    "not_now":        (True,  "Later",          30,   False),
    "referral":       (True,  "Interested",     4,    True),
    "says_booked":    (True,  "Interested",     2,    True),
    "not_interested": (False, "Not interested", None, False),
    "sensitive":      (False, "Replied",        None, True),
    "other":          (False, "Replied",        None, True),
}
# legacy names the AI may still use
ALIASES = {"interested": "ready_to_talk", "question": "wants_details", "phone_call": "ready_to_talk",
           "call": "ready_to_talk", "audit": "wants_audit", "later": "not_now", "unsubscribe": "not_interested"}

MAX_AUTO_REPLIES = 5        # auto answers per lead before a human takes over
NUDGE_GAPS = (4,)           # days between nudge 1 and nudge 2

NUDGES = [
    ("Hi {first}, just making it easy: here's the link to grab a quick Zoom whenever suits you. "
     "I'll come with a couple of concrete video and ad ideas for {company}.\n\n{link}\n\n{sender}"),
    ("Hi {first}, last nudge from me. Happy to show you exactly how I'd turn one of your finished projects into "
     "a video ad that brings in enquiries. 20 minutes, no obligation:\n\n{link}\n\n"
     "If now isn't the right time, no problem at all.\n\n{sender}"),
]
LATER_CHECKIN = ("Hi {first}, checking back in as promised. Still happy to put together that free video audit "
                 "for {company}, or if it's easier, grab a quick Zoom here:\n\n{link}\n\n{sender}")


def normalize(intent):
    intent = (intent or "other").strip().lower().replace(" ", "_").replace("-", "_")
    intent = ALIASES.get(intent, intent)
    return intent if intent in PLAYBOOK else "other"


def plan(intent):
    send, status, days, ping = PLAYBOOK[normalize(intent)]
    return {"send": send, "status": status, "days": days, "ping": ping}


REPLY_PROMPT = """You handle replies for {sender}, who runs Monetize Studio: cinematic project/property videos
plus Meta ads that turn them into enquiries, for architecture, interiors, kitchen & bath, builders/remodelers
and real estate businesses. Your ONLY goal is to get this lead to book a Zoom call with {first} using this
booking link: {link}

Lead: {company} ({niche}, {city}, {country}). First name: {lead_first}
Our first email to them:
\"\"\"{ours}\"\"\"
Conversation so far (newest first, may be empty):
\"\"\"{history}\"\"\"
Their NEW message:
\"\"\"{theirs}\"\"\"
Text from their website (for the audit; may be messy or empty):
\"\"\"{site}\"\"\"

Classify the new message into exactly one intent and write {first}'s reply. JSON only, keys:
- "intent": one of wants_audit, ready_to_talk, time_proposed, says_booked, wants_details, price, not_now, referral,
  not_interested, sensitive, other.
  wants_audit = they said yes / send it / show me the audit or a sample (our email offered a free audit).
  ready_to_talk = they want a call or chat (incl. a phone call). time_proposed = they suggest a day/time.
  says_booked = they say they already booked a slot on the link.
  wants_details = they ask what we do or how it works. price = they ask about cost.
  not_now = interested later / busy now. referral = they point us to someone else.
  not_interested = no, stop, remove, unsubscribe. sensitive = angry, complaint, spam/legal threat,
  personal/odd requests, or anything you are unsure about. other = anything else.
- "summary": one short line, what they said.
- "draft": the reply email body. Rules for every reply: warm, human, plain words, no fluff, no
  "I hope this finds you well", no exclamation marks, short paragraphs, sign off as "{first}".
  Never invent prices, results, client names, guarantees, or facts that are not in the website text.
  Always end with the booking link {link} (except not_interested, where draft is "").
  wants_audit: deliver the audit now in the email (130-220 words): one line thanks, then
    "What's working" (1-2 specific points from their site), "Where enquiries are being lost" (2-3 specific,
    fixable points, e.g. no video, no enquiry form, no testimonials, outdated details; only what the site
    text supports), "What I'd do first" (one concrete video + Meta ad move for their area), then invite them
    to a short Zoom to walk through the full plan (ad angles, targeting, budget) with the link.
    Do NOT claim you reviewed their Instagram posts unless the conversation says so; it is an audit of how
    their work is presented online.
  ready_to_talk: 40-80 words, thank them, give the link, say they can pick Zoom or a phone call there.
  time_proposed: 40-70 words, say that time works for {first} if it's open, and ask them to lock it on the
    link so the invite lands in both calendars.
  says_booked: 25-50 words, thank them, say {first} is looking forward to it, link only for rescheduling.
  wants_details: 70-120 words, answer plainly (we film/edit cinematic walkthroughs and before/after reveals,
    turn them into Meta ads aimed at homeowners or buyers in their area, and manage the ads), offer the free
    audit, then the link.
  price: 60-110 words, honest: it depends on scope (number of videos, filming, ad budget), so {first} gives
    an exact quote on a short call after looking at their goals; offer the free audit first; link.
  not_now: 40-70 words, no pressure, say you'll check back in about a month, link if they want it sooner.
  referral: 40-80 words, thank them, ask them to introduce us or share the right person's email, link.
  sensitive / other: a careful, polite draft for a human to check.
"""

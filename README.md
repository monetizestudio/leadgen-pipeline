# Monetize Studio: Foreign Directory Lead Pipeline

Runs free on GitHub Actions. No laptop needed.

**What it does every day**
- **11 PM Karachi:** finds architecture, interiors, kitchen/bath, remodeling and real estate businesses in the
  UAE, UK, US, Canada and Australia (OpenStreetMap + their websites). It drops any without an email or
  WhatsApp, has Gemini (free) check they're medium-sized, English-speaking and a fit, writes a personal line,
  and queues 100 leads in Notion (Lead Gen HQ > Leads).
- **Each region's morning:** emails that region's queued leads at a slow, random pace (Australia 3:30 AM,
  UAE 10:30 AM, UK 1:30 PM, US/Canada 7:30 PM Karachi), plus the day-3 and day-7 follow-ups.
- **Every 2 hours:** checks Gmail for replies. A reply marks the lead **Replied** and pings your phone 🔥.
  Bounces are marked **Bounced**. "Not interested" is marked and never contacted again.
- **WhatsApp leads** (businesses with a WhatsApp button on their site) are queued with a ready message.
  In Notion, filter Leads by Channel = WhatsApp, Status = Queued, and send them through your WhatsApp bulk sender.
- Every run logs a row in **Daily Runs** (target, done, failed or partial, notes).

---

## One-time setup (about 30 minutes)

### 1. Separate Gmail for outreach
Make a new Gmail, e.g. `azeem.monetizestudio@gmail.com`. Don't use your main one: cold email can hurt a
Gmail's reputation.
- Turn on **2-Step Verification**: myaccount.google.com > Security.
- Create an **App password**: myaccount.google.com/apppasswords, name it "lead pipeline", copy the 16
  letters.
- Warm it up: for the first week set `SEND_CAP_PER_RUN = 10` in `pipeline/config.py`, then 25, then 45.

### 2. Gemini API key (free)
aistudio.google.com > **Get API key** > Create. Copy it.

### 3. Notion integration token
- notion.so/profile/integrations > **New integration**, name "Lead Pipeline", type Internal. Copy the
  **Internal Integration Secret**.
- Open **Lead Gen HQ** in Notion > `•••` > **Connections** > add "Lead Pipeline". This gives it access to
  the Leads and Daily Runs databases.

### 4. Phone alerts (ntfy, free)
Install **ntfy** (iPhone/Android). Tap **+** and subscribe to a random topic name only you know, e.g.
`azeem-leads-7f3k9`. That name is your `NTFY_TOPIC`.

### 5. GitHub
- Make a free account at github.com > **New repository**, name `leadgen-pipeline`, set to **Private**.
- **Add file > Upload files** > drag in everything from this folder (keep the `.github` folder; on phone,
  unzip first).
- **Settings > Secrets and variables > Actions > New repository secret** for each:

| Secret | Value |
|---|---|
| `NOTION_TOKEN` | Notion integration secret |
| `GEMINI_API_KEY` | Gemini key |
| `GMAIL_ADDRESS` | the outreach Gmail |
| `GMAIL_APP_PASSWORD` | the 16-letter app password |
| `NTFY_TOPIC` | your ntfy topic |
| `SENDER_ADDRESS` | business address line for the email footer, e.g. `Monetize Studio, Karachi, Pakistan` |

### 6. Day-1 test
- **Actions** tab > **Find leads (nightly)** > **Run workflow** > limit `5`. In about 10 minutes, 5 leads
  should appear in Notion and you get a phone alert. Read the messages; if you like them, it's good.
- **Send emails** > **Run workflow** > region of one of those leads, max `2`. Check the outreach Gmail's
  Sent folder.
- After that it runs by itself every day.

---

## Changing things
Everything is in `pipeline/config.py`: cities, niches, daily target, send speed, and message templates.
Edit it on GitHub (pencil icon) and the next run uses it.

## Limits to know
- OpenStreetMap is free but doesn't list every business. If a night finds fewer than 100, the Daily Runs
  notes say why ("checked X, no contact Y, not a fit Z"). The fix is to add more cities in `config.py`.
- Gemini's free tier has a per-minute limit. The pipeline pauses 7 seconds between checks to stay under it.
- GitHub sometimes starts scheduled runs 5–20 minutes late. That's normal.

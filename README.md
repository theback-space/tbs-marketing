# tbs-marketing: social media manager for The Back Space

A self-hosted system that **writes posts, publishes them on schedule, watches comments,
drafts replies, likes positive comments, and tracks metrics**. Every step has guardrails
built for healthcare businesses.

| What it does | How |
|---|---|
| Generates content | Claude writes drafts in your brand voice (`brand.toml`) for each platform |
| Publishes | Approved drafts go out immediately or at a scheduled time |
| Monitors | Every N minutes it pulls new comments and account/post metrics |
| Engages | Triage of each comment (sentiment, intent, PHI risk), drafted reply, likes |
| Reports | Dashboard with follower trends, top posts, and an AI performance summary |
| Audits | Every post, reply and like is logged with who did it (staff or automation) |

## Quick start (no accounts needed)

```bash
pip install -e '.[dev]'
python -m tbs_social --demo serve --monitor-every 1
# open http://127.0.0.1:8000
```

`--demo` uses a fake platform and rule-based AI so you can click through everything.

## Going live

1. `cp .env.example .env` and fill it in.
2. **Claude:** set `ANTHROPIC_API_KEY`.
3. **Facebook / Instagram:** create a Meta app, connect your Facebook Page and Instagram
   Business account, and generate a long-lived **Page access token** with these permissions:
   `pages_manage_posts, pages_read_engagement, pages_manage_engagement,
   pages_read_user_content, instagram_basic, instagram_content_publish,
   instagram_manage_comments`. Set `META_PAGE_ID`, `META_IG_USER_ID`, `META_PAGE_ACCESS_TOKEN`,
   then `TBS_PLATFORMS=facebook,instagram`.
4. Edit `brand.toml` (voice, services, banned phrases, disclaimer).
5. Run it:
   ```bash
   python -m tbs_social serve --monitor-every 15                 # dashboard + monitor
   # or headless, e.g. from cron every 15 min:
   python -m tbs_social monitor --once
   ```
   To expose the dashboard beyond localhost, set `TBS_DASHBOARD_PASSWORD` and put it behind HTTPS.

## Automation levels

- `TBS_AUTOMATION=approve` (default): nothing goes out without a staff click. Start here for
  a week or two and check the drafts.
- `TBS_AUTOMATION=assist`: **only** clearly positive praise with no health details is liked
  and thanked automatically, capped per cycle (`TBS_MAX_AUTO_LIKES`, `TBS_MAX_AUTO_REPLIES`).
  Questions, bookings, complaints, health details and possible emergencies always wait for staff.

## What it deliberately does not do

- **Auto-follow accounts or mass-like other people's posts.** Facebook, Instagram and
  LinkedIn don't offer this in their official APIs. Doing it through browser bots breaks
  their terms and gets business accounts restricted or banned. Growth comes from content
  and fast, good replies, and the system focuses on those.
- **Like comments on Instagram.** The Instagram API doesn't support this, so the system
  likes comments on Facebook only.
- **Post text-only to Instagram.** Instagram requires an image, so add an image URL when
  you approve the draft.

## Compliance modes

`TBS_COMPLIANCE=standard` (default) is for business and marketing accounts:
- Banned phrases (`brand.toml`) block a post from being approved.
- Replies are blocked if they would repeat someone's phone number, email or ID number in public.
- Complaints, sales/demo inquiries and support questions are flagged for staff.

### HIPAA mode

Set `TBS_COMPLIANCE=hipaa` only if an account talks directly to patients. It adds:
- Comments with health details or identifiers are flagged "needs staff", and nobody can like them.
- Replies that would confirm a patient relationship ("your appointment", "welcome back") are
  blocked, even when staff send them.
- Possible-emergency detection (red-flag symptoms, self-harm language).
- Phone numbers, emails, ID numbers and dates are removed before a comment is sent to Claude.
- Drafts that mention testimonials or patient stories are flagged for written authorization.

Before using HIPAA mode with real patients, sign a Business Associate Agreement with
Anthropic and have your privacy officer review the rules. These checks reduce risk; they
don't replace a practice's policies or legal review.

## Project layout

```
tbs_social/
  ai.py          Claude: post generation, comment triage, performance summary (+ offline demo AI)
  compliance.py  Banned-phrase and privacy checks, plus HIPAA-mode PHI checks
  engine.py      The monitoring cycle and every outbound action
  platforms/     mock, Facebook Page, Instagram Business (add LinkedIn etc. by subclassing Platform)
  web.py         Dashboard (FastAPI + Jinja templates)
  db.py          SQLite storage
  cli.py         serve | monitor | generate | report
tests/           pytest suite (runs offline)
```

Run tests with `pytest`.

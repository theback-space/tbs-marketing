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
   then add `facebook,instagram` to `TBS_PLATFORMS`.
4. **Google Business Profile** (reviews, Google posts, search and maps metrics):
   1. In Google Cloud, create a project and
      [request Business Profile API access](https://developers.google.com/my-business/content/prereqs).
      Approval usually takes a few days or more.
   2. Once approved, enable the *My Business Account Management*, *My Business Business
      Information*, *Business Profile Performance* and *Google My Business* APIs.
   3. Create an OAuth client (type: Web application) and add
      `https://developers.google.com/oauthplayground` as a redirect URI.
   4. In the [OAuth Playground](https://developers.google.com/oauthplayground), click the gear,
      tick *Use your own OAuth credentials*, authorize scope
      `https://www.googleapis.com/auth/business.manage` while signed in as a manager of the
      listing, and exchange the code for a refresh token.
   5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, then run
      `python -m tbs_social google-locations` and copy the printed `GBP_ACCOUNT_ID` and
      `GBP_LOCATION_ID` into `.env`.
   6. Add `google` to `TBS_PLATFORMS`, e.g. `TBS_PLATFORMS=facebook,instagram,google`.
5. Edit `brand.toml` (voice, services, banned phrases, disclaimer).
6. Run it:
   ```bash
   python -m tbs_social serve --monitor-every 15                 # dashboard + monitor
   # or headless, e.g. from cron every 15 min:
   python -m tbs_social monitor --once
   ```
   To expose the dashboard beyond localhost, set `TBS_DASHBOARD_PASSWORD` and put it behind HTTPS.

## Posting by hand until a platform is connected

Set `TBS_MANUAL_PLATFORMS=google` (or any platform not yet in `TBS_PLATFORMS`). Generate drafts
for it on the Content page, edit them, click **Copy text**, post them yourself, then click
**Mark as posted** so the activity log stays complete. Move the platform to `TBS_PLATFORMS`
once its credentials are set up and the app will post for you.

## Running on a server

```bash
cp .env.example .env    # fill in keys and set TBS_DASHBOARD_PASSWORD
docker compose up -d --build
```

The dashboard listens on 127.0.0.1:8000. Put an HTTPS reverse proxy (Caddy, nginx, or your
host's load balancer) in front of it before exposing it. The database lives in the
`social-data` volume.

## What each platform supports

| | Publish | Read | Reply | Like | Metrics |
|---|---|---|---|---|---|
| Facebook Page | Text or photo posts | Comments | Yes | Comments | Followers, reactions, comments, shares |
| Instagram Business | Photo posts (image required) | Comments | Yes | No | Followers, likes, comments |
| Google Business Profile | Updates on your listing (1,500 chars) | Reviews not yet answered | Yes | No | Rating, review count, search/maps views, calls, website clicks, directions, bookings (last 28 days) |

Google reviews arrive in the Inbox with their star rating. Reviews already answered (here or
on Google) are skipped. In `assist` mode, glowing reviews get a short thank-you automatically;
anything critical waits for staff.

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
  platforms/     mock, Facebook Page, Instagram Business, Google Business Profile
                 (add more by subclassing Platform)
  web.py         Dashboard (FastAPI + Jinja templates)
  db.py          SQLite storage
  cli.py         serve | monitor | generate | report
tests/           pytest suite (runs offline)
```

Run tests with `pytest`.

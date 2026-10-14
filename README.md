# MotorScope

A local website for tracking everyday mobility from smartwatch and phone data, with
real email authentication: saved accounts, verified email addresses, and password
resets delivered by email. Dark theme, bright orange type.

The purpose is to turn passive movement data into a mobility score people can follow
over time, so gradual changes in walking become visible enough to mention to a
clinician. **MotorScope is not a medical device and does not diagnose anything.**

This is currently the authentication layer only. Device syncing, score calculation,
and trend charts are not built yet.

Runs entirely on your machine. Nothing leaves the box except the emails you ask it
to send.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env      # then fill in your SMTP details
.\.venv\Scripts\python.exe run.py
```

Open <http://127.0.0.1:8000>.

The app still runs without SMTP configured. Pages load and accounts work; emails are
not delivered and the reset link is printed to the console instead.

## Configuring email

Password resets and verification links are sent over real SMTP, so you need
credentials for a mailbox. In `.env`:

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your-app-password
MAIL_FROM=you@gmail.com
```

| Provider | Host | Port |
| --- | --- | --- |
| Gmail | `smtp.gmail.com` | 587 |
| Outlook / Hotmail | `smtp-mail.outlook.com` | 587 |
| Yahoo | `smtp.mail.yahoo.com` | 587 |
| Any other | provider default | 465 or 587 |

Gmail requires an App Password rather than your account password: enable 2-Step
Verification at <https://myaccount.google.com/apppasswords>, then generate one.

Port 465 uses implicit SSL and 587 uses STARTTLS. Both are handled automatically;
`SMTP_SSL` and `SMTP_STARTTLS` override the default if your provider needs it.

`BASE_URL` must match the address you open in the browser, because that is what gets
built into the links inside the emails.

## Google sign-in

Google sign-in can create the account outright, so a user can go from nothing to a
confirmed, signed-in account without ever typing a password.

No Google API needs enabling. The `openid` / `email` / `profile` scopes are part of
Google's built-in OpenID Connect support, so there is nothing to turn on in
*APIs & Services > Library*.

**Get credentials:**

1. Open <https://console.cloud.google.com/apis/credentials> and pick or create a project.
2. Go to the **Google Auth Platform** section and complete each page:
   - **Branding** — app name `MotorScope`, your support email, developer contact.
   - **Audience** — choose `External` and leave the publishing status as
     **Testing**. You do not need to publish, and you do not need to list test
     users: Google exempts apps that request only name/email/profile from the
     test-user restriction and from the 7-day authorization expiry.
   - **Data Access** — the default `openid` / `email` / `profile` scopes are
     already present. Add nothing.
3. **Clients > Create client** — application type **Web application**.
4. Authorized redirect URI — must match character for character:
   `http://127.0.0.1:8000/auth/google/callback`
5. Authorized JavaScript origin: `http://127.0.0.1:8000`
6. Copy the client ID and client secret into `.env`:

```ini
GOOGLE_CLIENT_ID=123456789-abcdef.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
```

> **The client secret is displayed exactly once.** Google hashes secrets at rest,
> so it can never be viewed again after the creation dialog closes. If you lose
> it, create a new client and delete the old one.

Leave both blank and the Google button is hidden everywhere.

The redirect URI is built from `BASE_URL`, not from the port the server happens to
run on. If you launch on a different port with `--port 8010`, also set
`BASE_URL=http://127.0.0.1:8010` or Google will reject the callback with
`redirect_uri_mismatch`. Google exempts `localhost` and `127.0.0.1` from its
HTTPS-only rule, so the loopback URL above is accepted as-is.

**How accounts resolve.** MotorScope only trusts an address Google reports as
`email_verified: true`. Given that:

| Situation | Result |
| --- | --- |
| No account for that address | One is created, already verified, no password |
| Google subject already linked | Signs straight in |
| Existing **verified** password account | Google is linked to it, password kept |
| Existing **unverified** account | Refused — the person must confirm or reset first |

That last row is deliberate. Someone who signed up with your address but never
confirmed it has chosen their own password. Letting a Google sign-in attach to that
account would hand them access as well, so MotorScope makes the real owner confirm
the address first.

Google accounts have no password, so password sign-in is refused for them with a
pointer to the Google button. Using *forgot password* sets one, after which either
method works.

**Permissions requested:** `openid`, `email`, `profile`. Enough to identify the user.
MotorScope cannot read Gmail.

**Security measures:** authorization code flow with PKCE (S256), a signed
single-use `state` cookie, `HttpOnly` + `SameSite=Lax`, and a 10-minute cap on the
round trip. Disabling CSRF or reusing a stale `state` shows an error page and creates
nothing.

## How accounts behave

- **One account per email address.** Email is a unique column in the database.
  Signing up again with the same address is refused, so an account can never be
  duplicated, overwritten, or hijacked by re-registering it with a different
  password. The only way to change the password is the emailed reset.
- **Accounts persist.** Users live in `data/motorscope.db` and survive restarts.
- **Email must be confirmed.** A signup is inactive until the emailed link is opened.
  Login attempts before that are refused.
- **Password reset by email.** The link is single-use and expires in 30 minutes.
  Using it invalidates the old password immediately. Requesting a second reset
  invalidates the first.
- **No account enumeration.** The forgot-password page returns the same confirmation
  whether or not the address is registered, so it cannot be used to discover which
  emails have accounts.

## Security notes

- Passwords are hashed with PBKDF2-HMAC-SHA256 at 600,000 iterations, never stored.
- Reset and verification tokens are generated with `secrets`, and only their SHA-256
  digests are stored, so a copy of the database cannot be replayed.
- Sessions are signed with a key in `data/secret.key` and expire after 12 hours.
- Every POST is protected by a double-submit CSRF token, and cookies are `HttpOnly`
  and `SameSite=Lax`.
- Five failed sign-ins lock an address for five minutes. Unknown emails are checked
  against a dummy hash so they take the same time as real ones.
- Post-login redirects are restricted to local paths.

## Tests

```powershell
.\.venv\Scripts\python.exe -m tests.test_auth_flow
.\.venv\Scripts\python.exe -m tests.test_google_auth
.\.venv\Scripts\python.exe -m tests.test_migration
```

Covers signup, duplicate rejection, verification, login, lockout, session handling,
password reset, CSRF, the full Google round trip with its edge cases, and the schema
migration. Google's endpoints are stubbed and a temporary database is used, so no
email is sent, no Google call is made, and your real accounts are untouched.

To inspect the reset email itself:

```powershell
.\.venv\Scripts\python.exe -m tests.preview_email
```

Writes `preview/reset_email.html` and `preview/reset_email.eml` to disk.

## Layout

```
app/
  main.py            app wiring, error pages, CSRF cookie
  config.py          .env loading and settings
  db.py              SQLite schema, queries, migrations
  security.py        password hashing, tokens, signed sessions
  google_auth.py     Google OAuth 2.0 + PKCE
  mailer.py          SMTP delivery and email templates
  deps.py            current-user and CSRF helpers
  routers/
    auth.py          signup, verification, login, reset, Google
    pages.py         landing, dashboard, account
templates/           server-rendered pages
static/css/style.css dark theme
tests/               auth, Google, migration, email preview
data/                SQLite database and signing key (gitignored)
```

## Adding features

The dashboard is an empty state waiting on device data. A sensible order:

1. `devices` and `readings` tables in `SCHEMA` in `app/db.py`, each with a `user_id`
   foreign key back to `users`. Ownership is enforced in the router, not the template.
2. An ingestion endpoint for wearable and phone exports, writing raw readings.
3. A scoring module that turns readings into gait speed, step length, symmetry,
   stability, and consistency figures.
4. A `GET /api/mobility` endpoint returning the score and its trend for the signed-in
   user, then render the chart in `templates/dashboard.html`.

## Scope note

Health-adjacent software carries real obligations. Keep the "not a medical device"
disclaimer visible, avoid language that promises to detect or rule out a condition,
and treat any diagnostic framing as a product decision that needs clinical and
regulatory input rather than a copy change.


# DUO FNCS Awards 2026 — isolated draft

All awards files live in this directory on `duo-fncs-awards-2026-draft`. Do not merge or deploy the repository root to launch this app. Answer North pages, CNAME, workflows and domain settings are untouched.

## Run the interactive demo

Requires Python 3.12+. No third-party packages.

```sh
cd awards-2026
python3 server.py --demo --host 127.0.0.1 --port 4173
```

Open http://127.0.0.1:4173. Demo code: `DEMO-2026-PREVIEW-CODE`. It accepts one demo ballot. Demo mode uses `.runtime/demo.sqlite3`; real mode uses a separate database. To repeat a local demo, stop the server and remove only `.runtime/demo.sqlite3`. Never put real voters in a demo database.

```sh
python3 -m unittest discover -s tests -v
```

## Implemented

- Random 256-bit access codes, stored as SHA-256 hashes. One unique approved member ID per roster row. Codes are bearer credentials, not proof of identity: an independent administrator must verify each member and deliver one code privately. Sharing or theft of codes remains a risk. OAuth is not implemented.
- Atomic SQLite transaction consumes the code, stores the ballot, and appends an audit entry. Concurrent duplicate submissions accept at most one ballot. Website and signed Forms adapter use the same transaction.
- Strict category/nominee checks; explicit abstention and publication consent; configured open/close times; real voting closed by default.
- Public API exposes only display names of accepted voters. Ballots do not contain member IDs; the voter table does not store receipt IDs. This is not cryptographic anonymity: database access, submission timing, logs outside the app, or Google Forms records can reveal associations.
- Ballot receipt generated independently of selections. No public results, ballot lookup, or administrative write API.
- Same-origin POST checks, JSON/body-size limits, fixed static-file allowlist, CSP, no-store responses, no request logging, and persisted request throttling.
- Local hash-chained audit entries detect edits against a separately retained checkpoint. A database administrator can rewrite the database AND chain; the chain alone does not establish independent certification or prevent owner manipulation.

## Real launch prerequisites — not completed

1. Independent nomination committee supplies the final category IDs, nominee IDs, voter eligibility rules, tie rules, voting window and roster. Names must be approved for public display.
2. Independent custodian hosts this app on a separate HTTPS origin and durable private disk; do not use the Answer North domain or GitHub Pages. Python's built-in HTTP server is for this draft, not a hardened public edge. Add TLS termination, request timeouts, connection limits and edge rate limits. Current address throttle uses the immediate socket peer (does not trust X-Forwarded-For); configure proxy handling before launch so all voters do not share one bucket.
3. Set `AWARDS_DB` to a private persistent path outside the web root, `AWARDS_ORIGIN` to the exact HTTPS origin, and optionally `AWARDS_FORMS_SECRET` through the hosting secret store. Do not commit secrets, voter databases, response sheets, or codes. Restrict backups and operating-system access to the independent custodian.
4. Issue codes only after independent membership verification:
   `python3 server.py --issue MEMBER_ID 'Approved public name'`
   The command displays the code once for private delivery. Do not record it in application logs or shared terminals. Duplicate member IDs are rejected; code rotation/revocation is not implemented.
5. Populate `election.json` with final categories and Unix-second `opens_at`/`closes_at`. Keep `open:false` until independently reviewed. Restart after configuration changes. No real nominees or dates have been invented.
6. Configure Google Forms separately if used. Its submit trigger must map answers into the payload below. Keep response records private: Forms can retain access codes alongside choices, so independent ownership and strict sharing are essential. A Forms confirmation is only receipt of a form response, not acceptance of a ballot by this service. Invalid/duplicate responses must remain excluded. Google account sign-in alone does not establish eligibility.
7. Independently test the actual deployed browser + Forms flow, duplicate submissions across both channels, closed window, privacy, outages, restore, and rollback. Retain external audit checkpoints and certify the tally independently. Audit export, tally certification, managed authentication and production hosting remain pending.

## Google Forms adapter contract (implemented endpoint, not connected)

POST `/api/forms`, Content-Type `application/json`:

```json
{"token":"privately-issued-member-code","choices":{"category-id":"nominee-id"},"consent":true}
```

The trusted server-side adapter signs the exact UTF-8 request bytes:

- `X-Awards-Timestamp`: Unix seconds, accepted within 300 seconds of server time.
- `X-Awards-Signature`: lowercase hex HMAC-SHA256 with `AWARDS_FORMS_SECRET` over `timestamp + '.' + rawJSON`.

Return 201 means accepted; 409 means the member already submitted; 400/401/403 means not accepted; 429 means throttled; 503 means the adapter is disabled. Retrying an accepted payload cannot add a second vote. Do not send raw email addresses or client-supplied display names: names come only from the approved roster. The secret stays in the server-side adapter, never in a form field or frontend script.

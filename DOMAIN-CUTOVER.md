# answernorth.com cutover runbook

**Current state (verified 2026-08-26):** apex A records point at Porkbun parking
(207.207.210.23/36/50), `www` CNAMEs to the apex, and MX is Google Workspace
(`smtp.google.com`) — mail records must NOT be touched.

## Owner step — Porkbun DNS (the only manual step)

Delete the three parking A records on `@` and the existing `www` record.
Leave every MX / TXT (SPF, DKIM, DMARC) record exactly as it is. Then add:

| Type  | Host | Answer / Value      | TTL |
|-------|------|---------------------|-----|
| A     | (blank / @) | 185.199.108.153 | 600 |
| A     | (blank / @) | 185.199.109.153 | 600 |
| A     | (blank / @) | 185.199.110.153 | 600 |
| A     | (blank / @) | 185.199.111.153 | 600 |
| CNAME | www  | vz4e.github.io      | 600 |

(Optional IPv6, same host `@`: AAAA 2606:50c0:8000::153, :8001::153,
:8002::153, :8003::153.)

## After DNS is set (Claude runs these — say "DNS is done")

1. Verify resolution: `Resolve-DnsName answernorth.com -Type A` shows the four
   185.199.x IPs; `www` resolves via vz4e.github.io.
2. Bind the domain: commit a `CNAME` file containing `answernorth.com` to the
   repo root AND `gh api repos/VZ4E/astraq-fund/pages -X PUT -f cname=answernorth.com`.
3. Poll `gh api repos/VZ4E/astraq-fund/pages` until `protected_domain_state`
   / domain check passes, then `-F https_enforced=true` (cert issuance can take
   up to ~1 hour).
4. Verify `https://answernorth.com/`, `https://www.answernorth.com/` (GitHub
   redirects www→apex when apex is the bound domain), `/fund/`, `/research/`,
   `/404` behavior, and favicon/asset paths.
5. Login check under the new origin: Supabase auth works from any origin for
   password sign-in; for email links, add `https://answernorth.com/fund/` to
   Auth → URL Configuration → Redirect URLs and set Site URL to
   `https://answernorth.com` in the Supabase dashboard (not exposed via MCP —
   owner or dashboard session).
6. `https://vz4e.github.io/astraq-fund/` will 301 to answernorth.com afterward —
   old links keep working.

## Why the CNAME file is not committed yet
Binding the custom domain before DNS resolves makes GitHub redirect the
github.io URL to a dead domain — a self-inflicted outage. The bind happens
immediately after DNS, not before.

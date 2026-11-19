# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This repo is **pre-implementation**. It contains only `context.txt`, a Spanish-language requirements document (user stories `HU-xx`, functional requirements `RFxx`, non-functional requirements `RNFxx`). There is no source code, package manifest, build, lint, or test tooling yet, and the directory is not a git repository. The tech stack has not been chosen — do not assume one; ask or confirm before scaffolding. When a stack is picked, replace this section with real build/test/run commands (including how to run a single test).

`context.txt` is the source of truth for scope. Cite requirement IDs (e.g. `RF15`, `RNF06`) in code comments, tests, and commit messages when implementing them. Note the numbering has gaps (HU-09/13/17/22/23 and RF13/RF21/RF22 are absent) and several RFs are repeated under multiple user stories; the gaps are in the source document, not lost work.

## Product

**Mibers** is an internal tool for a social-media agency. Staff manage *clientes* (the agency's customers), prepare *piezas* (post, carrusel, reel, historia, portada), get them approved, schedule them, auto-publish to the client's Instagram/Facebook, and send the content to the client's WhatsApp. This repo is the API/backend. There is no public registration; users are only the agency's own staff.

## Architecture-shaping requirements

These span several requirements and are easy to miss when working on one feature in isolation.

**Roles (RF3, RNF05).** Only two profiles: `Administrador` and `Personal`. Admin-only actions: approve/request changes on piezas, link social accounts, manage staff accounts, read the audit log. Permissions must never depend on job title/cargo, and onboarding a new collaborator must need no code change.

**Pieza lifecycle (RF10, RF12).** States: Borrador → En revisión → Cambios solicitados → Aprobada → Programada → Publicada | Fallida → Compartida. Staff submits for review; only Admin approves or requests changes (with a comment). **Editing the files or copy of an Aprobada pieza sends it back to En revisión** — this invariant must hold for every write path.

**Pieza data (RF8, RF9, RF11, RF14).** Required: client, type, target networks, date/time, files, copy. Optional: hashtags, links, campaign, owner, client message. Copy can be shared across networks or overridden per network. An editable source file can be attached to each pieza. Uploaded media must be validated (format, aspect ratio, duration, size) against the **target network's** rules, and 100% of invalid files must be rejected *before* scheduling (RNF08).

**Publishing pipeline (RF15–RF19, RNF06, RNF09, RNF12).**
- A scheduler publishes Aprobada piezas at their scheduled time using each network's own copy. An unapproved pieza must be **blocked and raise an alert**, never published.
- All schedule times are Colombia time (`America/Bogota`, UTC-5, no DST). 95%+ of publications and sends must land within ±5 min.
- Record the result per network: published (with link) or failed (with reason); failed ones must be retryable (RF17).
- WhatsApp delivery to the client's contacts happens on publication day at the scheduled time and **only after publication is confirmed on at least one network**; record whether it was delivered (RF18).
- Admin receives a personal WhatsApp alert within 5 min when: a publication fails, is blocked for lack of approval, an account token expires/gets unlinked, or a client send fails (RF19, RNF12).
- Hard safety rule: 0 publications outside the client's own linked accounts. The scheduling confirmation must show client, target accounts, and per-network copy (RNF09).

**Integrations (RF7, RF16, RNF10).** Only the official Meta APIs (Instagram, Facebook) and the official WhatsApp API — no unofficial automation or scraping. Client accounts are linked via Meta's OAuth authorization (admin only); client passwords are never stored. Each link has a status: `vinculada`, `expirada`, or `desvinculada`. TikTok, LinkedIn and X are explicitly out of scope. Metrics (RF23) are whatever each API returns — reach, views, interactions, followers per client/network/month — with no time-of-day analysis and no report export.

**Auth and security (RF1–RF2, RNF01, RNF02).**
- Username + password login only. Admin resets a user's password to a temporary one (no email required); the user must change it on first login; changing it later requires the current password.
- Session expires after 30 min of inactivity; 5 failed logins lock the account for 15 min; every internal route requires a session.
- Passwords hashed with Argon2 or bcrypt; network authorizations (tokens) and contact data encrypted at rest with AES-256; TLS 1.2+.

**Audit log (RF4, RNF11).** Record user, action, timestamp for: logins, pieza changes, approvals, scheduling, publication results, WhatsApp sends, network linking, account management. Append-only — no user can edit it — admin-read-only, retained 12 months.

**Data retention and legal (RF5, RNF13).** Deactivating a client or staff account keeps its history (clients for one year); staff deactivation must not lose their prior work. Must comply with Colombian Ley 1581 de 2012 and Decreto 1377 de 2013 (published data-treatment policy and holder authorization).

**Non-functional targets worth designing for.** 95% of screens load in <3 s (RNF07); test coverage ≥60% on scheduling and publishing code (RNF14); UI target is desktop ≥1366 px, usable down to 360 px, on the last 2 versions of Chrome/Edge/Firefox (RNF04) — relevant if a frontend lives in or alongside this repo.

## Conventions

Requirements, user stories and product terminology are in Spanish; keep domain terms (`cliente`, `pieza`, `Aprobada`, `Administrador`/`Personal`, etc.) consistent with `context.txt` when naming API resources, states, and roles.

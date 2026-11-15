# The Installed Agent — WorkMeta AI

> One MV3 codebase. A client gets the same agent with its own name, its own
> API address and its own trained domains. The agent asks the API what it may
> do; it never decides for itself.

This document describes the agent that runs **in the browser**.

---

## 1. What the agent is

A Chrome/Edge extension (Manifest V3) with three parts:

| Part | What it does |
|---|---|
| `src/background/` | The service worker. It holds the session token and is the **only** part that talks to the API. |
| `src/content/` | A content script that runs only on the client's trained domains. It reports which page is open and returns visible text **only when the user asks**. |
| `src/sidepanel/` | The assistant panel: sign in, see which page is open, sign out. |

There is one codebase. Packaging rewrites the manifest and the configuration
for a client; the code is identical.

---

## 2. The rules it keeps

These are enforced in code, not in the interface.

### The token never reaches a page

The session token lives in `chrome.storage.local` and is used only by the
service worker. A web page cannot read it: pages reach the agent only
through the side panel, and the worker answers only the messages it
understands.

### The agent reads only the client's own systems

The content script is registered for the client's trained domains and for
nothing else. The worker then checks the address again, in
`src/lib/domains.ts`, before it uses one. A page on any other domain is
never read — not its address, not its text.

There is no `tabs` permission and no "all sites" host permission, so the
agent cannot see where the user has been.

### The page is untrusted data

The visible text of a page is treated as data, never as instructions. Text on
a screen can say "ignore your instructions"; the agent reads it as something
to display, never as something to obey.

### The package is checked before it is used

The packager signs the agent's configuration with the deployment's Ed25519
key. The agent checks that signature in the browser with WebCrypto before it
uses the configuration, and shows the key's fingerprint so an administrator
can compare it with the one the WorkMeta console shows.

---

## 3. How it is used

1. The client administrator packages the agent for their organisation.
2. Staff install the ZIP (Chrome → *Load unpacked*, or the enterprise
   policy) and sign in with their work account.
3. The panel shows who is signed in and which of the client's pages the
   agent is currently allowed to read.

---

## 4. Building it

```powershell
# From the repository root
npm.cmd install
npm.cmd run build:extension     # -> apps/extension/dist
npm.cmd run typecheck:extension
npm.cmd run test:extension
```

`dist/` can be loaded straight into Chrome with *Load unpacked*. It carries
a development configuration pointing at `http://127.0.0.1:8000`.

---

## 5. Packaging it for a client

```powershell
apps\api\.venv\Scripts\python tools\scripts\pack_extension.py `
    --client-name "Example Rwanda Ltd" `
    --api-origin https://api.example.com `
    --org-id 0f6c1b2e-... `
    --domain ebm.example.com `
    --domain irembo.example.com
```

The packager writes `var/packages/<client>-<version>.zip` and prints what
was written:

```
Packaged the agent for Example Rwanda Ltd.
  Domains it may read: ebm.example.com, irembo.example.com
  Configuration signed: yes
  Signing key fingerprint: 302a300506032b65
```

The signing key comes from `EXTENSION_SIGNING_PRIVATE_KEY` in the
repository `.env`. Without one the package is produced **unsigned**, and the
agent says the configuration was not verified. It never pretends an unsigned
package is signed.

The package contains the agent, the client's manifest, the configuration and
its signature. It contains **no secret**: a stolen package is useless without
a valid user login for that client.

---

## 6. What the agent needs from the API

| Call | Why |
|---|---|
| `POST /auth/login` with `kind: "extension"` | Sign in. Returns a bearer token instead of a cookie. |
| `GET /auth/me` | Check the stored session is still valid. |
| `POST /auth/switch-organization` | Change the organisation the agent acts for. |
| `POST /auth/logout` | Revoke the session on the server. |

Every call is authorised by the server against the user's membership in the
organization, so the agent cannot reach another client's data.

---

## 7. What is not claimed yet

- **No system is supported out of the box.** EBM, Irembo, RTB CAMIS, school
  and hospital systems are all *trainable*, not *trained*.
- **The agent does not act on its own.** It never clicks through a financial
  operation by itself.
- **The desktop companion does not exist.** This is the browser agent only.

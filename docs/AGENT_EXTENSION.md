# The Installed Agent — WorkMeta AI

> One MV3 codebase. A client gets the same agent with its own name, its own
> API address and its own trained domains. The agent asks the API what it may
> do; it never decides for itself.

This document describes the agent that runs **in the browser**. The platform
side it talks to is described in
[Trained Extensions](TRAINED_EXTENSIONS.md) and in
[Submodels](SUBMODELS.md).

---

## 1. What the agent is

A Chrome/Edge extension (Manifest V3) with three parts:

| Part | What it does |
|---|---|
| `src/background/` | The service worker. It holds the session token and is the **only** part that talks to the API. |
| `src/content/` | A content script that runs only on the client's trained domains. It reports which page is open and returns visible text **only when the user asks**. |
| `src/sidepanel/` | The assistant panel: sign in, see which system the page belongs to, ask a question, confirm an action. |

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

The visible text of a page is delimited and marked as data in the guidance
prompt. Text on a screen can say "ignore your instructions"; the agent
treats it as something to read, never as something to obey. The same rule
applies to the uploaded training material.

### The server decides what may be done

The agent asks the API before it acts. The client's own policy decides
whether an action is allowed, whether it needs the user's confirmation and
whether it is refused. A sensitive step **always** forces a confirmation, and
the plan records who confirmed what and when.

The agent cannot skip this: the API refuses to complete a sensitive action
that was not confirmed, so a client that does not confirm cannot happen.

### The package is checked before it is used

The packager signs the agent's configuration with the deployment's Ed25519
key. The agent checks that signature in the browser with WebCrypto before it
uses the configuration, and shows the key's fingerprint so an administrator
can compare it with the one the WorkMeta console shows.

The published package is checked the same way, and against the key from the
agent's own **signed** configuration rather than the key the package
carries — so a package cannot introduce its own signer. The API returns the
exact bytes it signed, and the agent verifies them rather than re-serialising
the manifest (the canonical form of a number is not portable between
languages).

---

## 3. How it is used

1. The client administrator packages the agent for their organisation.
2. Staff install the ZIP (Chrome → *Load unpacked*, or the enterprise
   policy) and sign in with their work account.
3. When they open a trained system, the agent recognises it and offers
   guidance for that system and version.
4. When they pick a **submodel** — a trained model for a task — the agent asks
   it directly, whether it was trained here or installed from a package.
5. When they ask a question, the agent answers from the approved procedures,
   with the steps and where to click.
6. When an action is proposed, the client's policy decides. If it is
   sensitive, the panel asks the user to confirm it first.

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
| `GET /organizations/{org}/extensions/recognize?url=…` | Which published extension recognises this page. |
| `POST /organizations/{org}/extensions/{id}/guidance` | Grounded steps for the question, cited to the approved procedures. |
| `POST /organizations/{org}/extensions/{id}/actions` | Ask whether an action may be performed. |
| `POST …/actions/{plan}/confirm` | Record the user's confirmation or refusal. |
| `POST …/actions/{id}/actions/{plan}/complete` | Report that the action ran. |
| `GET /organizations/{org}/extensions/{id}/package` | The signed package and the bytes its signature covers. |
| `GET /organizations/{org}/submodels` | The trained models this organization can use. |
| `POST /organizations/{org}/submodels/{id}/guidance` | Grounded steps from a submodel's own approved workflows. |
| `POST /organizations/{org}/submodels/{id}/actions` | Ask whether an action may be performed, under the submodel's policy. |
| `POST …/submodels/{id}/actions/{plan}/confirm` | Record the user's confirmation or refusal. |
| `POST …/submodels/{id}/actions/{plan}/complete` | Report that the action ran. |

Every call is authorised by the server against the user's membership in the
organization, so the agent cannot reach another client's data.

### Submodels in the panel

The side panel lists the submodels of the organization and lets the user pick
one. A picked submodel is asked **directly** — no page matching is needed,
because a submodel carries its own workflows. It behaves identically whether
the model was trained here or installed from a package on another machine.

An action a submodel proposes goes through the same decision: the server
answers under the submodel's own policy, a sensitive action waits for the
user's confirmation, and a refused action is never reported as performed.

---

## 7. What is not claimed yet

- **No system is supported out of the box.** EBM, Irembo, RTB CAMIS, school
  and hospital systems are all *trainable*, not *trained*. An agent only
  claims the workflows that were trained, approved and tested.
- **The agent does not act on its own.** It proposes, the server decides and
  the user confirms. It never clicks through a financial operation by
  itself.
- **The desktop companion does not exist.** This is the browser agent only.
- **Video and audio training material needs optional capabilities.** A
  screen recording needs `ffmpeg` and OCR, and an audio instruction needs a
  transcription model. Both are detected at run time on the server and fail
  with a clear message rather than pretending the material was analysed.

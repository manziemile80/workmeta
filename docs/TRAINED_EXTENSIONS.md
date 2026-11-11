# Trained Extensions — WorkMeta AI

> A client trains an extension for **one** system at **one** version. The
> training material, the extracted procedures, the permissions and the saved
> knowledge stay in that client's own organization. WorkMeta never claims a
> system is supported before its workflows have been trained and tested.

This document describes the platform side of the feature. The installed agent
(the browser/desktop extension) is the client of the runtime API described in
[Runtime contract](#runtime-contract).

---

## 1. What a trained extension is

An **extension** is what a client installs for their staff. It is trained for:

| Part | Meaning | Example |
|---|---|---|
| `system_code` | Which system | `ebm`, `irembo`, `rtb-camis`, `school-management`, `hospital-his` |
| `system_version` | Which version of it | `3.2`, `2024.1` |
| `target_kind` | Web portal or desktop software | `web`, `desktop` |
| `url_patterns` | The pages it recognises (web) | `*://ebm.local/v3/*` |
| `app_hints` | The window/process it recognises (desktop) | `{"window_title": "EBM", "process_name": "ebm.exe"}` |

Two extensions for different versions of the same system live side by side in
the same organization: the recognition rules, the procedures and the
permissions of `EBM 3.2` and `EBM 4.0` never mix.

An extension points at an **assistant**, which is the knowledge scope: the
manuals, the screenshots and the chunks the model reads. Everything else in
this document hangs off that scope, so the existing retrieval, quota and
tenant machinery is reused rather than duplicated.

## 2. Data model

All five tables carry `org_id` and are protected by **forced** row-level
security with the tenant policy the knowledge tables use:

```sql
CREATE POLICY extensions_tenant ON public.extensions
FOR ALL USING (org_id = app_current_org())
WITH CHECK (org_id = app_current_org())
```

| Table | Holds |
|---|---|
| `extensions` | The extension: target system, version, recognition rules, permissions, lifecycle, signed package |
| `training_materials` | Manuals (linked documents), screen recordings, audio instructions, written procedures |
| `extension_procedures` | The operating procedures the model extracted, with their steps |
| `extension_tests` | The questions that check the assistant understands the system |
| `extension_action_plans` | The authority record for an action the agent performs, with the user's confirmation |

The runtime role (`wm_app`) may `SELECT`, `INSERT` and `UPDATE` these tables
and **never** `DELETE`: materials are archived, procedures are rejected, tests
and action plans stay as history.

Migration `20261114_0010` adds the tables and `subscription_plans.max_extensions`
(NULL means unlimited; the Free plan starts at one).

## 3. Lifecycle

```
draft ──▶ training ──▶ ready ──▶ published ──▶ retired
             │                       ▲
             └── analysed ───────────┘
```

| Step | Endpoint | What happens |
|---|---|---|
| Create | `POST /organizations/{org}/extensions` | An empty extension for one system at one version |
| Add material | `POST .../materials/notes`, `POST .../materials/upload` | Written procedures, screen recordings, audio instructions |
| Link a manual | the assistant's documents | PDF, Word, text, Markdown and screenshots (OCR) go through the existing knowledge pipeline |
| Read a recording | `POST .../materials/{id}/extract` | Frames are sampled with `ffmpeg` and read with OCR; audio is transcribed |
| Analyse | `POST .../analyze` | The model reads the material and extracts the procedures as **drafts** |
| Approve | `PATCH .../procedures/{id}` | A human approves or rejects each procedure |
| Test | `POST .../tests` | A question plus the points the answer must contain |
| Publish | `POST .../publish` | Builds the signed package |

### The publishing gate

`publish_extension` refuses to publish unless **all** of the following hold:

1. the extension has been analysed (`extension_not_trained`),
2. at least one procedure is **approved** (`extension_no_procedures`),
3. at least one test **passed** (`extension_not_tested`).

The gate is enforced in the service layer, not in the user interface, so it
cannot be skipped by calling the API directly.

## 4. Training material

| Kind | How it arrives | How it is read |
|---|---|---|
| `document` | Uploaded to the assistant (PDF, Word, text, Markdown, screenshot) | The knowledge pipeline chunks and embeds it; the analysis reads the chunks |
| `video` | `POST .../materials/upload` | `ffmpeg` samples frames, OCR reads them |
| `audio` | `POST .../materials/upload` | The transcription model turns it into text |
| `note` | `POST .../materials/notes` | The client's written procedures, read as they are |

Video and audio are **optional capabilities**, detected at runtime:

- without `ffmpeg` (or without OCR) a recording cannot be read — the material
  is stored, the API answers `503 media_capability_unavailable` and says
  exactly what to upload instead;
- without a transcription model an audio instruction cannot be transcribed —
  same behaviour.

A material is never marked as analysed when it was not.

## 5. Analysis

`POST .../analyze` sends the collected material (bounded by
`extension_analysis_max_characters`) to the model with a prompt that treats the
material as **untrusted data** and asks for structured procedures:

```json
{
  "procedures": [
    {
      "title": "Issue a receipt for a walk-in customer",
      "summary": "Create and print a receipt in the sales screen.",
      "confidence": 0.8,
      "steps": [
        {
          "order": 1,
          "instruction": "Open the sales screen",
          "where": "Menu: Sales",
          "value": null,
          "expected_result": "The sales screen opens",
          "sensitive": false
        }
      ]
    }
  ]
}
```

Every step carries *what to do*, *where*, *what to enter*, *what should happen
next* and whether it is **sensitive**. The analysis is metered like any other
AI work (`quota.reserve_ai_request` / `reconcile_ai_tokens`) and recorded as a
`usage_events` row of kind `extraction`.

Re-analysing replaces the drafts. Procedures that disappeared are marked
`rejected` rather than deleted, because the runtime role has no delete grant.

## 6. Testing

A test is a question plus the points the answer has to contain:

```json
{ "question": "How do I issue a receipt for a walk-in customer?",
  "expected_points": ["Open the sales screen"] }
```

The answer is produced by the same grounded guidance the installed agent uses.
A point is covered when the answer contains it literally, or contains all of
its significant words. The test passes only when **every** point is covered.

This is what stops WorkMeta from claiming support for a system it has not
learned: the extension cannot be published without a passing test.

## 7. The signed package

`GET .../package` returns the manifest the client installs:

```json
{
  "schema": "workmeta.extension/1",
  "extension_id": "…",
  "slug": "ebm-3-2",
  "version": 1,
  "system": { "code": "ebm", "version": "3.2", "url_patterns": ["*://ebm.local/v3/*"] },
  "permissions": { "auto_actions": ["navigate"], "confirm_actions": ["payment"], "denied_actions": [] },
  "procedures": [ { "title": "…", "steps": [ … ] } ]
}
```

The manifest is signed with **Ed25519** over its canonical JSON encoding, using
`EXTENSION_SIGNING_PRIVATE_KEY`. The public key travels with the package, so
the agent verifies the package with WebCrypto and the server can verify a
downloaded package. When no signing key is configured the package is published
**unsigned** and the console says so — WorkMeta never claims a package is
signed when it is not.

## 8. Runtime contract

The installed agent talks to these endpoints. All of them require an
authenticated member of the organization that owns the extension.

| Endpoint | Purpose |
|---|---|
| `GET /organizations/{org}/extensions/recognize?url=…` | Which published extension recognises the page or application in front of the user |
| `POST /organizations/{org}/extensions/{id}/guidance` | Step-by-step guidance, cited back to the approved procedures |
| `POST /organizations/{org}/extensions/{id}/actions` | Ask whether an action may be performed |
| `POST …/actions/{plan}/confirm` | The user confirms (or refuses) a sensitive operation |
| `POST …/actions/{plan}/complete` | The agent reports what happened |

### Guidance

The answer is grounded in the **approved procedures** of that extension plus
the retrieved material of its assistant. When the material does not cover the
question, the answer says the system is not trained for that operation — it
never invents a menu path.

### Actions and confirmation

The agent asks before it acts. The server answers from the client's own
permission policy:

```json
{ "auto_actions": ["navigate", "read", "fill_form", "download"],
  "confirm_actions": ["submit_application", "create_invoice", "payment", "delete_record", "send"],
  "denied_actions": [] }
```

| Action type | Result |
|---|---|
| In `auto_actions` | Allowed, no confirmation |
| In `confirm_actions` | Allowed **only after the user confirms** |
| In `denied_actions` | Refused (`allowed: false`) |
| Unknown | Allowed, but a confirmation is required |

A step marked `sensitive` always forces a confirmation, even for an otherwise
automatic action. The plan records who confirmed what and when, and the
operation cannot be completed before the confirmation exists
(`action_confirmation_required`).

## 9. Isolation

- Every table is tenant-scoped with forced RLS; the runtime role cannot read
  another client's rows.
- The analysis prompt, the guidance prompt and the extracted procedures never
  mention another client or another system.
- `products` (the global catalogue of systems) stays global metadata; the
  *training* is always per client.

## 10. Limits and configuration

| Setting | Default | Meaning |
|---|---|---|
| `max_extensions` (plan) | 1 on Free | How many extensions a client may train |
| `extension_analysis_max_characters` | 120000 | Bound on the material sent for analysis |
| `extension_analysis_max_output_tokens` | 6000 | Bound on the analysis answer |
| `media_video_frame_interval_seconds` | 5 | How often a recording is sampled |
| `media_video_max_frames` | 60 | Maximum frames read from a recording |
| `ai_transcription_model` | empty | Speech-to-text model; empty means audio cannot be transcribed |
| `EXTENSION_SIGNING_PRIVATE_KEY` | empty | Ed25519 key; empty means packages are published unsigned |

## 11. What is **not** claimed yet

- No system is supported out of the box. EBM, Irembo, RTB CAMIS, school
  management systems, hospital systems and desktop software are all
  *trainable*, not *trained*: an extension only exists after a client trained
  it, and it only claims the workflows that were trained, approved and tested.
- The installed agent (the MV3 browser/desktop extension) is a separate
  deliverable. This document defines the platform contract it talks to; the
  agent itself is built against `docs/PLAN.md` and is not part of this
  increment.
- Video reading needs `ffmpeg` and OCR; audio transcription needs a
  transcription model. Both are optional capabilities with honest failure
  messages, not silent gaps.
- The analysis and the guidance need a real model. A deployment without
  `AI_API_KEY` runs the deterministic offline provider, which extracts nothing:
  the extension stays untrained and the publishing gate keeps refusing. That is
  the intended behaviour — WorkMeta would rather publish nothing than publish
  something it has not understood.

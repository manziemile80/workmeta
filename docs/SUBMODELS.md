# WorkMeta AI Submodels

> A submodel is a **trained model for a task**. It is trained from a **video
> demo** of how the work is really done, it can be tested before anyone relies
> on it, and it is published as a **signed package** that installs on another
> computer and works there on its own.

---

## 1. What a submodel is

A client records their staff doing a piece of work — issue a receipt in EBM,
register a student in CAMIS, apply for a permit on a portal — and uploads the
recording. The platform reads the recording as a **timeline of screens and
actions**, and the model writes that down as workflows. The result is a model
that can be tested, exported, installed on another computer and used there on
its own.

The one property that makes it different from anything else on the platform:
**it carries its own knowledge**.

| | Submodel |
|---|---|
| Trained for | one **task** |
| Knowledge lives in | the workflows inside the submodel |
| Needs the training material again? | **no** |
| Needs the training machine? | **no** |
| Installable on another computer? | **yes** — it is the whole point |
| Who may use it | any organization that installs the package |

So the lifecycle is:

```
create ──▶ upload the video demo ──▶ train ──▶ approve ──▶ test ──▶ download
                                                              │
                        install on another computer ◀──────────┘
                                 │
                                 └──▶ it works immediately, no training
```

The **video demo is the primary input**. A recording is uploaded, read into the
screens it shows and what changed between them — the read starts with the
upload and runs in the background, so the material says `extracting` until the
timeline is ready — and the training reads that timeline. Written steps and
audio instructions are supported as extras, but the demo is what the feature is
built around.

## 2. The data model

Five tables, all carrying `org_id` and protected by **forced** row-level
security with the same tenant policy the knowledge tables use:

```sql
CREATE POLICY submodels_tenant ON public.submodels
FOR ALL USING (org_id = app_current_org())
WITH CHECK (org_id = app_current_org())
```

| Table | Holds |
|---|---|
| `submodels` | The submodel: the task, the target system, the lifecycle, the permission policy, the published package |
| `submodel_materials` | The recordings, audio, documents and written notes it learns from |
| `submodel_procedures` | The workflows extracted from them, with their steps |
| `submodel_tests` | The questions that check the submodel really learned the task |
| `submodel_action_plans` | The authority record for an action it performs, with the user's confirmation |

The runtime role (`wm_app`) may `SELECT`, `INSERT` and `UPDATE` these tables
and **never** `DELETE`: materials are archived, workflows are rejected, tests
and action plans stay as history.

Migration `20261112_0012` adds the tables and
`subscription_plans.max_submodels` (NULL means unlimited; the Free plan starts
at one).

## 3. Video-based training

A recording of someone doing a piece of work is not a document. It is a
**sequence**: the person is on one screen, they do something, and they are on
the next screen. **The action is the difference between the two.**

So a recording is read in three deterministic steps before the model is
involved at all:

1. **Frames with timestamps.** `ffmpeg` samples the recording and reports the
   time of every frame (`showinfo`).
2. **UI states.** Each frame is read with OCR. Neighbouring frames that show
   the same screen are grouped into one *state*, so a screen that sat still for
   twenty seconds counts once.
3. **Transitions.** The change between two states is a *transition* — what the
   screen looked like before and what it looked like after.

What the model then receives is a **timeline**, not a pile of OCR text:

```
SCREEN 1 at 0:00.0:
Login  Username  Password  Sign in

SCREEN 2 at 0:05.0:
Sales  New receipt  Amount  Save

WHAT CHANGED BETWEEN THE SCREENS:
CHANGE 1 at 0:05.0:
  before: Login  Username  Password  Sign in
  after:  Sales  New receipt  Amount  Save
```

The grouping and the timing are deterministic, so they are unit-tested without
a provider (`app/services/video_analysis.py`). The model is only asked to *name*
the actions and turn them into ordered steps.

> This is deliberately **not** a video summary. A summary tells you what was
> discussed; a timeline tells you what was clicked, in what order, and where.

Video and audio are **optional capabilities**, detected at runtime:

- a recording is decoded with the system `ffmpeg` when it is installed, and
  otherwise with **PyAV**, which carries the codec libraries in its wheel —
  so a deployment can read a recording with nothing installed on the machine
  (`pip install -e ".[media]"`);
- the text on the frames is read with the OCR engine: the **Tesseract** CLI,
  or the **ONNX** engine that ships its own models (also part of the `media`
  extra), selected with `OCR_ENGINE`;
- without a transcription model an audio instruction cannot be transcribed —
  same behaviour.

A material is never marked as read when it was not.

## 4. Training

`POST .../train` sends the collected material to the model with a prompt that
treats the material as **untrusted data** and asks for *workflows*, not prose:

```json
{
  "workflows": [
    {
      "title": "Issue a walk-in receipt",
      "summary": "Open the sales screen and print the receipt.",
      "confidence": 0.85,
      "steps": [
        {
          "order": 1,
          "instruction": "Open the sales screen",
          "where": "Menu: Sales",
          "value": null,
          "expected_result": "The sales screen opens",
          "sensitive": false
        },
        {
          "order": 2,
          "instruction": "Print the receipt",
          "where": "Button: Print",
          "value": null,
          "expected_result": "The receipt is printed",
          "sensitive": true
        }
      ]
    }
  ]
}
```

Every step carries *what to do*, *where*, *what to enter*, *what should happen
next* and whether it is **sensitive**. A step that submits an application,
moves money, deletes a record or sends something to another person is marked
sensitive and always needs the user's confirmation before it runs.

The result is a **draft**. A human approves or rejects each workflow before the
submodel may use it, and only approved workflows are ever exported. Training is
metered like any other AI work and recorded as a `usage_events` row of kind
`extraction`.

## 5. Testing

A test is a question plus the points the answer has to contain:

```json
{ "question": "How do I issue a walk-in receipt?",
  "expected_points": ["Open the sales screen"] }
```

The answer is produced by the same grounded guidance the submodel uses at
runtime. A point is covered when the answer contains it literally, or contains
all of its significant words. The test passes only when **every** point is
covered.

This is what stops WorkMeta from claiming a task was learned when it was not.

## 6. The signed package

`GET .../package` returns the manifest; `GET .../download` returns the file
that travels:

```json
{
  "schema": "workmeta.submodel-package/1",
  "package": {
    "schema": "workmeta.submodel/1",
    "slug": "ebm-issue-a-receipt",
    "name": "EBM receipts",
    "task": "issue a receipt for a walk-in customer",
    "system": { "code": "ebm", "name": "EBM", "version": "3.2", "target_kind": "web" },
    "permissions": {
      "auto_actions": ["navigate", "read", "fill_form", "download"],
      "confirm_actions": ["submit_application", "create_invoice", "payment", "delete_record", "send"],
      "denied_actions": []
    },
    "workflows": [ { "title": "Issue a walk-in receipt", "steps": [ … ] } ]
  },
  "signature": "base64…",
  "signing_key_id": "…",
  "public_key": "base64…"
}
```

Three properties are deliberate:

* the **workflows are inside the signature**, not just the metadata — a
  tampered workflow is detected;
* the **signing key id travels with the package**, so an administrator can see
  which key signed it and refuse a package signed by a key they do not trust;
* the **public key travels with the package**, so the machine that installs it
  can verify it without contacting the machine that trained it.

The signature is Ed25519 over the canonical JSON encoding of the package
payload, using `EXTENSION_SIGNING_PRIVATE_KEY` — the same signing machinery the
white-label agent packages use.

Only **approved** workflows are exported. A workflow nobody approved is not
something the client vouches for, so it does not travel.

## 7. Installing on another machine

`POST .../import` takes the downloaded file and installs it:

```http
POST /api/v1/organizations/{org}/submodels/import
Content-Type: multipart/form-data

file=@ebm-issue-a-receipt.wmsubmodel.json
label=Trained by Example Rwanda Ltd
```

The server:

1. reads the package,
2. checks the schema,
3. verifies the signature against the key the package carries,
4. **refuses** the package when the verification fails
   (`409 submodel_package_unverified`),
5. creates the submodel with `origin = imported` and the workflows as
   **approved** — they were approved before the package was signed,
6. records `source_label` (the `label` the installer gave, or the one the
   package carried) and `source_digest` so an administrator can see where it
   came from.

The installed copy is immediately usable:

* it has **no material** (`materials_count = 0`) — the training material never
  travels, and never has to;
* it has **approved workflows**, so it can answer questions and be tested
  immediately;
* it never talks to the machine that trained it.

The provenance is kept honest: an imported submodel is re-signed locally by the
installing deployment, and `source_digest` records the digest of the package it
came from.

## 8. Using a submodel

### Guidance

`POST .../guidance` answers a question **from the submodel's own approved
workflows**:

```json
{ "question": "How do I issue a walk-in receipt?", "context": { "url": "https://…" } }
```

The workflows and the page context are both treated as **untrusted data** — the
text on the user's screen can never change the model's behaviour. When the
workflows do not cover the question, the submodel says so and names what is
missing. It never invents a menu path.

### Actions and confirmation

The agent asks before it acts. `POST .../actions` records the proposal and the
server answers from the submodel's own permission policy:

```json
{ "auto_actions": ["navigate", "read", "fill_form", "download"],
  "confirm_actions": ["submit_application", "create_invoice", "payment", "delete_record", "send"],
  "denied_actions": [] }
```

| Action type | Result |
|---|---|
| In `auto_actions` | Allowed, no confirmation |
| In `confirm_actions` | Allowed **only after the user confirms** |
| In `denied_actions` | Refused (`allowed: false`), recorded as `rejected` |
| Unknown | Allowed, but a confirmation is required |

A step marked `sensitive` always forces a confirmation, even for an otherwise
automatic action.

Two guarantees are enforced on the server, not in the user interface:

* an action that requires confirmation cannot be completed before the
  confirmation exists (`409 action_confirmation_required`);
* an action the policy **refused** can never be reported as performed — it is
  stored as `rejected` and `POST .../complete` answers
  `409 action_not_allowed`.

The plan records who confirmed what and when.

## 9. Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /organizations/{org}/submodels` | Create a submodel for a task |
| `GET /organizations/{org}/submodels` | List them |
| `GET/PATCH/DELETE /organizations/{org}/submodels/{id}` | Read, change, retire |
| `POST .../{id}/materials/notes` | Add a written procedure |
| `POST .../{id}/materials/upload` | Upload a training video or audio instruction |
| `POST .../{id}/materials/{material}/read` | Read the recording into a timeline |
| `DELETE .../{id}/materials/{material}` | Archive a material |
| `POST .../{id}/train` | Train on the material |
| `GET .../{id}/workflows` | The learned workflows |
| `PATCH .../{id}/workflows/{workflow}` | Approve or reject a workflow |
| `GET/POST .../{id}/tests` | List and run tests |
| `GET .../{id}/package` | The package manifest |
| `GET .../{id}/download` | Download the signed package |
| `POST /organizations/{org}/submodels/import` | Install a package |
| `POST .../{id}/guidance` | Ask the submodel a question |
| `POST .../{id}/actions` | Ask whether an action may be performed |
| `POST .../{id}/actions/{plan}/confirm` | The user confirms (or refuses) |
| `POST .../{id}/actions/{plan}/complete` | The agent reports what happened |
| `GET .../{id}/actions` | The action history |

## 10. The console and the agent

### The console

The **Submodels** tab of the web console mirrors the whole lifecycle:

* create a submodel for a task;
* install a package somebody else trained, and say where it came from;
* add a written procedure or upload a recording, then **Read** it;
* **Train**, then approve or reject the workflows the model found;
* run a test and read the result;
* **Try the submodel** — ask a question and get the cited answer;
* **Download package** — the file that installs elsewhere.

Retiring takes a submodel out of use. The row is kept: it is part of what was
published and of the audit trail, and the runtime role has no delete grant.

### The installed agent

The MV3 agent ([AGENT_EXTENSION.md](AGENT_EXTENSION.md)) lists the submodels of
the organization in its side panel. A picked submodel is asked **directly** —
no page matching is needed, because a submodel carries its own workflows — and
it behaves identically whether the model was trained there or installed from a
package.

An action a submodel proposes goes through the same decision as any other
action: the server answers under the submodel's own policy, a sensitive action
waits for the user's confirmation, and a refused action is never reported as
performed.

## 11. Isolation

- Every table is tenant-scoped with forced RLS; the runtime role cannot read
  another client's rows.
- The training prompt, the guidance prompt and the extracted workflows never
  mention another client or another system.
- A submodel can only be exported by the client that trained or installed it.
- The package carries **no training material**, only the workflows the client
  approved for export.

## 12. Limits and configuration

| Setting | Default | Meaning |
|---|---|---|
| `max_submodels` (plan) | 1 on Free | How many submodels a client may hold (imported ones count) |
| `analysis_max_characters` | 120000 | Bound on the material sent for training |
| `analysis_max_output_tokens` | 6000 | Bound on the training answer |
| `media_video_frame_interval_seconds` | 5 | How often a recording is sampled |
| `media_video_max_frames` | 60 | Maximum frames read from a recording |
| `OCR_ENGINE` | `none` | `rapidocr`, `tesseract` or `auto`; `none` disables image reading |
| `ai_transcription_model` | empty | Speech-to-text model; empty means audio cannot be transcribed |
| `EXTENSION_SIGNING_PRIVATE_KEY` | empty | Ed25519 key; empty means packages cannot be exported |
| `MAX_MEDIA_UPLOAD_BYTES` | 256 MiB | How large a recording may be; only the media upload paths get this limit |

The `media` extra (`pip install -e ".[media]"`) installs PyAV, Pillow and the
ONNX OCR engine, which together make a deployment able to read a recording
with nothing installed on the machine.

Reading a recording is a long operation, so the proxies allow a large body and
a long timeout on the media upload path only. The API enforces
`MAX_MEDIA_UPLOAD_BYTES` itself.

## 13. What is **not** claimed yet

- No task is supported out of the box. EBM business operations, Irembo
  services, RTB CAMIS student management and any other institutional system are
  all *trainable*, not *trained*: a submodel only exists after a client
  trained it, and it only claims the workflows that were trained, approved and
  tested.
- A submodel **guides** and **authorises**; it does not drive a browser on its
  own. The installed agent (Stage 11) performs the actions, and it asks the
  server before each one.
- Video reading needs a decoder and OCR. Both are available as a pip extra
  (`pip install -e ".[media]"`) that needs nothing on the machine, or as the
  system `ffmpeg` and Tesseract. When neither is there, the API says so
  rather than pretending the material was read.
- Training and guidance need a real model. A deployment without `AI_API_KEY`
  runs the deterministic offline provider, which extracts nothing: the
  submodel stays untrained and the console says so. That is the intended
  behaviour — WorkMeta would rather publish nothing than publish something it
  has not understood.

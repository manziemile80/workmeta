/**
 * The side panel.
 *
 * It shows one thing at a time: who is signed in, the trained models the
 * organization can use, the answer to a question, and the decision about an
 * action. Every value comes from the API through the background worker.
 */

import type { PanelMessage, SessionState, WorkerResponse } from "../lib/messages.ts";
import type {
  ActionType,
  SubmodelActionDecision,
  SubmodelGuidanceResponse,
  SubmodelSummary,
} from "../lib/types.ts";

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

async function send(message: PanelMessage): Promise<WorkerResponse> {
  try {
    return (await chrome.runtime.sendMessage(message)) as WorkerResponse;
  } catch {
    return { ok: false, error: "The agent is not responding." };
  }
}

let state: SessionState | null = null;
let busy = false;
/** The submodel the user picked, when they picked one. */
let selectedSubmodelId: string | null = null;

function setBusy(value: boolean): void {
  busy = value;
  render();
}

function showError(message: string): void {
  const box = document.getElementById("error");
  if (box) {
    box.textContent = message;
    box.hidden = false;
  }
}

function clearError(): void {
  const box = document.getElementById("error");
  if (box) {
    box.textContent = "";
    box.hidden = true;
  }
}

/* ------------------------------------------------------------------ sign in */

function renderSignIn(): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "Sign in"));

  const form = el("form");
  form.id = "sign-in-form";

  const emailField = el("div", "field");
  emailField.append(el("label", undefined, "Work email"));
  const email = el("input");
  email.type = "email";
  email.name = "email";
  email.required = true;
  email.autocomplete = "username";
  emailField.append(email);
  form.append(emailField);

  const passwordField = el("div", "field");
  passwordField.append(el("label", undefined, "Password"));
  const password = el("input");
  password.type = "password";
  password.name = "password";
  password.required = true;
  password.autocomplete = "current-password";
  passwordField.append(password);
  form.append(passwordField);

  const button = el("button", undefined, "Sign in");
  button.type = "submit";
  button.disabled = busy;
  form.append(button);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    clearError();
    setBusy(true);
    void send({
      kind: "sign-in",
      email: email.value.trim(),
      password: password.value,
    }).then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Sign-in failed.");
        return;
      }
      state = response.data as SessionState;
      render();
      // The trained models are loaded as soon as the user is signed in.
      void send({ kind: "submodels" }).then((loaded) => {
        if (loaded.ok) {
          state = loaded.data as SessionState;
          render();
        }
      });
    });
  });

  card.append(form);
  return card;
}

/* -------------------------------------------------------------------- page */

/* ---------------------------------------------------------------- submodels */

/**
 * Ask a submodel a question.
 *
 * A submodel carries its own workflows, so it is asked directly: no training
 * material and no page matching is needed, and it answers the same way on a
 * machine where it was installed from a package.
 */
function askSubmodel(submodelId: string, question: string): void {
  void send({ kind: "submodel-guidance", submodelId, question })
    .then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Could not get an answer.");
        return;
      }
      state = {
        ...state!,
        submodelGuidance: response.data as SubmodelGuidanceResponse,
      };
      render();
    })
    .catch((error: unknown) => {
      setBusy(false);
      showError(
        error instanceof Error ? error.message : "Could not get an answer.",
      );
    });
}

/**
 * The trained models this organization can use.
 *
 * A submodel carries its own workflows, so it works on this machine even when
 * it was trained somewhere else and installed as a package.
 */
function renderSubmodels(): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "Submodels"));

  if (state!.submodels.length === 0) {
    card.append(
      el(
        "p",
        "muted",
        "No submodel yet. Ask your administrator to train one, or to install " +
          "the package of one that was trained elsewhere.",
      ),
    );
    return card;
  }

  const list = el("ul", "list");
  for (const submodel of state!.submodels) {
    list.append(renderSubmodel(submodel));
  }
  card.append(list);
  return card;
}

function renderSubmodel(submodel: SubmodelSummary): HTMLElement {
  const item = el("li");

  const title = el("div");
  title.append(el("strong", undefined, submodel.name));
  item.append(title);

  const badge = el("span", "badge ok", submodel.origin === "imported" ? "Installed" : "Trained");
  item.append(badge);
  item.append(document.createTextNode(" "));

  item.append(el("p", "muted", submodel.task));

  const use = el("button", "link", "Use this model");
  use.type = "button";
  use.addEventListener("click", () => {
    selectedSubmodelId = submodel.id;
    const question = document.getElementById("question") as
      | HTMLTextAreaElement
      | null;
    if (question) {
      question.focus();
    }
    render();
  });
  item.append(use);

  if (selectedSubmodelId === submodel.id) {
    const chosen = el("span", "chip", "In use");
    item.append(document.createTextNode(" "));
    item.append(chosen);
  }

  return item;
}

/** The answer from a submodel. */
function renderSubmodelGuidance(): HTMLElement | null {
  if (!state?.submodelGuidance) {
    return null;
  }
  const guidance: SubmodelGuidanceResponse = state.submodelGuidance;
  const card = el("section", "card");
  card.append(el("h2", undefined, "Answer from the submodel"));

  card.append(el("div", "answer", guidance.answer));

  if (!guidance.grounded) {
    card.append(
      el(
        "p",
        "message info",
        "This answer is not covered by the approved workflows of the model, " +
          "so treat it with care.",
      ),
    );
  }

  if (guidance.steps.length > 0) {
    const steps = el("ol", "steps");
    for (const step of guidance.steps) {
      const item = el("li");
      item.append(document.createTextNode(step.instruction));
      if (step.where) {
        item.append(el("div", "muted", `Where: ${step.where}`));
      }
      if (step.value) {
        item.append(el("div", "muted", `Enter: ${step.value}`));
      }
      if (step.expected_result) {
        item.append(el("div", "muted", `You should see: ${step.expected_result}`));
      }
      steps.append(item);
    }
    card.append(steps);
  }

  if (guidance.workflows_used.length > 0) {
    const used = el("div");
    used.append(el("div", "muted", "Workflows used:"));
    for (const title of guidance.workflows_used) {
      used.append(el("span", "chip", title));
    }
    card.append(used);
  }

  return card;
}

/** The action a submodel may perform, under the client's own policy. */
function renderSubmodelActionRequest(submodel: SubmodelSummary): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "Ask the submodel to do something"));

  const form = el("form");
  const field = el("div", "field");
  field.append(el("label", undefined, "What should it do?"));

  const select = el("select");
  select.id = "submodel-action-type";
  const offered = [
    ...submodel.permissions.auto_actions,
    ...submodel.permissions.confirm_actions,
  ];
  for (const action of offered) {
    const option = el("option", undefined, action.replace(/_/g, " "));
    option.value = action;
    select.append(option);
  }
  if (offered.length === 0) {
    field.append(
      el(
        "p",
        "muted",
        "This client's policy does not let this model perform any action.",
      ),
    );
  }
  field.append(select);
  form.append(field);

  const descriptionField = el("div", "field");
  descriptionField.append(el("label", undefined, "Describe the action"));
  const description = el("input");
  description.id = "submodel-action-description";
  description.placeholder = "Print the receipt for the walk-in customer";
  descriptionField.append(description);
  form.append(descriptionField);

  const button = el("button", undefined, "Ask to do this");
  button.type = "submit";
  button.disabled = busy || offered.length === 0;
  form.append(button);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = description.value.trim();
    if (!text) {
      showError("Describe what should be done.");
      return;
    }
    clearError();
    setBusy(true);
    void send({
      kind: "submodel-propose-action",
      submodelId: submodel.id,
      actionType: select.value as ActionType,
      description: text,
      steps: [],
    }).then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Could not request the action.");
        return;
      }
      state = response.data as SessionState;
      render();
    });
  });

  card.append(form);
  return card;
}

/** The decision about a submodel action, and the user's answer to it. */
function renderSubmodelAction(): HTMLElement | null {
  if (!state?.pendingSubmodelAction) {
    return null;
  }
  const decision: SubmodelActionDecision = state.pendingSubmodelAction.decision;
  const card = el("section", "card");
  card.append(el("h2", undefined, "Submodel action"));

  if (!decision.allowed) {
    card.append(el("p", "message error", decision.reason));
    card.append(
      el(
        "p",
        "muted",
        "Nothing will be done. The client does not allow this operation.",
      ),
    );
    return card;
  }

  card.append(el("p", undefined, decision.plan.description));

  if (decision.requires_confirmation) {
    card.append(
      el(
        "p",
        "message info",
        "This operation needs your confirmation before it runs.",
      ),
    );
    const row = el("div", "actions");
    const confirm = el("button", undefined, "Confirm");
    confirm.type = "button";
    confirm.disabled = busy;
    confirm.addEventListener("click", () => {
      clearError();
      setBusy(true);
      void send({
        kind: "submodel-confirm-action",
        submodelId: state!.pendingSubmodelAction!.submodelId,
        planId: decision.plan.id,
        confirmed: true,
      }).then((response) => {
        setBusy(false);
        if (!response.ok) {
          showError(response.error ?? "Could not record the confirmation.");
          return;
        }
        state = response.data as SessionState;
        render();
      });
    });
    const refuse = el("button", "secondary", "No, don't do it");
    refuse.type = "button";
    refuse.disabled = busy;
    refuse.addEventListener("click", () => {
      clearError();
      setBusy(true);
      void send({
        kind: "submodel-confirm-action",
        submodelId: state!.pendingSubmodelAction!.submodelId,
        planId: decision.plan.id,
        confirmed: false,
      }).then((response) => {
        setBusy(false);
        if (!response.ok) {
          showError(response.error ?? "Could not record the answer.");
          return;
        }
        state = response.data as SessionState;
        render();
      });
    });
    row.append(confirm);
    row.append(refuse);
    card.append(row);
    return card;
  }

  card.append(
    el("p", "muted", "The client's policy allows this without asking."),
  );
  return card;
}

/** Report that a confirmed submodel action ran. */
function renderSubmodelComplete(planId: string, submodelId: string): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "Submodel action"));

  const row = el("div", "actions");
  const done = el("button", undefined, "I did this");
  done.type = "button";
  done.disabled = busy;
  done.addEventListener("click", () => {
    clearError();
    setBusy(true);
    void send({
      kind: "submodel-complete-action",
      submodelId,
      planId,
      succeeded: true,
      result: {},
    }).then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Could not record the result.");
        return;
      }
      state = response.data as SessionState;
      render();
    });
  });
  const failed = el("button", "secondary", "It did not work");
  failed.type = "button";
  failed.disabled = busy;
  failed.addEventListener("click", () => {
    clearError();
    setBusy(true);
    void send({
      kind: "submodel-complete-action",
      submodelId,
      planId,
      succeeded: false,
      result: {},
    }).then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Could not record the result.");
        return;
      }
      state = response.data as SessionState;
      render();
    });
  });
  row.append(done);
  row.append(failed);
  card.append(
    el(
      "p",
      "muted",
      "Carry the action out in the system, then say what happened so it is " +
        "recorded.",
    ),
  );
  card.append(row);
  return card;
}

/* ------------------------------------------------------------------ render */

function render(): void {
  const main = document.getElementById("main");
  if (!main) {
    return;
  }
  main.replaceChildren();

  const signOutButton = document.getElementById("sign-out");
  if (signOutButton) {
    signOutButton.hidden = !state?.signedIn;
  }

  const clientName = document.getElementById("client-name");
  if (clientName && state) {
    clientName.textContent = state.clientName;
  }

  // A build whose configuration was not verified says so, rather than
  // looking as trustworthy as a verified one.
  if (state && !state.configChecked && state.configReason) {
    main.append(el("p", "message warn", state.configReason));
  }

  if (!state?.signedIn) {
    main.append(renderSignIn());
    return;
  }

  main.append(renderSubmodels());

  const selected = state.submodels.find(
    (item) => item.id === selectedSubmodelId,
  );

  const ask = el("section", "card");
  ask.append(el("h2", undefined, "Ask"));
  if (selected) {
    ask.append(
      el(
        "p",
        "muted",
        `Asking ${selected.name}. Pick another model in the list above, or ` +
          "ask about the system on this page.",
      ),
    );
  }
  const form = el("form");
  const field = el("div", "field");
  field.append(el("label", undefined, "Your question"));
  const question = el("textarea");
  question.id = "question";
  question.placeholder = "How do I issue a receipt for a walk-in customer?";
  field.append(question);
  form.append(field);
  const button = el("button", undefined, "Ask");
  button.type = "submit";
  button.disabled = busy;
  form.append(button);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = question.value.trim();
    if (!text) {
      return;
    }
    clearError();
    setBusy(true);
    if (selected) {
      askSubmodel(selected.id, text);
    } else {
      showError("Pick a model to ask first.");
    }
  });
  ask.append(form);
  main.append(ask);


  const submodelGuidance = renderSubmodelGuidance();
  if (submodelGuidance) {
    main.append(submodelGuidance);
  }


  const submodelAction = renderSubmodelAction();
  if (submodelAction) {
    main.append(submodelAction);
  }


  const confirmedSubmodel = state?.lastSubmodelAction;
  if (confirmedSubmodel && confirmedSubmodel.status === "confirmed") {
    main.append(
      renderSubmodelComplete(
        confirmedSubmodel.id,
        confirmedSubmodel.submodel_id,
      ),
    );
  }


  // The same, for the submodel the user picked.
  if (selected) {
    main.append(renderSubmodelActionRequest(selected));
  }


  if (state?.lastSubmodelAction) {
    const done = el("section", "card");
    done.append(el("h2", undefined, "Last submodel action"));
    done.append(
      el(
        "p",
        undefined,
        `${state.lastSubmodelAction.description} — ${state.lastSubmodelAction.status}`,
      ),
    );
    main.append(done);
  }

}

async function boot(): Promise<void> {
  const response = await send({ kind: "session" });
  if (response.ok) {
    state = response.data as SessionState;
  } else {
    state = null;
    showError(response.error ?? "The agent is not configured.");
  }

  // The sign-out button lives in the header, outside the rendered area.
  const signOut = document.getElementById("sign-out");
  signOut?.addEventListener("click", () => {
    clearError();
    setBusy(true);
    void send({ kind: "sign-out" }).then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Could not sign out.");
        return;
      }
      state = response.data as SessionState;
      render();
    });
  });

  // The page the user is on is read fresh, so the panel never shows a page
  // the agent was on earlier.
  if (state?.signedIn) {
    const refreshed = await send({ kind: "read-page" });
    if (refreshed.ok) {
      state = refreshed.data as SessionState;
    }
    // The trained models are loaded once, so the user can pick one.
    const submodels = await send({ kind: "submodels" });
    if (submodels.ok) {
      state = submodels.data as SessionState;
    }
  }

  render();
}

void boot();

/**
 * The side panel.
 *
 * It shows one thing at a time: who is signed in, which trained system the
 * current page belongs to, the answer to a question, and the decision about
 * an action. Every value comes from the API through the background worker.
 */

import type { PanelMessage, SessionState, WorkerResponse } from "../lib/messages.ts";
import type {
  ActionDecision,
  ActionType,
  ExtensionSummary,
  GuidanceResponse,
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
    });
  });

  card.append(form);
  return card;
}

/* -------------------------------------------------------------------- page */

function renderPage(): HTMLElement | null {
  if (!state?.page) {
    const card = el("section", "card");
    card.append(el("h2", undefined, "This page"));
    card.append(
      el(
        "p",
        "muted",
        "Open one of the systems this agent was trained for, and it will " +
          "appear here.",
      ),
    );
    return card;
  }

  const card = el("section", "card");
  card.append(el("h2", undefined, "This page"));
  card.append(el("p", undefined, state.page.title || state.page.url));

  if (state.matches.length === 0) {
    card.append(
      el(
        "p",
        "muted",
        "No trained system matches this page. Ask about a system explicitly " +
          "if you need help with it.",
      ),
    );
  } else {
    const list = el("ul", "list");
    for (const match of state.matches) {
      list.append(renderMatch(match));
    }
    card.append(list);
  }
  return card;
}

function renderMatch(match: ExtensionSummary): HTMLElement {
  const item = el("li");

  const title = el("div");
  title.append(el("strong", undefined, `${match.system_name} ${match.system_version}`));
  item.append(title);

  const badge = el("span", "badge ok", "Trained");
  item.append(badge);
  item.append(document.createTextNode(" "));

  const ask = el("button", "link", "Ask about this system");
  ask.type = "button";
  ask.addEventListener("click", () => {
    const question = document.getElementById("question") as HTMLTextAreaElement | null;
    if (question) {
      question.value = `How do I use ${match.system_name} ${match.system_version}?`;
      question.focus();
    }
  });
  item.append(ask);

  const procedures = el("button", "link", "Show its procedures");
  procedures.type = "button";
  procedures.style.marginLeft = "0.5rem";
  procedures.addEventListener("click", () => {
    clearError();
    setBusy(true);
    void send({ kind: "package", extensionId: match.id }).then((response) => {
      setBusy(false);
      if (!response.ok) {
        showError(response.error ?? "Could not read the package.");
        return;
      }
      const box = document.getElementById("package");
      if (box) {
        box.hidden = false;
        box.replaceChildren(...renderPackage(response.data));
      }
    });
  });
  item.append(procedures);

  return item;
}

/**
 * The action the agent may perform.
 *
 * The client decides which actions are allowed, which need a confirmation and
 * which are never performed, so the panel offers what the client's own policy
 * permits and nothing else.
 */
function renderActionRequest(extension: ExtensionSummary): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "Perform an action"));

  const form = el("form");
  const field = el("div", "field");
  field.append(el("label", undefined, "What should the agent do?"));

  const select = el("select");
  select.id = "action-type";
  const offered = [
    ...extension.permissions.auto_actions,
    ...extension.permissions.confirm_actions,
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
        "This client's policy does not let the agent perform any action here.",
      ),
    );
  }
  field.append(select);
  form.append(field);

  const descriptionField = el("div", "field");
  descriptionField.append(el("label", undefined, "Describe the action"));
  const description = el("input");
  description.id = "action-description";
  description.placeholder = "Open the sales screen for a walk-in customer";
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
      showError("Describe what the agent should do.");
      return;
    }
    clearError();
    setBusy(true);
    void send({
      kind: "propose-action",
      extensionId: extension.id,
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

function renderPackage(data: unknown): Node[] {
  const nodes: Node[] = [];
  const pkg = data as {
    manifest: { procedures: Array<{ title: string; summary: string | null }> } | null;
    signatureValid: boolean;
    signatureReason: string;
  };

  if (!pkg.manifest) {
    nodes.push(el("p", "muted", "There is no published package."));
    return nodes;
  }

  if (pkg.signatureValid) {
    nodes.push(
      el(
        "p",
        "message success",
        "The package signature is valid. These procedures are the ones the " +
          "platform published.",
      ),
    );
  } else {
    nodes.push(
      el(
        "p",
        "message error",
        pkg.signatureReason || "The package signature could not be verified.",
      ),
    );
  }

  const list = el("ul", "list");
  for (const procedure of pkg.manifest.procedures) {
    const item = el("li");
    item.append(el("strong", undefined, procedure.title));
    if (procedure.summary) {
      item.append(el("p", "muted", procedure.summary));
    }
    list.append(item);
  }
  nodes.push(list);
  return nodes;
}

/* ---------------------------------------------------------------- guidance */

function renderGuidance(): HTMLElement | null {
  if (!state?.guidance) {
    return null;
  }
  const guidance: GuidanceResponse = state.guidance;
  const card = el("section", "card");
  card.append(el("h2", undefined, "Answer"));

  const answer = el("div", "answer", guidance.answer);
  card.append(answer);

  if (!guidance.grounded) {
    card.append(
      el(
        "p",
        "message info",
        "This answer is not covered by the approved procedures, so treat it " +
          "with care.",
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

  if (guidance.procedures_used.length > 0) {
    const used = el("div");
    used.append(el("div", "muted", "Procedures used:"));
    for (const title of guidance.procedures_used) {
      used.append(el("span", "chip", title));
    }
    card.append(used);
  }

  return card;
}

/* ----------------------------------------------------------------- actions */

function renderAction(): HTMLElement | null {
  if (!state?.pendingAction) {
    return null;
  }
  const decision: ActionDecision = state.pendingAction.decision;
  const card = el("section", "card");
  card.append(el("h2", undefined, "Action"));

  if (!decision.allowed) {
    card.append(el("p", "message error", decision.reason));
    card.append(
      el(
        "p",
        "muted",
        "The agent will not perform this. Nothing was changed.",
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
        kind: "confirm-action",
        extensionId: state!.pendingAction!.extensionId,
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
        kind: "confirm-action",
        extensionId: state!.pendingAction!.extensionId,
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

/**
 * Report that a confirmed action ran.
 *
 * The agent does not claim an action succeeded on its own: the user does the
 * work in the system, and then says it is done. What happened is recorded
 * either way.
 */
function renderComplete(planId: string, extensionId: string): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "Action"));

  const row = el("div", "actions");
  const done = el("button", undefined, "I did this");
  done.type = "button";
  done.disabled = busy;
  done.addEventListener("click", () => {
    clearError();
    setBusy(true);
    void send({
      kind: "complete-action",
      extensionId,
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
      kind: "complete-action",
      extensionId,
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

  const page = renderPage();
  if (page) {
    main.append(page);
  }

  const ask = el("section", "card");
  ask.append(el("h2", undefined, "Ask"));
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
    // The page is read first, so the answer is about the system the user is
    // actually looking at now, not the one they were looking at earlier.
    void send({ kind: "read-page" })
      .then((response) => {
        if (!response.ok) {
          throw new Error(response.error ?? "Could not read this page.");
        }
        const refreshed = response.data as SessionState;
        state = { ...state!, ...refreshed };
        const extensionId = refreshed.matches[0]?.id;
        if (!extensionId) {
          throw new Error(
            "No trained system matches this page. Open the system you need " +
              "help with, or ask your administrator to train it.",
          );
        }
        return send({ kind: "guidance", extensionId, question: text });
      })
      .then((response) => {
        setBusy(false);
        if (!response.ok) {
          showError(response.error ?? "Could not get an answer.");
          return;
        }
        state = { ...state!, guidance: response.data as GuidanceResponse };
        render();
      })
      .catch((error: unknown) => {
        setBusy(false);
        showError(
          error instanceof Error ? error.message : "Could not get an answer.",
        );
      });
  });
  ask.append(form);
  main.append(ask);

  const guidance = renderGuidance();
  if (guidance) {
    main.append(guidance);
  }

  const action = renderAction();
  if (action) {
    main.append(action);
  }

  // A confirmed action waits for the user to say what happened.
  const confirmed = state?.lastAction;
  if (confirmed && confirmed.status === "confirmed") {
    main.append(
      renderComplete(confirmed.id, confirmed.extension_id),
    );
  }

  // The action the agent may perform is offered for the system the user is
  // looking at, under the client's own policy.
  const current = state.matches[0];
  if (current) {
    main.append(renderActionRequest(current));
  }

  if (state?.lastAction) {
    const done = el("section", "card");
    done.append(el("h2", undefined, "Last action"));
    done.append(
      el("p", undefined, `${state.lastAction.description} — ${state.lastAction.status}`),
    );
    main.append(done);
  }

  const packageBox = el("section", "card");
  packageBox.id = "package";
  packageBox.hidden = true;
  main.append(packageBox);
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
  }

  render();
}

void boot();

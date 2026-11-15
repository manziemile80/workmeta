/**
 * The side panel.
 *
 * It shows one thing at a time: who is signed in and the page the agent is
 * allowed to read. Every value comes from the API through the background
 * worker.
 */

import type { PanelMessage, SessionState, WorkerResponse } from "../lib/messages.ts";

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

function renderPage(): HTMLElement {
  const card = el("section", "card");
  card.append(el("h2", undefined, "This page"));

  if (!state?.page) {
    card.append(
      el(
        "p",
        "muted",
        "This page is not one of the systems the client set up for this agent.",
      ),
    );
    return card;
  }

  card.append(el("p", undefined, state.page.title));
  card.append(el("p", "muted", state.page.url));
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

  if (state.user) {
    const who = el("section", "card");
    who.append(el("h2", undefined, "Signed in"));
    who.append(el("p", undefined, state.user.full_name || state.user.email));
    if (state.organization) {
      who.append(el("p", "muted", state.organization.name));
    }
    main.append(who);
  }

  main.append(renderPage());
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

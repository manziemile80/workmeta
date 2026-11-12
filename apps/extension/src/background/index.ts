/**
 * The background service worker.
 *
 * It is the only part of the extension that talks to the API. The session
 * token lives here, so a web page can never reach it: pages reach the worker
 * through the side panel, and the worker answers only the messages it
 * understands.
 */

import { ApiError, WorkMetaClient } from "../lib/api.ts";
import {
  clearSession,
  loadConfig,
  mayReadUrl,
  readSession,
  writeSession,
  type LoadedConfig,
} from "../lib/config.ts";
import type {
  ContentMessage,
  PanelMessage,
  SessionState,
  WorkerResponse,
} from "../lib/messages.ts";
import { verifyPackage } from "../lib/signature.ts";
import type {
  ActionDecision,
  ActionPlan,
  ExtensionSummary,
  GuidanceResponse,
  LoginResponse,
  PackageResponse,
  PageContext,
  SubmodelActionDecision,
  SubmodelActionPlan,
  SubmodelGuidanceResponse,
  SubmodelSummary,
} from "../lib/types.ts";

interface WorkerState {
  config: LoadedConfig | null;
  client: WorkMetaClient | null;
  session: LoginResponse | null;
  page: { url: string; title: string } | null;
  /** The visible text of the page, read only when the user asks for it. */
  pageText: string;
  matches: ExtensionSummary[];
  guidance: GuidanceResponse | null;
  pendingAction: { decision: ActionDecision; extensionId: string } | null;
  lastAction: ActionPlan | null;
  package: {
    manifest: PackageResponse | null;
    signatureValid: boolean;
    signatureReason: string;
  } | null;
  submodels: SubmodelSummary[];
  submodelGuidance: SubmodelGuidanceResponse | null;
  pendingSubmodelAction: {
    decision: SubmodelActionDecision;
    submodelId: string;
  } | null;
  lastSubmodelAction: SubmodelActionPlan | null;
  error: string | null;
}

const state: WorkerState = {
  config: null,
  client: null,
  session: null,
  page: null,
  pageText: "",
  matches: [],
  guidance: null,
  pendingAction: null,
  lastAction: null,
  package: null,
  submodels: [],
  submodelGuidance: null,
  pendingSubmodelAction: null,
  lastSubmodelAction: null,
  error: null,
};

async function ensureReady(): Promise<boolean> {
  if (state.client && state.config) {
    return true;
  }
  try {
    const config = await loadConfig();
    const session = await readSession();
    state.config = config;
    state.client = new WorkMetaClient(config.apiBaseUrl, session?.token ?? null);
    if (session) {
      try {
        state.session = await state.client.me();
      } catch {
        // The stored session has expired or was revoked. It is cleared and
        // the panel asks the user to sign in again, rather than the agent
        // refusing to work at all.
        await clearSession();
        state.client.setToken(null);
      }
    }
    return true;
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
    return false;
  }
}

function publicState(): SessionState {
  return {
    signedIn: state.session !== null,
    user: state.session?.user ?? null,
    organization: state.session?.active_organization ?? null,
    memberships: state.session?.memberships ?? [],
    page: state.page,
    matches: state.matches,
    guidance: state.guidance,
    pendingAction: state.pendingAction,
    lastAction: state.lastAction,
    package: state.package,
    submodels: state.submodels,
    submodelGuidance: state.submodelGuidance,
    pendingSubmodelAction: state.pendingSubmodelAction,
    lastSubmodelAction: state.lastSubmodelAction,
    clientName: state.config?.clientName ?? "WorkMeta AI",
    configChecked: state.config?.signatureChecked ?? false,
    configReason: state.config?.signatureReason ?? "",
    error: state.error,
  };
}

async function signIn(
  email: string,
  password: string,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const response = await state.client.login(email, password);
    if (!response.access_token) {
      return {
        ok: false,
        error: "The service did not return a session for this agent.",
      };
    }
    const orgId = response.active_organization?.id ?? state.config?.orgId ?? "";
    state.session = response;
    await writeSession({
      token: response.access_token,
      orgId,
      user: response.user,
      organization: response.active_organization,
      memberships: response.memberships,
    });
    state.error = null;
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Sign-in failed.";
    state.error = message;
    return { ok: false, error: message, code: "sign_in_failed" };
  }
}

async function signOut(): Promise<WorkerResponse> {
  // The session is revoked on the server first, so a token that was copied
  // out of the browser profile stops working.
  if (state.client) {
    await state.client.logout();
  }
  await clearSession();
  state.session = null;
  state.matches = [];
  state.guidance = null;
  state.pendingAction = null;
  state.lastAction = null;
  state.submodels = [];
  state.submodelGuidance = null;
  state.pendingSubmodelAction = null;
  state.lastSubmodelAction = null;
  state.page = null;
  state.pageText = "";
  if (state.client) {
    state.client.setToken(null);
  }
  return { ok: true, data: publicState() };
}

/**
 * Read the page the user is on.
 *
 * The text is untrusted. It is only read when the user asks a question about
 * the page, and the server treats it as data, never as instructions.
 *
 * A page that is not one of the client's systems is a normal state, not an
 * error: what is known about the previous page is dropped, so the agent never
 * sends the address of a page the client did not train.
 */
async function readPage(): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabId = tabs[0]?.id;
  if (tabId === undefined) {
    state.page = null;
    state.pageText = "";
    return { ok: true, data: publicState() };
  }
  try {
    const info = (await chrome.tabs.sendMessage(tabId, { kind: "collect" })) as
      | { url: string; title: string; text: string }
      | undefined;
    if (!info || !mayReadUrl(info.url, state.config.allowedDomains)) {
      state.page = null;
      state.pageText = "";
      state.matches = [];
      return { ok: true, data: publicState() };
    }
    state.page = { url: info.url, title: info.title };
    state.pageText = info.text ?? "";
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    try {
      state.matches = state.client
        ? await state.client.recognize(orgId, info.url)
        : [];
    } catch {
      state.matches = [];
    }
    return { ok: true, data: publicState() };
  } catch {
    state.page = null;
    state.pageText = "";
    state.matches = [];
    return { ok: true, data: publicState() };
  }
}

async function guidance(
  extensionId: string,
  question: string,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const context: PageContext = {};
    if (state.page) {
      context.url = state.page.url;
    }
    if (state.pageText) {
      context.text = state.pageText;
    }
    state.guidance = await state.client.guidance(
      orgId,
      extensionId,
      question,
      context,
    );
    return { ok: true, data: state.guidance };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not get guidance.";
    return { ok: false, error: message, code: "guidance_failed" };
  }
}

async function proposeAction(
  extensionId: string,
  actionType: Parameters<WorkMetaClient["proposeAction"]>[2],
  description: string,
  steps: Parameters<WorkMetaClient["proposeAction"]>[4],
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const decision = await state.client.proposeAction(
      orgId,
      extensionId,
      actionType,
      description,
      steps,
    );
    state.pendingAction = { decision, extensionId };
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not request the action.";
    return { ok: false, error: message, code: "action_failed" };
  }
}

/**
 * Record the user's confirmation.
 *
 * A refusal is recorded too: the plan then says the user declined, which is
 * what happened.
 */
async function confirmAction(
  extensionId: string,
  planId: string,
  confirmed: boolean,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const plan = await state.client.confirmAction(
      orgId,
      extensionId,
      planId,
      confirmed,
    );
    state.lastAction = plan;
    state.pendingAction = null;
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not record the answer.";
    return { ok: false, error: message, code: "confirm_failed" };
  }
}

async function completeAction(
  extensionId: string,
  planId: string,
  succeeded: boolean,
  result: Record<string, unknown>,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const plan = await state.client.completeAction(
      orgId,
      extensionId,
      planId,
      succeeded,
      result,
    );
    state.lastAction = plan;
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not record the result.";
    return { ok: false, error: message, code: "complete_failed" };
  }
}

/**
 * Download the signed package and check the signature.
 *
 * The agent shows the procedures it was given, so it checks that they are the
 * ones the platform published before it acts on them.
 *
 * The package is checked against the key from the agent's own **signed**
 * configuration, not against the key the package carries. A package therefore
 * cannot introduce its own signer.
 */
async function loadPackage(extensionId: string): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const pkg = await state.client.package(orgId, extensionId);
    const check = await verifyPackage({
      canonicalManifest: pkg.canonical_manifest ?? "",
      signature: pkg.signature ?? "",
      publicKey: state.config.publicKey || (pkg.public_key ?? ""),
      expectedKeyId: state.config.signingKeyId || null,
      signingKeyId: pkg.signing_key_id,
    });
    state.package = {
      manifest: pkg,
      signatureValid: check.valid,
      signatureReason: check.reason,
    };
    return { ok: true, data: state.package };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not read the package.";
    return { ok: false, error: message, code: "package_failed" };
  }
}

/**
 * Load the trained submodels.
 *
 * A submodel is a trained model for one task. It may have been trained here
 * or installed from a package on another machine — either way it carries its
 * own workflows, so the agent can use it without training anything.
 */
async function loadSubmodels(): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    state.submodels = await state.client.submodels(orgId);
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not load the submodels.";
    return { ok: false, error: message, code: "submodels_failed" };
  }
}

/**
 * Ask a submodel a question.
 *
 * The answer comes from the workflows the submodel learned, so it is the same
 * on this machine as on the machine it was trained on.
 */
async function submodelGuidance(
  submodelId: string,
  question: string,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const context: PageContext = {};
    if (state.page) {
      context.url = state.page.url;
    }
    if (state.pageText) {
      context.text = state.pageText;
    }
    state.submodelGuidance = await state.client.submodelGuidance(
      orgId,
      submodelId,
      question,
      context,
    );
    return { ok: true, data: state.submodelGuidance };
  } catch (error) {
    const message =
      error instanceof ApiError
        ? error.message
        : "Could not get an answer from the submodel.";
    return { ok: false, error: message, code: "submodel_guidance_failed" };
  }
}

/**
 * Ask whether a submodel may perform an action.
 *
 * The submodel's own policy decides, and a sensitive action waits for the
 * user's confirmation before it runs.
 */
async function submodelProposeAction(
  submodelId: string,
  actionType: Parameters<WorkMetaClient["submodelProposeAction"]>[2],
  description: string,
  steps: Parameters<WorkMetaClient["submodelProposeAction"]>[4],
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const decision = await state.client.submodelProposeAction(
      orgId,
      submodelId,
      actionType,
      description,
      steps,
    );
    state.pendingSubmodelAction = { decision, submodelId };
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not request the action.";
    return { ok: false, error: message, code: "submodel_action_failed" };
  }
}

/** Record the user's answer about a sensitive action. */
async function submodelConfirmAction(
  submodelId: string,
  planId: string,
  confirmed: boolean,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const plan = await state.client.submodelConfirmAction(
      orgId,
      submodelId,
      planId,
      confirmed,
    );
    state.lastSubmodelAction = plan;
    state.pendingSubmodelAction = null;
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not record the answer.";
    return { ok: false, error: message, code: "submodel_confirm_failed" };
  }
}

/** Report that the submodel's action ran. */
async function submodelCompleteAction(
  submodelId: string,
  planId: string,
  succeeded: boolean,
  result: Record<string, unknown>,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.client || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  try {
    const orgId = state.session?.active_organization?.id ?? state.config.orgId;
    const plan = await state.client.submodelCompleteAction(
      orgId,
      submodelId,
      planId,
      succeeded,
      result,
    );
    state.lastSubmodelAction = plan;
    return { ok: true, data: publicState() };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Could not record the result.";
    return { ok: false, error: message, code: "submodel_complete_failed" };
  }
}

async function handlePanelMessage(
  message: PanelMessage,
): Promise<WorkerResponse> {
  switch (message.kind) {
    case "sign-in":
      return signIn(message.email, message.password);
    case "sign-out":
      return signOut();
    case "session":
      await ensureReady();
      return { ok: true, data: publicState() };
    case "read-page":
      return readPage();
    case "guidance":
      return guidance(message.extensionId, message.question);
    case "propose-action":
      return proposeAction(
        message.extensionId,
        message.actionType,
        message.description,
        message.steps,
      );
    case "confirm-action":
      return confirmAction(
        message.extensionId,
        message.planId,
        message.confirmed,
      );
    case "complete-action":
      return completeAction(
        message.extensionId,
        message.planId,
        message.succeeded,
        message.result,
      );
    case "package":
      return loadPackage(message.extensionId);
    case "submodels":
      return loadSubmodels();
    case "submodel-guidance":
      return submodelGuidance(message.submodelId, message.question);
    case "submodel-propose-action":
      return submodelProposeAction(
        message.submodelId,
        message.actionType,
        message.description,
        message.steps,
      );
    case "submodel-confirm-action":
      return submodelConfirmAction(
        message.submodelId,
        message.planId,
        message.confirmed,
      );
    case "submodel-complete-action":
      return submodelCompleteAction(
        message.submodelId,
        message.planId,
        message.succeeded,
        message.result,
      );
  }
}

/**
 * The content script reports the page it is on.
 *
 * Only the address and the title are taken. The text of a page is read only
 * when the user asks about it, through `read-page`.
 */
async function handleContentMessage(
  message: ContentMessage,
): Promise<WorkerResponse> {
  if (!(await ensureReady()) || !state.config) {
    return { ok: false, error: state.error ?? "The extension is not configured." };
  }
  if (!mayReadUrl(message.url, state.config.allowedDomains)) {
    state.page = null;
    state.pageText = "";
    state.matches = [];
    return { ok: true, data: publicState() };
  }
  state.page = { url: message.url, title: message.title };
  const orgId = state.session?.active_organization?.id ?? state.config.orgId;
  try {
    state.matches = state.client
      ? await state.client.recognize(orgId, message.url)
      : [];
  } catch {
    state.matches = [];
  }
  return { ok: true, data: publicState() };
}

chrome.runtime.onMessage.addListener(
  (message: PanelMessage | ContentMessage, _sender, sendResponse) => {
    const run = async (): Promise<WorkerResponse> => {
      if ("kind" in message) {
        if (message.kind === "page-context") {
          return handleContentMessage(message);
        }
        return handlePanelMessage(message);
      }
      return { ok: false, error: "Unknown message." };
    };
    run().then(sendResponse, (error: unknown) => {
      sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : "Something went wrong.",
      });
    });
    // Keep the message channel open for the asynchronous answer.
    return true;
  },
);

chrome.runtime.onInstalled.addListener(() => {
  void ensureReady();
});

void ensureReady();

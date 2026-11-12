/**
 * The content script.
 *
 * It runs only on the domains the client trained, and it does two things:
 *
 * 1. It tells the background worker which page the user is on, so the agent
 *    knows which trained system it is looking at.
 * 2. When the worker asks, it returns a short excerpt of the visible text.
 *
 * It never contacts the API and never receives a token. It cannot ask the
 * worker to fetch a URL, so a hostile page cannot use this extension to read
 * other sites.
 */

const MAX_TEXT = 2000;

/** The visible text of the page, bounded and without any script content. */
function visibleText(): string {
  const body = document.body;
  if (!body) {
    return "";
  }
  const text = (body.innerText ?? "").replace(/\s+/g, " ").trim();
  return text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text;
}

function pageInfo(): { url: string; title: string; text: string } {
  return {
    url: window.location.href,
    title: document.title,
    text: visibleText(),
  };
}

chrome.runtime.onMessage.addListener(
  (
    message: { kind?: string } | undefined,
    _sender: unknown,
    sendResponse: (value: unknown) => void,
  ) => {
    // The worker asks for the page when the user asks a question. Nothing
    // else is answered, so a message from elsewhere cannot make this script
    // read the page.
    if (message?.kind !== "collect") {
      return false;
    }
    sendResponse(pageInfo());
    return true;
  },
);

// Tell the worker which page this is. The worker decides whether the page may
// be read at all; this script only reports where it is.
function reportPage(): void {
  chrome.runtime.sendMessage({
    kind: "page-context",
    url: window.location.href,
    title: document.title,
  }).catch(() => {
    // The worker may be asleep or the extension may be reloading. The next
    // navigation reports again.
  });
}

reportPage();
window.addEventListener("popstate", reportPage);

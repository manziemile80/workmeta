/**
 * Which pages the agent may read.
 *
 * Only the systems the client trained are readable. A page on any other
 * domain is not read at all, so the agent never collects what it was not
 * asked for.
 *
 * This lives on its own, without the browser APIs, so it can be tested
 * directly.
 */

/**
 * Whether the agent may read the page at this URL.
 */
export function mayReadUrl(
  url: string,
  allowedDomains: string[],
): boolean {
  if (allowedDomains.length === 0) {
    return false;
  }
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  return allowedDomains.some((pattern) => {
    const wanted = pattern.trim().toLowerCase();
    if (!wanted) {
      return false;
    }
    if (wanted.startsWith("*.")) {
      const base = wanted.slice(2);
      return host === base || host.endsWith(`.${base}`);
    }
    return host === wanted;
  });
}

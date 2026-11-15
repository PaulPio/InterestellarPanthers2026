/** Which feed this browser asks the public site for. The Pi's own dashboard ignores it. */
export type DataSource = "live" | "demo";

const STORAGE_KEY = "ccm-data-source";
export const DATA_SOURCE_EVENT = "ccm-data-source";

/** Live unless this browser has chosen the scripted demo. */
export function dataSource(): DataSource {
  try {
    return localStorage.getItem(STORAGE_KEY) === "demo" ? "demo" : "live";
  } catch {
    return "live";
  }
}

/** Remember the choice and tell open polls to fetch again. */
export function setDataSource(value: DataSource): void {
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Storage blocked (private browsing): the choice cannot be kept, so Live stays in effect.
  }
  window.dispatchEvent(new Event(DATA_SOURCE_EVENT));
}

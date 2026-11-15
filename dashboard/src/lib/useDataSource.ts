import { useSyncExternalStore } from "react";
import { DATA_SOURCE_EVENT, dataSource, type DataSource } from "./dataSource";

function subscribe(onChange: () => void): () => void {
  window.addEventListener(DATA_SOURCE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(DATA_SOURCE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** The data-source choice for this browser. Live during server render. */
export function useDataSource(): DataSource {
  return useSyncExternalStore(subscribe, dataSource, () => "live");
}

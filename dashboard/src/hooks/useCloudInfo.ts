import { useEffect, useState } from "react";
import { api, type CloudInfo } from "../api/client";

/**
 * The public site's cloud info, or null on the Pi and the local mock (the route is absent).
 * A failed fetch stays null so the rest of the page keeps its ordinary labels.
 */
export function useCloudInfo(): CloudInfo | null {
  const [info, setInfo] = useState<CloudInfo | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    api
      .cloudInfo(ctrl.signal)
      .then((value) => {
        if (!ctrl.signal.aborted) setInfo(value);
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setInfo(null);
      });
    return () => ctrl.abort();
  }, []);
  return info;
}

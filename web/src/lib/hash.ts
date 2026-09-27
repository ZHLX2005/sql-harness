import { useEffect, useState } from "react";

/**
 * Subscribe to window.location.hash and re-render on change.
 * Returns the raw hash string (including the leading "#/"), or "" if absent.
 */
export function useHash(): string {
  const [hash, setHash] = useState<string>(() =>
    typeof window === "undefined" ? "" : window.location.hash,
  );
  useEffect(() => {
    function onChange() {
      setHash(window.location.hash);
    }
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}
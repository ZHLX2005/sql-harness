import type { ReactNode } from "react";
import { StateProvider } from "./state";

/**
 * Compose all React providers here. Kept separate so main.tsx stays a thin
 * renderer and tests (if any are added later) can mount with just one import.
 */
export function Providers({ children }: { children: ReactNode }) {
  return <StateProvider>{children}</StateProvider>;
}
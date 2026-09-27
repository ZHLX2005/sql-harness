import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import App from "./App";
import { Providers } from "./lib/providers";
import "./styles/globals.css";

// Theme bootstrap: pick up any persisted choice before React renders to avoid
// a flash of the wrong palette on dark-mode machines.
try {
  const saved = localStorage.getItem("sqlHarnessTheme");
  if (saved === "dark" || saved === "light") {
    document.documentElement.dataset.theme = saved;
  }
} catch {
  /* localStorage may be blocked (private mode) — fall back to prefers-color-scheme */
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("missing #root element");

createRoot(rootEl).render(
  <StrictMode>
    <Providers>
      <App />
      <Toaster richColors position="bottom-right" />
    </Providers>
  </StrictMode>,
);
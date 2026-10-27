// Standalone local-only sample context, never the shared app or its API.
import { createRoot } from "react-dom/client";
import { FinanceDraftLab, financeEditors } from "../../src/FinanceLabShell";

createRoot(document.getElementById("root")!).render(
  <FinanceDraftLab sessionSample={!Object.hasOwn(financeEditors, window.location.pathname)} />,
);

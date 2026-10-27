import { useCallback, useEffect, useRef, useState } from "react";

/** Presentation-only guard: never cancel a transaction or regenerate its retry key. */
export function useWorkspaceNavigationSafety(section: string) {
  const sectionRef = useRef(section);
  sectionRef.current = section;
  const edited = useRef(false);
  const [navigationWarning, setNavigationWarning] = useState("");
  const pending = useCallback(() => [...document.querySelectorAll<HTMLElement>("[data-pending-operation-count]")]
    .some(node => Number(node.dataset.pendingOperationCount) > 0), []);

  useEffect(() => {
    const onEdit = (event: Event) => {
      const target = event.target;
      if (target instanceof window.Element && target.closest(".pw-transactions form, .pw-sales-return form, .packing-preview")) {
        edited.current = true;
        setNavigationWarning("");
      }
    };
    const onFormAction = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof window.Element && target.closest(".pw-transactions form [data-quote-draft-action]")) {
        edited.current = true;
        setNavigationWarning("");
      }
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (edited.current || pending()) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    document.addEventListener("input", onEdit, true);
    document.addEventListener("change", onEdit, true);
    document.addEventListener("click", onFormAction, true);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      document.removeEventListener("input", onEdit, true);
      document.removeEventListener("change", onEdit, true);
      document.removeEventListener("click", onFormAction, true);
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [pending]);

  const canLeaveSection = useCallback((next: string) => {
    if (next === sectionRef.current) return true; // Same mounted workspace preserves drafts.
    if (pending()) {
      setNavigationWarning("A submission is still pending. Stay in this section until it finishes so its outcome and retry identity remain available. No request was cancelled or retried.");
      return false;
    }
    if (edited.current && !window.confirm("This section has local draft/form values and retry context. Leave and discard them? Choose Cancel to keep your work.")) {
      return false;
    }
    edited.current = false;
    setNavigationWarning("");
    return true;
  }, [pending]);
  return { canLeaveSection, navigationWarning };
}

import { useEffect, useState } from "react";
import type { ComponentType } from "react";

export type EditorLoader = () => Promise<{ default: ComponentType }>;

/** Catches module-load failures only, not errors inside an already edited form.
 * A successful component has stable identity across shell/recovery rerenders.
 * No form save, recovery retry, navigation or authorization is initiated here.
 */
export function FinanceEditorLoader({ load, label }: { load: EditorLoader; label: string }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    loader: EditorLoader; component?: ComponentType; failed?: boolean;
  } | null>(null);
  useEffect(() => {
    let current = true;
    setResult(null);
    void Promise.resolve().then(load).then(
      ({ default: component }) => { if (current) setResult({ loader: load, component }); },
      () => { if (current) setResult({ loader: load, failed: true }); },
    );
    return () => { current = false; };
  }, [load, attempt]);

  // Never commit an old module under a newly selected view's title.
  const active = result?.loader === load ? result : null;
  if (active?.component) {
    const Editor = active.component;
    return <Editor />;
  }
  if (active?.failed) return <section className="lab-review-message" role="alert">
    <div>
      <h2>{label} could not be loaded</h2>
      <p>Loading this view does not change saved recovery metadata. Navigation remains available. Retrying loads this view only; it does not save or resubmit a draft.</p>
      <p>Recovery status is shown only after the current synthetic session is verified by a working view.</p>
      <button type="button" className="lab-review-button" onClick={() => setAttempt((value) => value + 1)}>Retry loading view</button>
      <p>If the browser retains a failed module, reload this page. Saved recovery metadata remains available; reload does not persist unsaved inputs.</p>
      <button type="button" className="lab-review-button" onClick={() => window.location.reload()}>Reload this page</button>
    </div>
  </section>;
  return <section className="lab-review-message" role="status" aria-live="polite" aria-busy="true">
    <h2>Loading {label}…</h2>
  </section>;
}

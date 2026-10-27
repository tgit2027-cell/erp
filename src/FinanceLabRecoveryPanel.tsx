import { useEffect, useState } from "react";
import { recoveryAttempts, subscribeRecovery, checkRecovery, retryRecovery } from "./finance-lab-recovery";
import type { RecoveryAttempt } from "./finance-lab-recovery";

export function FinanceLabRecoveryPanel({ attempts: suppliedAttempts }: { attempts?: RecoveryAttempt[] } = {}) {
  const [currentAttempts, setCurrentAttempts] = useState<RecoveryAttempt[]>(recoveryAttempts);
  useEffect(() => {
    const update = () => setCurrentAttempts(recoveryAttempts());
    const unsubscribe = subscribeRecovery(update);
    update();
    return unsubscribe;
  }, []);
  const attempts = suppliedAttempts ?? currentAttempts;
  const [retrying, setRetrying] = useState("");
  const [checking, setChecking] = useState("");
  const [actionError, setActionError] = useState("");
  if (!attempts.length) return null;

  async function retry(id: string) {
    setRetrying(id);
    setActionError("");
    try { await retryRecovery(id); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "Recovery check could not be retried."); }
    finally { setRetrying(""); }
  }
  async function check(id: string) {
    setChecking(id);
    setActionError("");
    try { await checkRecovery(id); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "Request status could not be checked."); }
    finally { setChecking(""); }
  }

  return <aside className="lab-recovery" aria-labelledby="lab-recovery-title">
    <div className="lab-recovery-heading">
      <p className="lab-review-eyebrow">SAVE RECOVERY</p>
      <h2 id="lab-recovery-title">Requests needing attention</h2>
    </div>
    <p className="lab-recovery-caution">A missing record or a 404 does not prove an earlier save did not commit. Keep unresolved requests; inspect a saved register before deciding what to do next.</p>
    {actionError && <p role="alert">{actionError}</p>}
    <ul className="lab-recovery-list">{attempts.map((attempt) => <li key={attempt.id}>
      <div>
        <strong>{attempt.state === "saved" ? "Save acknowledged" : "Outcome unknown"}</strong>
        <p role={attempt.state === "unknown" ? "alert" : "status"}>{attempt.detail || "The request remains unresolved."}</p>
        <code>{attempt.path}</code>
      </div>
      <div className="lab-recovery-actions">
        {attempt.state === "unknown" && <button type="button" className="lab-review-button"
          disabled={checking === attempt.id} onClick={() => void check(attempt.id)}>
          {checking === attempt.id ? "Checking status…" : "Check request status"}</button>}
        {attempt.state === "saved" && attempt.editorPath && attempt.draftId &&
          <a className="lab-review-link" href={`${attempt.editorPath}?reopen=${attempt.draftId}`}>Open saved draft {attempt.draftId}</a>}
        {attempt.state === "unknown" && <button type="button" className="lab-review-button"
          disabled={!attempt.canRetry || retrying === attempt.id} onClick={() => void retry(attempt.id)}>
          {retrying === attempt.id ? "Retrying original…" : "Retry original request"}</button>}
      </div>
    </li>)}</ul>
  </aside>;
}

export default FinanceLabRecoveryPanel;

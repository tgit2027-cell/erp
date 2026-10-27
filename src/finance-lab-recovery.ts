/** Only request metadata survives reload. Never persist proposal payloads. */
export type RecoveryAttempt = {
  id: string; path: string; canRetry: boolean; state: "unknown" | "saved";
  detail?: string; editorPath?: string; draftId?: number;
};
type Stored = { id: string; path: string; fingerprint: string; draftId?: number };
const resources = new Set(["journals", "allocations", "budgets", "reconciliations", "expenses",
  "assets", "forecasts", "invoices", "adjustments", "customer-invoices", "customer-credits", "supplier-advances", "reversals"]);
let scope = "";
let csrf = "";
let attempts: Stored[] = [];
const outcomes = new Map<string, Partial<RecoveryAttempt>>();
const retries = new Map<string, () => Promise<unknown>>();
const listeners = new Set<() => void>();
const busy = new Set<string>();
const notify = () => listeners.forEach(listener => listener());
const key = () => `tivoli-finance-create:${scope}`;
function persist() {
  // Storage availability is not needed for in-tab identity/retry correctness.
  try { if (scope) sessionStorage.setItem(key(), JSON.stringify(attempts)); } catch { /* visible recovery remains in memory */ }
  notify();
}
export function setRecoverySession(nextScope: string, token: string) {
  csrf = token;
  if (scope === nextScope) return;
  scope = nextScope; outcomes.clear(); retries.clear(); attempts = [];
  try {
    const data: unknown = scope ? JSON.parse(sessionStorage.getItem(key()) ?? "[]") : [];
    if (Array.isArray(data)) attempts = data.filter((entry): entry is Stored =>
      !!entry && typeof entry.id === "string" && /^[0-9a-f-]{36}$/.test(entry.id)
      && resources.has(entry.path) && typeof entry.fingerprint === "string"
      && /^[0-9a-f]{64}$/.test(entry.fingerprint)).map(entry => ({
        id: entry.id, path: entry.path, fingerprint: entry.fingerprint,
        ...(typeof entry.draftId === "number" && Number.isSafeInteger(entry.draftId)
          && entry.draftId > 0 ? {draftId: entry.draftId} : {}),
      }));
  } catch { /* corrupted/unavailable browser metadata cannot grant access */ }
  notify();
}
export function recoveryAttempts(): RecoveryAttempt[] {
  return attempts.map(attempt => ({
    id: attempt.id, path: attempt.path, state: "unknown",
    canRetry: retries.has(attempt.id) && !busy.has(attempt.id),
    ...outcomes.get(attempt.id),
  }));
}
export function subscribeRecovery(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function remove(id: string) {
  attempts = attempts.filter(entry => entry.id !== id);
  outcomes.delete(id); retries.delete(id); persist();
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(name =>
      `${JSON.stringify(name)}:${canonical((value as Record<string, unknown>)[name])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
export async function protectedCreate<T>(path: string, body: unknown, send: (id: string) => Promise<T>): Promise<T> {
  if (!scope || !resources.has(path)) return send("");
  const session = scope;
  const bytes = new TextEncoder().encode(`${path}\n${canonical(body)}`);
  const fingerprint = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map(byte => byte.toString(16).padStart(2, "0")).join("");
  if (scope !== session) throw new Error("Synthetic session changed; reopen the current lab.");
  let entry = attempts.find(item => item.path === path && item.fingerprint === fingerprint);
  if (!entry) {
    entry = {id: crypto.randomUUID(), path, fingerprint};
    attempts.push(entry); persist();
  }
  const id = entry.id;
  // Freeze caller input through send's closure in labRequest, never a live form.
  const execute = async () => {
    if (scope !== session) throw new Error("Synthetic session changed; original input is unavailable.");
    busy.add(id); notify();
    try {
      const result = await send(id);
      if (scope !== session) throw new Error("Synthetic session changed; reopen the current lab before using its records.");
      if (scope === session) remove(id);
      return result;
    } catch (error) {
      const status = (error as {status?: number}).status;
      if (scope === session) {
        if (status === 403) setRecoverySession("", "");
        else if (status && status >= 400 && status < 500 && status !== 409) remove(id);
        else outcomes.set(id, {detail: "Acknowledgement unavailable. Check the recorded outcome or retry this exact request; do not create a replacement."});
      }
      throw error;
    } finally { busy.delete(id); notify(); }
  };
  retries.set(id, execute);
  return execute();
}
export async function checkRecovery(id: string): Promise<void> {
  if (!scope || !attempts.some(entry => entry.id === id)) throw new Error("Request is unavailable in this synthetic session.");
  const session = scope;
  const response = await fetch(`/lab/requests/${id}/`, {
    credentials: "same-origin", cache: "no-store", headers: {"X-CSRFToken": csrf},
  });
  if (scope !== session) return;
  if (response.status === 403) { setRecoverySession("", ""); throw new Error("Synthetic request access is unavailable."); }
  const data = await response.json();
  if (scope !== session) return;
  if (response.status === 404) {
    outcomes.set(id, {state: "unknown", detail: data.detail}); notify(); return;
  }
  if (!response.ok) throw new Error("Request outcome could not be checked; retain its identity.");
  const item = attempts.find(entry => entry.id === id);
  if (!item || data.resource !== item.path || data.state !== "saved"
      || !Number.isSafeInteger(data.draft_id) || !/^\/lab(?:-[a-z-]+)?-?ui\/$/.test(data.editor_path)) {
    throw new Error("Invalid recorded outcome; retain its identity.");
  }
  outcomes.set(id, {state: "saved", canRetry: false, draftId: data.draft_id, editorPath: data.editor_path,
    detail: `Create saved as proposal ${data.draft_id}, original revision ${data.original_revision}. Reopen to load its current revision.`});
  // Keep only the acknowledged ID across navigation, never protected payload.
  // A successful, freshly authorized detail GET must still occur to clear it.
  item.draftId = data.draft_id;
  persist();
}
export async function retryRecovery(id: string): Promise<void> {
  const session = scope;
  const retry = retries.get(id);
  if (!retry || busy.has(id)) throw new Error("Original input is not held in this tab. Check status, or restore the exact original input in its editor; do not create a replacement.");
  // A successful explicit replay is followed by an explicit reopen. Retain
  // metadata for this panel even though normal acknowledged saves discard it.
  const entry = attempts.find(item => item.id === id);
  await retry();
  if (entry && scope && scope === session) { attempts.push(entry); persist(); await checkRecovery(id); }
}
export function acknowledgeReopen(path: string) {
  for (const item of [...attempts]) {
    const result = outcomes.get(item.id);
    const savedId = result?.state === "saved" ? result.draftId : item.draftId;
    if (savedId && path === `${item.path}/${savedId}`) remove(item.id);
  }
}

const attemptedReopenSetters = new WeakSet<Function>();

export function reopenFromQuery(
  open: (id: number) => void,
  setError: (message: string) => void,
) {
  const params = new URLSearchParams(window.location.search);
  if (!params.has("reopen")) return;
  if (attemptedReopenSetters.has(setError)) return;
  attemptedReopenSetters.add(setError);
  const raw = params.get("reopen") ?? "";
  if (!/^[1-9]\d*$/.test(raw) || !Number.isSafeInteger(Number(raw))) {
    setError("The reopen link needs a valid positive saved draft ID. No draft was opened.");
    return;
  }
  open(Number(raw));
}

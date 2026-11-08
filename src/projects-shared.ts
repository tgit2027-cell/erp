// Shared helpers for the Projects & Fit-Out screens (Phase 2).
import { useState } from "react";

export async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : typeof data.file === "string" ? data.file
      : Array.isArray(data.file) ? String(data.file[0]) : "The request could not be completed.";
    throw new Error(detail);
  }
  return data as T;
}
export const show = (text: string) => (text ?? "").replace(/TEST ONLY\s*/g, "");
export const qar = (value: string | number) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const short = (value: string | number) => {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 1_000_000) return `${(amount / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 2 })}M`;
  if (Math.abs(amount) >= 10_000) return `${(amount / 1000).toLocaleString("en-US", { maximumFractionDigits: 0 })}k`;
  return amount.toLocaleString("en-US", { maximumFractionDigits: 0 });
};
export const num = (value: string) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 3 });
export const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
export const errorText = (caught: unknown, fallback: string) => (caught instanceof Error ? show(caught.message) : fallback);
export const TONE: Record<string, string> = {
  setup: "waiting", running: "approved", handover: "approved", closed: "done", cancelled: "done",
  manager: "waiting", finance: "waiting", details: "waiting", approved: "approved", rejected: "rejected",
  waiting: "waiting", at_risk: "waiting", signed: "approved", invoiced: "done",
  preparing: "waiting", submitted: "waiting", won: "approved", lost: "rejected",
  active: "approved", released: "done", claimed: "rejected", expired: "rejected",
};

export function useAction(csrfToken: string, unitId: number, onDone: (message: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async <T,>(path: string, method: string, body: unknown, message: (result: T) => string) => {
    setBusy(true); setError("");
    try {
      const result = await call<T>(`${path}?unit=${unitId}`, { method, headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone(message(result));
      return true;
    } catch (caught) {
      setError(errorText(caught, "That could not be done."));
      return false;
    } finally { setBusy(false); }
  };
  return { busy, error, send };
}

export type Person = { id: number; name: string };
export type Bond = {
  id: number; kind: string; kind_label: string; reference: string; issuer: string; amount: string; issued_on: string; expires_on: string;
  days_left: number | null; expiring: boolean; expired: boolean; status: string; status_label: string; note: string;
  tender_id: number | null; project_id: number | null; for?: string;
};

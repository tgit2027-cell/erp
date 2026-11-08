import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Attachments, type ChatMessage, type Person } from "./Chat";

type AccessRequest = {
  id: number; requested_by: Person; subject: Person; reason: string; reason_label: string; explanation: string;
  period_start: string; period_end: string; created_at: string; status: "pending" | "approved" | "rejected" | "withdrawn";
  status_label: string; decided_by: Person | null; decided_at: string | null; decision_note: string; access_until: string | null;
  can_read: boolean; can_decide: boolean; can_withdraw: boolean;
};
type ReviewedConversation = { id: number; kind: string; title: string; members: Person[]; messages_in_period: number };
type LogEntry = { event: string; event_label: string; user: string; occurred_at: string; conversation: number | null };

export const REASONS: [string, string][] = [
  ["hr_investigation", "HR investigation"],
  ["legal_case", "Legal case or claim"],
  ["regulator", "Request from a regulator or the authorities"],
  ["fraud", "Suspected fraud or theft"],
];

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Object.values(data as Record<string, unknown>).flat().find((value) => typeof value === "string");
    throw new Error(typeof detail === "string" ? detail : "The request could not be completed.");
  }
  return data as T;
}

const stamp = (value: string) => new Date(value).toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const today = () => new Date().toISOString().slice(0, 10);

function NewRequest({ csrfToken, onCreated, onCancel }: { csrfToken: string; onCreated: () => void; onCancel: () => void }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [subject, setSubject] = useState("");
  const [reason, setReason] = useState("");
  const [explanation, setExplanation] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState(today());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { call<{ people: Person[] }>("/api/chat/people/").then((data) => setPeople(data.people)).catch(() => undefined); }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await call("/api/chat/access-review/", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ subject_id: Number(subject), reason, explanation, period_start: start, period_end: end }),
      });
      onCreated();
    } catch (reason) { setError((reason as Error).message); setBusy(false); }
  };
  return <form className="chat-review-form" onSubmit={(event) => void submit(event)} aria-label="Ask to review chats">
    <p className="chat-hint">The business owner must approve this. If approved, you can read for 72 hours, and only messages in the dates you give. Every chat you open is recorded.</p>
    <label>Whose chats<select value={subject} onChange={(event) => setSubject(event.target.value)} required>
      <option value="">Choose a person</option>
      {people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
    </select></label>
    <label>Reason<select value={reason} onChange={(event) => setReason(event.target.value)} required>
      <option value="">Choose a reason</option>
      {REASONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    <label>The specific case<textarea value={explanation} maxLength={2000} rows={3} onChange={(event) => setExplanation(event.target.value)}
      placeholder="For example: HR case 2026-14, complaint about messages sent to a colleague in October" /></label>
    <div className="chat-review-dates">
      <label>Messages from<input type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} required /></label>
      <label>to<input type="date" value={end} max={today()} onChange={(event) => setEnd(event.target.value)} required /></label>
    </div>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-actions">
      <button className="button button-dark" type="submit" disabled={busy || !subject || !reason || explanation.trim().length < 30 || !start || !end}>Send for approval</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Decision({ item, csrfToken, onDone }: { item: AccessRequest; csrfToken: string; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "approve" | "reject") => {
    if (decision === "approve" && !window.confirm(`Allow ${item.requested_by.name} to read ${item.subject.name}'s chats from ${item.period_start} to ${item.period_end} for 72 hours?`)) return;
    setBusy(true); setError("");
    try {
      await call(`/api/chat/access-review/${item.id}/decision/`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ decision, note }),
      });
      onDone();
    } catch (reason) { setError((reason as Error).message); setBusy(false); }
  };
  return <div className="chat-review-decision">
    <label>Note (needed to reject)<input value={note} maxLength={1000} onChange={(event) => setNote(event.target.value)} /></label>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-actions">
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void decide("approve")}>Approve</button>
      <button className="button button-outline" type="button" disabled={busy || note.trim().length < 10} onClick={() => void decide("reject")}>Reject</button>
    </div>
  </div>;
}

function Reader({ item, onClose }: { item: AccessRequest; onClose: () => void }) {
  const [conversations, setConversations] = useState<ReviewedConversation[] | null>(null);
  const [open, setOpen] = useState<ReviewedConversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    call<{ conversations: ReviewedConversation[] }>(`/api/chat/access-review/${item.id}/conversations/`)
      .then((data) => setConversations(data.conversations)).catch((reason: Error) => setError(reason.message));
  }, [item.id]);
  const read = async (conversation: ReviewedConversation) => {
    setOpen(conversation); setMessages([]); setError("");
    try {
      setMessages((await call<{ messages: ChatMessage[] }>(`/api/chat/access-review/${item.id}/conversations/${conversation.id}/messages/`)).messages);
    } catch (reason) { setError((reason as Error).message); }
  };
  return <section className="chat-review-reader" aria-label={`Review of ${item.subject.name}'s chats`}>
    <div className="chat-review-banner" role="note">
      Read-only review of <strong>{item.subject.name}</strong>'s chats, {item.period_start} to {item.period_end}. Every chat you open is recorded.
      {item.access_until && <> Access ends {stamp(item.access_until)}.</>}
    </div>
    <button type="button" className="chat-link" onClick={onClose}>Back to requests</button>
    {error && <p className="chat-error" role="alert">{error}</p>}
    {conversations && conversations.length === 0 && <p className="chat-hint">No chats with messages in this period.</p>}
    <ul className="chat-review-list">
      {conversations?.map((conversation) => <li key={conversation.id}>
        <button type="button" className={open?.id === conversation.id ? "is-active" : ""} onClick={() => void read(conversation)}>
          <strong>{conversation.title}</strong><small>{conversation.messages_in_period} {conversation.messages_in_period === 1 ? "message" : "messages"} in period</small>
        </button>
      </li>)}
    </ul>
    {open && <ol className="chat-review-messages" aria-label={`Messages in ${open.title}`}>
      {messages.map((message) => <li key={message.id}>
        <div className="chat-meta">{message.sender.name} · {stamp(message.created_at)}</div>
        {message.body && <div className="chat-bubble">{message.body}</div>}
        <Attachments items={message.attachments ?? []} baseUrl={`/api/chat/access-review/${item.id}/attachments/`} />
      </li>)}
    </ol>}
  </section>;
}

export default function ChatAccessReview({ csrfToken, canRequest, canApprove }: { csrfToken: string; canRequest: boolean; canApprove: boolean }) {
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [reading, setReading] = useState<AccessRequest | null>(null);
  const [logFor, setLogFor] = useState<number | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const load = useCallback(async () => {
    try { setRequests((await call<{ requests: AccessRequest[] }>("/api/chat/access-review/")).requests); setError(""); }
    catch (reason) { setError((reason as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const showLog = async (id: number) => {
    if (logFor === id) { setLogFor(null); return; }
    setLogFor(id);
    try { setLog((await call<{ log: LogEntry[] }>(`/api/chat/access-review/${id}/log/`)).log); } catch (reason) { setError((reason as Error).message); }
  };
  const withdraw = async (id: number) => {
    try {
      await call(`/api/chat/access-review/${id}/withdraw/`, { method: "POST", headers: { "X-CSRFToken": csrfToken } });
      void load();
    } catch (reason) { setError((reason as Error).message); }
  };
  if (reading) return <div className="chat-review"><Reader item={reading} onClose={() => { setReading(null); void load(); }} /></div>;
  return <div className="chat-review" data-workspace-view="chat/review">
    <header className="chat-review-head">
      <div><span className="eyebrow">RESTRICTED</span><h2>Chat reviews</h2>
        <p className="chat-hint">Chats are private. A review needs a specific reason and the business owner's approval, and every step is recorded.</p></div>
      {canRequest && !creating && <button type="button" className="button button-dark" onClick={() => setCreating(true)}>Ask to review chats</button>}
    </header>
    {creating && <NewRequest csrfToken={csrfToken} onCancel={() => setCreating(false)} onCreated={() => { setCreating(false); void load(); }} />}
    {error && <p className="chat-error" role="alert">{error}</p>}
    {requests.length === 0 && !creating && <p className="chat-hint">No review requests.</p>}
    <ul className="chat-review-requests">
      {requests.map((item) => <li key={item.id} className="chat-review-card">
        <header>
          <h3>{item.subject.name}<small>{item.reason_label}</small></h3>
          <span className={`chat-review-status is-${item.status}`}>{item.status_label}</span>
        </header>
        <p>{item.explanation}</p>
        <dl>
          <div><dt>Messages from</dt><dd>{item.period_start} to {item.period_end}</dd></div>
          <div><dt>Asked by</dt><dd>{item.requested_by.name}, {stamp(item.created_at)}</dd></div>
          {item.decided_by && item.decided_at && <div><dt>Decided by</dt><dd>{item.decided_by.name}, {stamp(item.decided_at)}</dd></div>}
          {item.decision_note && <div><dt>Note</dt><dd>{item.decision_note}</dd></div>}
          {item.access_until && <div><dt>Reading allowed until</dt><dd>{stamp(item.access_until)}</dd></div>}
        </dl>
        {item.can_decide && <Decision item={item} csrfToken={csrfToken} onDone={() => void load()} />}
        <div className="chat-actions">
          {item.can_read && <button type="button" className="button button-dark" onClick={() => setReading(item)}>Open review</button>}
          {item.can_withdraw && <button type="button" className="button button-outline" onClick={() => void withdraw(item.id)}>Withdraw</button>}
          {canApprove && <button type="button" className="chat-link" aria-expanded={logFor === item.id} onClick={() => void showLog(item.id)}>{logFor === item.id ? "Hide record" : "Show record"}</button>}
        </div>
        {logFor === item.id && <ol className="chat-review-log">
          {log.map((entry, index) => <li key={index}>{stamp(entry.occurred_at)} · {entry.user} · {entry.event_label}{entry.conversation ? ` (chat ${entry.conversation})` : ""}</li>)}
        </ol>}
      </li>)}
    </ul>
  </div>;
}

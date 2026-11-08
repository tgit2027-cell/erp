import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import "./chat.css";
import ChatAccessReview from "./ChatAccessReview";

export type Person = { id: number; username: string; name: string };
export type Conversation = {
  id: number; kind: "direct" | "group" | "team"; title: string; members: Person[]; started_by: number;
  can_remove_members: boolean; unread: number;
  last_message: { sender: string; text: string; created_at: string } | null;
};
export type Attachment = { id: number; name: string; size: number; content_type: string; is_picture: boolean };
export type ChatMessage = { id: number; sender: Person; body: string; created_at: string; attachments?: Attachment[] };

export const MAX_FILES = 5;
export const MAX_FILE_BYTES = 25 * 1024 * 1024;
export const FILE_ACCEPT = ".pdf,.png,.jpg,.jpeg,.gif,.webp,.docx,.xlsx,.pptx,.doc,.xls,.ppt,.txt,.csv";

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Attachments({ items, baseUrl }: { items: Attachment[]; baseUrl: string }) {
  if (!items.length) return null;
  return <ul className="chat-attachments">
    {items.map((item) => <li key={item.id}>
      {item.is_picture ? (
        <a href={`${baseUrl}${item.id}/`} title={`Download ${item.name}`}>
          <img src={`${baseUrl}${item.id}/?inline=1`} alt={item.name} loading="lazy" />
        </a>
      ) : (
        <a className="chat-file" href={`${baseUrl}${item.id}/`} download={item.name}>
          <span className="chat-file-icon" aria-hidden="true">{item.name.split(".").pop()?.toUpperCase().slice(0, 4)}</span>
          <span>{item.name}<small>{fileSize(item.size)}</small></span>
        </a>
      )}
    </li>)}
  </ul>;
}
export type UnreadSummary = { total: number; conversations: number };

// How often the open chat and the chat list look for new messages while the page is visible.
export const MESSAGE_POLL_MS = 4000;
export const LIST_POLL_MS = 10000;
export const UNREAD_POLL_MS = 20000;
const MAX_MESSAGE_LENGTH = 4000;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Object.values(data as Record<string, unknown>).flat().find((value) => typeof value === "string");
    throw new Error(typeof detail === "string" ? detail : "The request could not be completed.");
  }
  return data as T;
}

export async function fetchUnread(): Promise<UnreadSummary> {
  return call<UnreadSummary>("/api/chat/unread/");
}

const pageVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

function useVisibleInterval(callback: () => void, delay: number, active = true) {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { if (pageVisible()) saved.current(); }, delay);
    return () => window.clearInterval(timer);
  }, [delay, active]);
}

function when(value: string) {
  const date = new Date(value);
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]!.toUpperCase()).join("") || "?";
const avatar = (item: { kind: string; title: string }) => item.kind === "group" ? "#" : item.kind === "team" ? "T" : initials(item.title);
const avatarClass = (kind: string) => kind === "team" ? "chat-avatar chat-avatar-team" : "chat-avatar";

function PeoplePicker({ exclude, multiple, onDone, onCancel, submitLabel, needsTitle }: {
  exclude: number[]; multiple: boolean; submitLabel: string; needsTitle: boolean;
  onDone: (people: Person[], title: string) => Promise<void>; onCancel: () => void;
}) {
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [chosen, setChosen] = useState<Person[]>([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => {
      call<{ people: Person[] }>(`/api/chat/people/?q=${encodeURIComponent(query)}`)
        .then((data) => { if (current) setPeople(data.people); })
        .catch((reason: Error) => { if (current) setError(reason.message); });
    }, 200);
    return () => { current = false; window.clearTimeout(timer); };
  }, [query]);
  const toggle = (person: Person) => {
    if (!multiple) { void finish([person]); return; }
    setChosen((list) => list.some((item) => item.id === person.id) ? list.filter((item) => item.id !== person.id) : [...list, person]);
  };
  const finish = async (list: Person[]) => {
    setBusy(true); setError("");
    try { await onDone(list, title); } catch (reason) { setError((reason as Error).message); setBusy(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); void finish(chosen); };
  const visible = people.filter((person) => !exclude.includes(person.id));
  return <form className="chat-picker" onSubmit={submit} aria-label={submitLabel}>
    {needsTitle && <label>Group name<input value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} placeholder="For example: Warehouse team" /></label>}
    <label>Find people<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Type a name" autoFocus={!needsTitle} /></label>
    {multiple && chosen.length > 0 && <p className="chat-chosen">{chosen.map((person) => person.name).join(", ")}</p>}
    <ul className="chat-people" aria-label="People">
      {visible.map((person) => {
        const picked = chosen.some((item) => item.id === person.id);
        return <li key={person.id}>
          <button type="button" className={picked ? "is-picked" : ""} aria-pressed={multiple ? picked : undefined} disabled={busy} onClick={() => toggle(person)}>
            <span className="chat-avatar" aria-hidden="true">{initials(person.name)}</span>
            <span>{person.name}<small>{person.username}</small></span>
          </button>
        </li>;
      })}
      {visible.length === 0 && <li className="chat-empty-row">No one found.</li>}
    </ul>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-actions">
      {multiple && <button className="button button-dark" type="submit" disabled={busy || chosen.length === 0 || (needsTitle && !title.trim())}>{submitLabel}</button>}
      <button className="button button-outline" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function GroupDetails({ conversation, me, csrfToken, onChanged, onLeft }: {
  conversation: Conversation; me: string; csrfToken: string; onChanged: (next: Conversation) => void; onLeft: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const [error, setError] = useState("");
  const headers = { "Content-Type": "application/json", "X-CSRFToken": csrfToken };
  const self = conversation.members.find((person) => person.username === me);
  const run = async (work: () => Promise<void>) => {
    setError("");
    try { await work(); } catch (reason) { setError((reason as Error).message); }
  };
  const rename = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => onChanged(await call<Conversation>(`/api/chat/conversations/${conversation.id}/`, { method: "PATCH", headers, body: JSON.stringify({ title }) })));
  };
  const remove = (person: Person) => void run(async () => {
    if (person.username === me) {
      if (!window.confirm(`Leave “${conversation.title}”? You will stop seeing its messages.`)) return;
      await call(`/api/chat/conversations/${conversation.id}/members/${person.id}/`, { method: "DELETE", headers });
      onLeft();
    } else {
      onChanged(await call<Conversation>(`/api/chat/conversations/${conversation.id}/members/${person.id}/`, { method: "DELETE", headers }));
    }
  });
  if (adding) return <aside className="chat-details">
    <h3>Add people</h3>
    <PeoplePicker exclude={conversation.members.map((person) => person.id)} multiple needsTitle={false} submitLabel="Add to group"
      onCancel={() => setAdding(false)}
      onDone={async (people) => {
        onChanged(await call<Conversation>(`/api/chat/conversations/${conversation.id}/members/`, { method: "POST", headers, body: JSON.stringify({ member_ids: people.map((person) => person.id) }) }));
        setAdding(false);
      }} />
  </aside>;
  return <aside className="chat-details" aria-label="Group details">
    <form className="chat-rename" onSubmit={rename}>
      <label>Group name<input value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} /></label>
      <button className="button button-outline" type="submit" disabled={!title.trim() || title.trim() === conversation.title}>Rename</button>
    </form>
    <h3>{conversation.members.length} members</h3>
    <ul className="chat-members">
      {conversation.members.map((person) => <li key={person.id}>
        <span className="chat-avatar" aria-hidden="true">{initials(person.name)}</span>
        <span>{person.name}{person.id === conversation.started_by && <small>Started the group</small>}</span>
        {person.username !== me && conversation.can_remove_members && <button type="button" className="chat-link" onClick={() => remove(person)}>Remove</button>}
      </li>)}
    </ul>
    {error && <p className="chat-error" role="alert">{error}</p>}
    <div className="chat-actions">
      <button className="button button-outline" type="button" onClick={() => setAdding(true)}>Add people</button>
      {self && <button className="button button-quiet" type="button" onClick={() => remove(self)}>Leave group</button>}
    </div>
  </aside>;
}

function Thread({ conversation, me, csrfToken, onSent, onRead, onChanged, onLeft, onBack }: {
  conversation: Conversation; me: string; csrfToken: string;
  onSent: () => void; onRead: (summary: UnreadSummary) => void; onChanged: (next: Conversation) => void; onLeft: () => void; onBack: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const listRef = useRef<HTMLOListElement>(null);
  const stickToBottom = useRef(true);
  const lastRead = useRef(0);
  const newest = messages.length ? messages[messages.length - 1]!.id : 0;
  const base = `/api/chat/conversations/${conversation.id}`;

  const markRead = useCallback(async (messageId: number) => {
    if (!messageId || messageId <= lastRead.current || !pageVisible()) return;
    lastRead.current = messageId;
    try {
      const result = await call<{ unread: UnreadSummary }>(`${base}/read/`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ message_id: messageId }),
      });
      onRead(result.unread);
    } catch { lastRead.current = 0; }
  }, [base, csrfToken, onRead]);

  useEffect(() => {
    let current = true;
    setLoading(true); setMessages([]); setError(""); setShowDetails(false); lastRead.current = 0; stickToBottom.current = true;
    call<{ messages: ChatMessage[]; has_more: boolean; last_read_message_id: number }>(`${base}/messages/`)
      .then((data) => { if (current) { setMessages(data.messages); setHasMore(data.has_more); lastRead.current = data.last_read_message_id; } })
      .catch((reason: Error) => { if (current) setError(reason.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [base]);

  useEffect(() => { void markRead(newest); }, [newest, markRead]);
  useEffect(() => {
    const list = listRef.current;
    if (list && stickToBottom.current) list.scrollTop = list.scrollHeight;
  }, [messages]);

  const fetchNewer = useCallback(async () => {
    if (loading) return;
    try {
      const data = await call<{ messages: ChatMessage[] }>(`${base}/messages/?after=${newest}`);
      if (data.messages.length) {
        setMessages((list) => {
          const known = new Set(list.map((item) => item.id));
          return [...list, ...data.messages.filter((item) => !known.has(item.id))];
        });
      }
    } catch { /* The next poll tries again. */ }
  }, [base, newest, loading]);
  useVisibleInterval(() => void fetchNewer(), MESSAGE_POLL_MS);

  const loadEarlier = async () => {
    if (!messages.length) return;
    stickToBottom.current = false;
    try {
      const data = await call<{ messages: ChatMessage[]; has_more: boolean }>(`${base}/messages/?before=${messages[0]!.id}`);
      setMessages((list) => [...data.messages, ...list]);
      setHasMore(data.has_more);
    } catch (reason) { setError((reason as Error).message); }
  };

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    const body = draft.trim();
    const chosen = files;
    if ((!body && !chosen.length) || sending) return;
    // Clear the box straight away so anything typed while sending is kept.
    setSending(true); setError(""); setDraft(""); setFiles([]);
    try {
      const message = await call<ChatMessage>(`${base}/messages/`, {
        method: "POST", headers: { "X-CSRFToken": csrfToken }, body: (() => {
          const form = new FormData();
          form.append("body", body);
          chosen.forEach((file) => form.append("files", file, file.name));
          return form;
        })(),
      });
      stickToBottom.current = true;
      setMessages((list) => list.some((item) => item.id === message.id) ? list : [...list, message]);
      onSent();
    } catch (reason) {
      setError((reason as Error).message);
      setDraft((current) => current ? `${body}\n${current}` : body);
      setFiles((current) => [...chosen, ...current]);
    }
    finally { setSending(false); }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
  };
  const onScroll = () => {
    const list = listRef.current;
    if (list) stickToBottom.current = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  };
  const names = conversation.members.map((person) => person.username === me ? "You" : person.name).join(", ");
  const subtitle = conversation.kind === "group" ? names
    : conversation.kind === "team" ? `Team chat, reaches everyone in the team: ${names}` : "Direct message";

  return <section className="chat-thread" aria-label={`Chat with ${conversation.title}`}>
    <header className="chat-thread-head">
      <button type="button" className="chat-back" onClick={onBack} aria-label="Back to chats">‹</button>
      <span className={avatarClass(conversation.kind)} aria-hidden="true">{avatar(conversation)}</span>
      <div><h2>{conversation.title}</h2><p>{subtitle}</p></div>
      {conversation.kind === "group" && <button type="button" className="button button-outline" aria-expanded={showDetails} onClick={() => setShowDetails((open) => !open)}>
        {showDetails ? "Close" : "Members"}
      </button>}
    </header>
    <div className="chat-thread-body">
      <div className="chat-thread-main">
        <ol className="chat-messages" ref={listRef} onScroll={onScroll} aria-live="polite" aria-busy={loading}>
          {hasMore && <li className="chat-earlier"><button type="button" className="chat-link" onClick={() => void loadEarlier()}>Show earlier messages</button></li>}
          {loading && <li className="chat-empty-row">Loading messages…</li>}
          {!loading && messages.length === 0 && <li className="chat-empty-row">No messages yet. Say hello.</li>}
          {messages.map((message, index) => {
            const mine = message.sender.username === me;
            const previous = messages[index - 1];
            const grouped = previous && previous.sender.id === message.sender.id
              && new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() < 5 * 60 * 1000;
            return <li key={message.id} className={`chat-message ${mine ? "is-mine" : ""} ${grouped ? "is-grouped" : ""}`}>
              {!grouped && <div className="chat-meta">{mine ? "You" : message.sender.name} · <time dateTime={message.created_at}>{when(message.created_at)}</time></div>}
              {message.body && <div className="chat-bubble">{message.body}</div>}
              <Attachments items={message.attachments ?? []} baseUrl="/api/chat/attachments/" />
            </li>;
          })}
        </ol>
        {error && <p className="chat-error" role="alert">{error}</p>}
        {files.length > 0 && <ul className="chat-chosen-files" aria-label="Files to send">
          {files.map((file, index) => <li key={`${file.name}-${index}`}>
            {file.name} <small>{fileSize(file.size)}</small>
            <button type="button" className="chat-link" aria-label={`Remove ${file.name}`} onClick={() => setFiles((list) => list.filter((_, at) => at !== index))}>Remove</button>
          </li>)}
        </ul>}
        <form className="chat-composer" onSubmit={(event) => void send(event)}>
          <input ref={fileInput} type="file" multiple hidden accept={FILE_ACCEPT} aria-label="Choose files to share" onChange={(event) => {
            const picked = [...(event.target.files ?? [])];
            event.target.value = "";
            const tooBig = picked.find((file) => file.size > MAX_FILE_BYTES);
            if (tooBig) { setError(`“${tooBig.name}” is larger than 25 MB.`); return; }
            const next = [...files, ...picked];
            if (next.length > MAX_FILES) { setError(`Share up to ${MAX_FILES} files at a time.`); return; }
            setError(""); setFiles(next);
          }} />
          <button type="button" className="button button-outline chat-attach" title="Attach files" aria-label="Attach files" disabled={sending} onClick={() => fileInput.current?.click()}>📎</button>
          <textarea
            aria-label="Message"
            placeholder={`Message ${conversation.title}`}
            value={draft}
            maxLength={MAX_MESSAGE_LENGTH}
            rows={2}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="button button-dark" type="submit" disabled={sending || (!draft.trim() && !files.length)}>Send</button>
        </form>
        <p className="chat-hint">Enter sends · Shift+Enter starts a new line · 📎 shares files up to 25 MB (PDF, Office, pictures, text)</p>
      </div>
      {showDetails && conversation.kind === "group" && <GroupDetails conversation={conversation} me={me} csrfToken={csrfToken} onChanged={onChanged} onLeft={onLeft} />}
    </div>
  </section>;
}

export default function Chat({ csrfToken, username, onUnreadChange }: {
  csrfToken: string; username: string; onUnreadChange?: (summary: UnreadSummary) => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [starting, setStarting] = useState<null | "direct" | "group">(null);
  const [filter, setFilter] = useState("");
  const [reviewRoles, setReviewRoles] = useState({ can_request: false, can_approve: false });
  const [reviewing, setReviewing] = useState(false);
  useEffect(() => {
    call<{ can_request: boolean; can_approve: boolean }>("/api/chat/access-review/me/").then(setReviewRoles).catch(() => undefined);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const data = await call<{ conversations: Conversation[] }>("/api/chat/conversations/");
      setConversations(data.conversations);
      setError("");
      onUnreadChange?.({
        total: data.conversations.reduce((sum, item) => sum + item.unread, 0),
        conversations: data.conversations.filter((item) => item.unread > 0).length,
      });
    } catch (reason) { setError((reason as Error).message); }
    finally { setLoaded(true); }
  }, [onUnreadChange]);
  useEffect(() => { void refresh(); }, [refresh]);
  useVisibleInterval(() => void refresh(), LIST_POLL_MS);

  const selected = conversations.find((item) => item.id === selectedId) ?? null;
  const replace = (next: Conversation) => setConversations((list) => list.some((item) => item.id === next.id)
    ? list.map((item) => item.id === next.id ? next : item) : [next, ...list]);
  const start = async (people: Person[], title: string) => {
    const body = starting === "group"
      ? { kind: "group", title, member_ids: people.map((person) => person.id) }
      : { kind: "direct", user_id: people[0]!.id };
    const conversation = await call<Conversation>("/api/chat/conversations/", {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
    });
    replace(conversation);
    setSelectedId(conversation.id);
    setStarting(null);
  };
  const onRead = useCallback((summary: UnreadSummary) => {
    setConversations((list) => list.map((item) => item.id === selectedId ? { ...item, unread: 0 } : item));
    onUnreadChange?.(summary);
  }, [selectedId, onUnreadChange]);
  const shown = conversations.filter((item) => !filter.trim() || item.title.toLowerCase().includes(filter.trim().toLowerCase()));

  if (reviewing) return <div className="chat-review-wrap">
    <button type="button" className="chat-link" onClick={() => setReviewing(false)}>Back to messages</button>
    <ChatAccessReview csrfToken={csrfToken} canRequest={reviewRoles.can_request} canApprove={reviewRoles.can_approve} />
  </div>;
  return <div className={`chat-workspace ${selected ? "has-selection" : ""}`} data-workspace-view="chat">
    <section className="chat-list" aria-label="Chats">
      <div className="chat-list-head">
        <div><span className="eyebrow">INTERNAL CHAT</span><h2>Messages</h2></div>
        <div className="chat-actions">
          <button type="button" className="button button-dark" onClick={() => setStarting("direct")}>New chat</button>
          <button type="button" className="button button-outline" onClick={() => setStarting("group")}>New group</button>
          {(reviewRoles.can_request || reviewRoles.can_approve) && <button type="button" className="button button-quiet" onClick={() => setReviewing(true)}>Chat reviews</button>}
        </div>
      </div>
      {starting ? (
        <PeoplePicker exclude={[]} multiple={starting === "group"} needsTitle={starting === "group"}
          submitLabel={starting === "group" ? "Create group" : "Start chat"} onDone={start} onCancel={() => setStarting(null)} />
      ) : (
        <>
          <input className="chat-filter" aria-label="Search chats" placeholder="Search chats" value={filter} onChange={(event) => setFilter(event.target.value)} />
          {error && <p className="chat-error" role="alert">{error}</p>}
          <ul className="chat-conversations">
            {shown.map((item) => <li key={item.id}>
              <button type="button" className={`${item.id === selectedId ? "is-active" : ""} ${item.unread ? "is-unread" : ""}`}
                aria-current={item.id === selectedId ? "true" : undefined} onClick={() => setSelectedId(item.id)}>
                <span className={avatarClass(item.kind)} aria-hidden="true">{avatar(item)}</span>
                <span className="chat-conversation-text">
                  <strong>{item.title}</strong>
                  <small>{item.last_message ? `${item.last_message.sender}: ${item.last_message.text}` : item.kind === "group" ? `${item.members.length} members` : item.kind === "team" ? `Team · ${item.members.length} members` : "No messages yet"}</small>
                </span>
                <span className="chat-conversation-side">
                  {item.last_message && <time dateTime={item.last_message.created_at}>{when(item.last_message.created_at)}</time>}
                  {item.unread > 0 && <span className="chat-badge" aria-label={`${item.unread} unread`}>{item.unread > 99 ? "99+" : item.unread}</span>}
                </span>
              </button>
            </li>)}
            {loaded && shown.length === 0 && <li className="chat-empty-row">{conversations.length ? "No chats match your search." : "No chats yet. Start one with a colleague."}</li>}
          </ul>
        </>
      )}
    </section>
    {selected ? (
      <Thread key={selected.id} conversation={selected} me={username} csrfToken={csrfToken}
        onSent={() => void refresh()} onRead={onRead} onChanged={replace}
        onLeft={() => { setSelectedId(null); void refresh(); }} onBack={() => setSelectedId(null)} />
    ) : (
      <section className="chat-thread chat-placeholder">
        <p>Choose a chat on the left, or start a new one.</p>
        <p className="chat-hint">Only the people in a chat can read it.</p>
      </section>
    )}
  </div>;
}

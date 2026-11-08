import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./portal.css";
import { PostCard, SuggestionCard, portalCall, type PortalPost } from "./Portal";

type Manage = {
  posts: PortalPost[]; post_units: { id: number; name: string }[]; can_post_group: boolean; kinds: { value: string; label: string }[]; policy: string;
};
type Inbox = {
  inbox: { id: number; unit: string; topic: string; topic_label: string; text: string; sent_at: string; status: string; status_label: string;
    reply: string; replied_by: string; replied_at: string | null; sender?: string }[];
  can_read: boolean;
};

const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });

function NewPost({ data, csrfToken, onDone }: { data: Manage; csrfToken: string; onDone: (message: string) => void }) {
  const [kind, setKind] = useState("announcement");
  const [unit, setUnit] = useState(data.can_post_group ? "group" : String(data.post_units[0]?.id ?? ""));
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [mustRead, setMustRead] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [eventOn, setEventOn] = useState("");
  const [eventTime, setEventTime] = useState("");
  const [eventPlace, setEventPlace] = useState("");
  const [file, setFile] = useState<{ name: string; data: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pick = (picked: File | undefined) => {
    if (!picked) { setFile(null); return; }
    const reader = new FileReader();
    reader.onload = () => setFile({ name: picked.name, data: String(reader.result) });
    reader.readAsDataURL(picked);
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await portalCall("/api/portal/posts/", { method: "POST", headers: headers(csrfToken), body: JSON.stringify({
        kind, title, body, must_read: mustRead, pinned, unit_id: unit === "group" ? null : Number(unit),
        ...(kind === "event" ? { event_on: eventOn, event_time: eventTime, event_place: eventPlace } : {}),
        ...(file ? { file } : {}) }) });
      onDone(`“${title}” is on the HR Portal. Staff it is for get an email about it.`);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "The post could not be made."); } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Post on the HR Portal">
    <h3>Post on the HR Portal</h3>
    <div className="sp-fields an-grid">
      <label>What<select value={kind} onChange={(event) => setKind(event.target.value)}>
        {data.kinds.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}</select></label>
      <label>For<select value={unit} onChange={(event) => setUnit(event.target.value)}>
        {data.can_post_group && <option value="group">Everyone in the group</option>}
        {data.post_units.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>
      <label className="an-wide">Title<input value={title} maxLength={150} onChange={(event) => setTitle(event.target.value)} required /></label>
      <label className="an-wide">Text<textarea value={body} rows={6} maxLength={8000} onChange={(event) => setBody(event.target.value)} required /></label>
      {kind === "event" && <>
        <label>Date<input type="date" value={eventOn} onChange={(event) => setEventOn(event.target.value)} required /></label>
        <label>Time (optional)<input value={eventTime} maxLength={40} onChange={(event) => setEventTime(event.target.value)} placeholder="e.g. 7:00 pm" /></label>
        <label>Place (optional)<input value={eventPlace} maxLength={120} onChange={(event) => setEventPlace(event.target.value)} placeholder="e.g. Tivoli showroom, Salwa Road" /></label>
      </>}
      <label>Picture or PDF (optional)<input type="file" accept="image/png,image/jpeg,application/pdf" onChange={(event) => pick(event.target.files?.[0])} /></label>
      {kind !== "event" && <label className="lt-check"><input type="checkbox" checked={mustRead} onChange={(event) => setMustRead(event.target.checked)} /> Must read: staff confirm they have read it</label>}
      <label className="lt-check"><input type="checkbox" checked={pinned} onChange={(event) => setPinned(event.target.checked)} /> Pin to the top</label>
    </div>
    <p className="sp-hint">It goes live straight away. Event photos are added to the event after it is posted.</p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy || !title.trim() || !body.trim() || !unit}>Post</button></div>
  </form>;
}

function Posts({ csrfToken }: { csrfToken: string }) {
  const [data, setData] = useState<Manage | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [writing, setWriting] = useState(false);
  const load = useCallback(async () => {
    try { setData(await portalCall<Manage>("/api/portal/posts/?section=manage")); setError(""); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Posts could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setWriting(false); void load(); };
  return <section className="content-card supplier-payments hr-portal" aria-labelledby="hp-admin-title">
    <header className="sp-head"><span className="eyebrow">HR · HR PORTAL · TEST ONLY</span><h2 id="hp-admin-title">HR Portal posts</h2>
      <p>Announcements, memos, procedures and events for staff. Posts go live at once, and staff get an email with a button to open them.</p></header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {data && !data.post_units.length && <p className="lt-warn">You do not have “Post on the HR Portal”. An admin gives it to the HR team in Users and permissions.</p>}
    {data && data.post_units.length > 0 && <div className="sp-actions"><button className="button button-outline" type="button" onClick={() => setWriting(!writing)}>{writing ? "Close" : "New post"}</button></div>}
    {data && writing && <NewPost data={data} csrfToken={csrfToken} onDone={done} />}
    {data && (data.posts.length ? <div className="hp-list">{data.posts.map((post) => <PostCard key={post.id} post={post} csrfToken={csrfToken} onChanged={done} manage />)}</div>
      : <p className="sp-empty">Nothing posted yet.</p>)}
    {data && <p className="sp-hint">{data.policy}</p>}
  </section>;
}

function Suggestions({ csrfToken }: { csrfToken: string }) {
  const [data, setData] = useState<Inbox | null>(null);
  const [error, setError] = useState("");
  const [answering, setAnswering] = useState<number | null>(null);
  const [reply, setReply] = useState("");
  const load = useCallback(async () => {
    try { setData(await portalCall<Inbox>("/api/portal/suggestions/")); setError(""); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Suggestions could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const act = async (id: number, body: Record<string, unknown>) => {
    try {
      await portalCall(`/api/portal/suggestions/${id}/`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      setAnswering(null); setReply(""); void load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "That could not be done."); }
  };
  return <section className="content-card supplier-payments hr-portal" aria-labelledby="hp-inbox-title">
    <header className="sp-head"><span className="eyebrow">HR · HR PORTAL · TEST ONLY</span><h2 id="hp-inbox-title">Staff suggestions</h2>
      <p>Suggestions staff send from the HR Portal, with their names. Mark them read, or answer: the person sees the answer in the portal.</p></header>
    {error && <div className="alert" role="alert">{error}</div>}
    {data && !data.can_read && <p className="lt-warn">Only HR (Add and change staff records) reads staff suggestions.</p>}
    {data && data.can_read && (data.inbox.length ? <div className="hp-list">{data.inbox.map((row) => <SuggestionCard key={row.id} row={row}>
      {row.status !== "closed" && <div className="sp-actions">
        {row.status === "new" && <button className="button button-outline" type="button" onClick={() => void act(row.id, { action: "seen" })}>Mark read</button>}
        <button className="button button-dark" type="button" onClick={() => { setAnswering(answering === row.id ? null : row.id); setReply(""); }}>Answer</button>
      </div>}
      {answering === row.id && <span className="mj-inline">
        <input aria-label="Answer" value={reply} maxLength={1000} onChange={(event) => setReply(event.target.value)} placeholder="Your answer to the person" />
        <button className="button button-dark" type="button" disabled={!reply.trim()} onClick={() => void act(row.id, { action: "answer", reply })}>Send answer</button>
      </span>}
    </SuggestionCard>)}</div> : <p className="sp-empty">No suggestions yet.</p>)}
  </section>;
}

/** The HR team's side of the portal, inside the HR section. */
export default function PortalAdmin({ view, csrfToken }: { view: "posts" | "suggestions"; csrfToken: string }) {
  return view === "posts" ? <Posts csrfToken={csrfToken} /> : <Suggestions csrfToken={csrfToken} />;
}

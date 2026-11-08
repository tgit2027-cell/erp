import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import "./supplier-payments.css";
import "./portal.css";

export type PortalPost = {
  id: number; kind: "announcement" | "memo" | "procedure" | "event"; kind_label: string; title: string; body: string;
  unit: { id: number; name: string } | null; for_label: string; must_read: boolean; pinned: boolean; read_by_me: boolean;
  event_on: string | null; event_time: string; event_place: string; file: { name: string; type: string } | null;
  status: "published" | "withdrawn"; status_label: string; posted_by: string; posted_at: string; withdrawn_by: string;
  withdrawn_at: string | null; withdrawn_reason: string; can_manage: boolean; photos: { id: number; caption: string }[];
  reads?: { done: number; total: number }; readers?: { name: string; read_at: string | null }[];
};
type Overview = {
  section: string; me: { name: string; unit: string }; posts: PortalPost[]; to_read: PortalPost[]; upcoming: PortalPost[];
  latest_album: PortalPost | null; post_units: { id: number; name: string }[]; can_post_group: boolean;
  kinds: { value: string; label: string }[]; policy: string;
};
type Suggestion = {
  id: number; unit: string; topic: string; topic_label: string; text: string; sent_at: string; status: string; status_label: string;
  reply: string; replied_by: string; replied_at: string | null; sender?: string;
};
type Suggestions = {
  units: { id: number; name: string }[]; default_unit: number | null; topics: { value: string; label: string }[];
  mine: Suggestion[]; inbox: Suggestion[]; can_read: boolean;
};

export async function portalCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const day = (iso: string | null) => iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
const photoUrl = (post: PortalPost, photoId: number) => `/api/portal/posts/${post.id}/photos/${photoId}/`;
const fileUrl = (post: PortalPost) => `/api/portal/posts/${post.id}/file/`;

// Simple line icons, drawn in the tile colour.
const ICON: Record<string, ReactNode> = {
  people: <><circle cx="9" cy="8" r="3" /><circle cx="17" cy="8" r="3" /><path d="M3 20c0-3 3-5 6-5s6 2 6 5M15 15c3 0 6 2 6 5" /></>,
  plane: <path d="M10 13 3 11l1-2 7 1 4-6 2 1-2 6 5 2-1 2-5-1-3 6-2-1 1-6z" />,
  clock: <><circle cx="12" cy="13" r="8" /><path d="M12 9v4l3 2M9 3h6" /></>,
  calendar: <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M4 10h16M8 3v4M16 3v4M9 15l2 2 4-4" /></>,
  card: <><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="11" r="2" /><path d="M6 16c1-2 5-2 6 0M14 10h4M14 13h4" /></>,
  userPlus: <><circle cx="10" cy="8" r="4" /><path d="M3 20c0-4 3-6 7-6s7 2 7 6M19 6v6M16 9h6" /></>,
  userX: <><circle cx="10" cy="8" r="4" /><path d="M3 20c0-4 3-6 7-6M16 15l5 5M21 15l-5 5" /></>,
  gauge: <><path d="M4 17a8 8 0 1 1 16 0" /><path d="m12 17 4-5" /></>,
  trophy: <><path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8 20h8" /></>,
  list: <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M8 9h8M8 12h8M8 15h5" /></>,
  board: <><rect x="4" y="4" width="16" height="11" rx="1" /><path d="M8 20l2-5M16 20l-2-5M12 2v2" /></>,
  money: <><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="3" /></>,
  briefcase: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M9 7V5h6v2M3 12h18" /></>,
  book: <><path d="M3 5c3-1 6-1 9 1 3-2 6-2 9-1v14c-3-1-6-1-9 1-3-2-6-2-9-1z" /><path d="M12 6v14" /></>,
  news: <><rect x="3" y="5" width="15" height="15" rx="1" /><path d="M18 9h3v9a2 2 0 0 1-2 2M6 9h9M6 13h9M6 16h6" /></>,
  chat: <><path d="M4 5h16v11H9l-5 4z" /><path d="M8 9h8M8 12h5" /></>,
  badge: <><rect x="6" y="3" width="12" height="18" rx="2" /><circle cx="12" cy="10" r="2.5" /><path d="M9 16h6" /></>,
  award: <><circle cx="12" cy="9" r="5" /><path d="m9 13-2 8 5-3 5 3-2-8" /></>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V4H5v13h3" /></>,
  car: <><path d="M5 16V12l2-5h10l2 5v4zM5 16v2M19 16v2" /><circle cx="8" cy="13" r="1" /><circle cx="16" cy="13" r="1" /></>,
  tools: <path d="m4 20 8-8M14 4l6 6M9 9 4 4M20 20l-6-6M15 3l-2 2 6 6 2-2" />,
  clipboard: <><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4V2h6v2M9 13l2 2 4-4" /></>,
  truck: <><path d="M3 6h11v10H3zM14 10h4l3 3v3h-7" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></>,
  doc: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 15h6M9 18h4" /></>,
  door: <><path d="M6 21V3h12v18M3 21h18" /><circle cx="15" cy="12" r=".8" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>,
  bell: <><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z" /><path d="M10 20a2 2 0 0 0 4 0" /></>,
  check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
  camera: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7l2-3h4l2 3" /><circle cx="12" cy="13" r="4" /></>,
  key: <><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M17 6l3 3" /></>,
};

type Tile = { label: string; icon: string; open?: string };
// The tiles of Tivoli's current HR portal. Those with `open` already work in the ERP; the rest are coming.
export const PORTAL_TILES: Tile[] = [
  { label: "Policies and Procedures", icon: "list", open: "library" },
  { label: "Newsletter", icon: "news", open: "news" },
  { label: "Events and Photos", icon: "camera", open: "photos" },
  { label: "Leave Request", icon: "plane", open: "leave" },
  { label: "Attendance", icon: "clock", open: "attendance" },
  { label: "Employee Overtime", icon: "clock", open: "overtime" },
  { label: "E-docs and Letters", icon: "doc", open: "letters" },
  { label: "Suggestion Box", icon: "chat", open: "suggestions" },
  { label: "My Password", icon: "key", open: "password" },
  { label: "Employee Management", icon: "people" },
  { label: "Teamline", icon: "calendar" },
  { label: "QID Renewal", icon: "card" },
  { label: "On-boarding", icon: "userPlus" },
  { label: "Off-boarding", icon: "userX" },
  { label: "Performance Management", icon: "gauge" },
  { label: "Employee Performance Recognition", icon: "trophy" },
  { label: "Development Plan", icon: "board" },
  { label: "Incentive Plan", icon: "money" },
  { label: "iRecruitment", icon: "briefcase" },
  { label: "Knowledge League Program", icon: "book" },
  { label: "Employee Privilege", icon: "badge" },
  { label: "Loyalty Program", icon: "award" },
  { label: "Employee Request", icon: "copy" },
  { label: "Driver Request", icon: "car" },
  { label: "Vehicle Maintenance", icon: "tools" },
  { label: "Job Orders", icon: "clipboard" },
  { label: "Home Delivery", icon: "truck" },
  { label: "Meeting Room Reservation", icon: "door" },
  { label: "Email Signature", icon: "mail" },
  { label: "Notification", icon: "bell" },
  { label: "HR Approvals", icon: "check" },
];

function Icon({ name }: { name: string }) {
  return <svg className="hp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON[name] ?? ICON.doc}</svg>;
}

function Body({ text }: { text: string }) {
  return <div className="hp-body">{show(text).split(/\n{2,}/).map((part, index) => <p key={index}>{part}</p>)}</div>;
}

export function PostCard({ post, csrfToken, onChanged, manage = false }: { post: PortalPost; csrfToken: string; onChanged: (message: string) => void; manage?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [withdrawing, setWithdrawing] = useState(false);
  const [readers, setReaders] = useState<PortalPost["readers"] | null>(null);
  const act = async (body: Record<string, unknown>, message: string) => {
    setBusy(true); setError("");
    try {
      await portalCall(`/api/portal/posts/${post.id}/`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onChanged(message);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "That could not be done."); } finally { setBusy(false); }
  };
  const showReaders = async () => {
    try { setReaders((await portalCall<PortalPost>(`/api/portal/posts/${post.id}/`)).readers ?? []); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load who has read it.");
    }
  };
  const addPhoto = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => void act({ action: "add_photo", photo: { data: String(reader.result), caption: "" } }, "The photo is added to the album.");
    reader.readAsDataURL(file);
  };
  const withdrawn = post.status === "withdrawn";
  return <article className={`hp-post ${post.pinned ? "is-pinned" : ""} ${withdrawn ? "is-withdrawn" : ""}`} aria-label={show(post.title)}>
    <header>
      <span className="hp-kind">{post.kind_label}{post.pinned ? " · Pinned" : ""}{post.must_read ? " · Must read" : ""}</span>
      <h3>{show(post.title)}</h3>
      <small>{show(post.for_label)} · {post.posted_by} · {day(post.posted_at)}</small>
    </header>
    {post.kind === "event" && <p className="hp-when"><strong>{day(post.event_on)}</strong>{post.event_time ? ` · ${show(post.event_time)}` : ""}{post.event_place ? ` · ${show(post.event_place)}` : ""}</p>}
    <Body text={post.body} />
    {post.file?.type === "png" || post.file?.type === "jpeg" ? <img className="hp-picture" src={fileUrl(post)} alt={show(post.title)} loading="lazy" /> : null}
    {post.file?.type === "pdf" && <p><a className="button button-outline" href={fileUrl(post)}>Download {show(post.file.name)}</a></p>}
    {post.photos.length > 0 && <div className="hp-strip">{post.photos.map((photo) => <a key={photo.id} href={photoUrl(post, photo.id)} target="_blank" rel="noreferrer">
      <img src={photoUrl(post, photo.id)} alt={photo.caption ? show(photo.caption) : `Photo from ${show(post.title)}`} loading="lazy" /></a>)}</div>}
    {withdrawn && <p className="hp-withdrawn">Taken down by {post.withdrawn_by} on {day(post.withdrawn_at)}: {show(post.withdrawn_reason)}</p>}
    <footer>
      {!manage && post.must_read && !withdrawn && (post.read_by_me
        ? <span className="hp-done">✓ You confirmed you have read this</span>
        : <button className="button button-dark" type="button" disabled={busy} onClick={() => void act({ action: "read" }, `Thank you. Your reading of “${show(post.title)}” is recorded.`)}>I have read this</button>)}
      {manage && post.reads && <button className="button button-outline" type="button" onClick={() => void showReaders()}>Read by {post.reads.done} of {post.reads.total}</button>}
      {manage && post.can_manage && !withdrawn && <>
        <button className="button button-outline" type="button" disabled={busy} onClick={() => void act({ action: post.pinned ? "unpin" : "pin" }, post.pinned ? "Unpinned." : "Pinned to the top.")}>{post.pinned ? "Unpin" : "Pin to top"}</button>
        {post.kind === "event" && <label className="button button-outline hp-upload">Add a photo<input type="file" accept="image/png,image/jpeg" onChange={(event) => addPhoto(event.target.files?.[0])} /></label>}
        <button className="button button-outline" type="button" onClick={() => setWithdrawing(!withdrawing)}>Take down</button>
      </>}
    </footer>
    {withdrawing && <span className="mj-inline">
      <input aria-label="Why it is taken down" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why, e.g. replaced by a new version" />
      <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void act({ action: "withdraw", reason }, "The post is taken down.")}>Confirm</button>
    </span>}
    {readers && <ul className="hp-readers" aria-label="Who has read it">{readers.map((row, index) => <li key={index} className={row.read_at ? "is-read" : ""}>
      {show(row.name)} <small>{row.read_at ? `read ${day(row.read_at)}` : "not yet"}</small></li>)}</ul>}
    {error && <div className="alert" role="alert">{error}</div>}
  </article>;
}

function Home({ overview, onOpen, csrfToken, onChanged }: { overview: Overview; onOpen: (child: string) => void; csrfToken: string; onChanged: (message: string) => void }) {
  const [search, setSearch] = useState("");
  const [soon, setSoon] = useState("");
  const tiles = PORTAL_TILES.filter((tile) => tile.label.toLowerCase().includes(search.trim().toLowerCase()));
  const album = overview.latest_album;
  return <>
    <div className="hp-hero">
      <span className="hp-brand">Tivoli Group <em>Human Resources</em></span>
      <h2>All your HR resources, in one place</h2>
      <p>Welcome, {show(overview.me.name)}{overview.me.unit ? ` · ${show(overview.me.unit)}` : ""}</p>
      <input className="hp-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search…" aria-label="Search the portal" />
      <div className="hp-tiles">{tiles.map((tile) => <button key={tile.label} type="button" className={`hp-tile ${tile.open ? "" : "is-soon"}`}
        onClick={() => tile.open ? onOpen(tile.open) : setSoon(tile.label)} aria-label={tile.open ? tile.label : `${tile.label}, coming soon`}>
        <Icon name={tile.icon} /><span>{tile.label}</span>{!tile.open && <small>Coming soon</small>}
      </button>)}</div>
      {soon && <p className="hp-soon" role="status">{soon} is not in the new portal yet. It is coming in a later version.</p>}
    </div>
    {overview.to_read.length > 0 && <section className="hp-block hp-to-read" aria-labelledby="hp-to-read">
      <h3 id="hp-to-read">Please read ({overview.to_read.length})</h3>
      {overview.to_read.map((post) => <PostCard key={post.id} post={post} csrfToken={csrfToken} onChanged={onChanged} />)}
    </section>}
    <section className="hp-block" aria-labelledby="hp-latest">
      <h3 id="hp-latest">Latest announcements</h3>
      {overview.posts.length ? overview.posts.map((post) => <PostCard key={post.id} post={post} csrfToken={csrfToken} onChanged={onChanged} />)
        : <p className="sp-empty">No announcements yet.</p>}
    </section>
    {album && <section className="hp-block" aria-labelledby="hp-album">
      <span className="hp-overline">Gathering</span>
      <h3 id="hp-album">{show(album.title)}</h3>
      <div className="hp-strip">{album.photos.map((photo) => <img key={photo.id} src={photoUrl(album, photo.id)} alt={photo.caption ? show(photo.caption) : `Photo from ${show(album.title)}`} loading="lazy" />)}</div>
      <button className="button button-outline" type="button" onClick={() => onOpen("photos")}>All photos</button>
    </section>}
    <section className="hp-block hp-events" aria-labelledby="hp-events">
      <h3 id="hp-events">Upcoming events</h3>
      {overview.upcoming.length ? <ul>{overview.upcoming.map((post) => <li key={post.id}><strong>{day(post.event_on)}</strong> {show(post.title)}
        {post.event_place ? <small> · {show(post.event_place)}</small> : null}</li>)}</ul> : <p className="sp-empty">No events planned yet.</p>}
      <button className="button button-outline" type="button" onClick={() => onOpen("news")}>View events</button>
    </section>
  </>;
}

const TITLES: Record<string, [string, string]> = {
  main: ["HR Portal", ""],
  news: ["Newsletter and events", "Announcements and events from HR."],
  library: ["Policies and procedures", "Procedures and memos from HR. Some ask you to confirm you have read them."],
  photos: ["Events and photos", "Photos from our gatherings."],
};

/** The employee side of the HR Portal: home with tiles, news, procedures and photos. */
export default function Portal({ view, csrfToken, onOpen }: { view: string; csrfToken: string; onOpen: (child: string) => void }) {
  if (view === "suggestions") return <SuggestionBox csrfToken={csrfToken} />;
  if (view === "password") return <MyPassword csrfToken={csrfToken} />;
  return <PostsView view={view} csrfToken={csrfToken} onOpen={onOpen} />;
}

function PostsView({ view, csrfToken, onOpen }: { view: string; csrfToken: string; onOpen: (child: string) => void }) {
  const section = view === "main" ? "home" : view;
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try { setOverview(await portalCall<Overview>(`/api/portal/posts/?section=${section}`)); setError(""); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The portal could not be loaded.");
    }
  }, [section]);
  useEffect(() => { void load(); }, [load]);
  const changed = (message: string) => { setNotice(message); void load(); };
  const [title, intro] = TITLES[view] ?? TITLES.main;
  return <section className={`content-card supplier-payments hr-portal ${view === "main" ? "is-home" : ""}`} aria-label={title}>
    {view !== "main" && <header className="sp-head">
      <span className="eyebrow">HR PORTAL · TEST ONLY</span>
      <h2>{title}</h2>
      {intro && <p>{intro}</p>}
    </header>}
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && view === "main" && <Home overview={overview} onOpen={onOpen} csrfToken={csrfToken} onChanged={changed} />}
    {overview && view !== "main" && (overview.posts.length
      ? <div className="hp-list">{overview.posts.map((post) => <PostCard key={post.id} post={post} csrfToken={csrfToken} onChanged={changed} />)}</div>
      : <p className="sp-empty">Nothing here yet.</p>)}
  </section>;
}

function SuggestionBox({ csrfToken }: { csrfToken: string }) {
  const [data, setData] = useState<Suggestions | null>(null);
  const [unitId, setUnitId] = useState("");
  const [topic, setTopic] = useState("idea");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try {
      const next = await portalCall<Suggestions>("/api/portal/suggestions/");
      setData(next); setUnitId((current) => current || String(next.default_unit ?? next.units[0]?.id ?? ""));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Suggestions could not be loaded."); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const send = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await portalCall("/api/portal/suggestions/", { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ unit_id: Number(unitId), topic, text }) });
      setText(""); setNotice("Thank you. Your suggestion is sent to HR."); void load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "The suggestion could not be sent."); } finally { setBusy(false); }
  };
  return <section className="content-card supplier-payments hr-portal" aria-labelledby="hp-suggest">
    <header className="sp-head"><span className="eyebrow">HR PORTAL · TEST ONLY</span><h2 id="hp-suggest">Suggestion box</h2>
      <p>Send your ideas to the company. HR reads every suggestion and can answer you here. Your name is sent with it.</p></header>
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {data && <form className="sp-form an-form" onSubmit={(event) => void send(event)} aria-label="Send a suggestion">
      <div className="sp-fields an-grid">
        {data.units.length > 1 && <label>For<select value={unitId} onChange={(event) => setUnitId(event.target.value)}>
          {data.units.map((unit) => <option key={unit.id} value={unit.id}>{show(unit.name)}</option>)}</select></label>}
        <label>About<select value={topic} onChange={(event) => setTopic(event.target.value)}>
          {data.topics.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}</select></label>
        <label className="an-wide">Your suggestion<textarea value={text} maxLength={2000} rows={4} onChange={(event) => setText(event.target.value)} required /></label>
      </div>
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy || !text.trim() || !unitId}>Send to HR</button></div>
    </form>}
    {error && <div className="alert" role="alert">{error}</div>}
    {data && data.mine.length > 0 && <div className="hp-list"><h3>My suggestions</h3>{data.mine.map((row) => <SuggestionCard key={row.id} row={row} />)}</div>}
  </section>;
}

export function SuggestionCard({ row, children }: { row: Suggestion; children?: ReactNode }) {
  return <article className="hp-post" aria-label={`Suggestion of ${day(row.sent_at)}`}>
    <header><span className="hp-kind">{row.topic_label} · {row.status_label}</span>
      <small>{row.sender ? `${show(row.sender)} · ` : ""}{show(row.unit)} · {day(row.sent_at)}</small></header>
    <Body text={row.text} />
    {row.reply && <p className="hp-reply"><strong>HR ({row.replied_by}):</strong> {show(row.reply)}</p>}
    {children}
  </article>;
}

function MyPassword({ csrfToken }: { csrfToken: string }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (next !== again) { setError("The two new passwords are not the same."); return; }
    setBusy(true); setError("");
    try {
      await portalCall("/api/portal/password/", { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ current, new: next }) });
      setCurrent(""); setNext(""); setAgain(""); setNotice("Your password is changed. Use it next time you sign in.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "The password could not be changed."); } finally { setBusy(false); }
  };
  return <section className="content-card supplier-payments hr-portal" aria-labelledby="hp-password">
    <header className="sp-head"><span className="eyebrow">HR PORTAL · TEST ONLY</span><h2 id="hp-password">My password</h2>
      <p>You sign in to the portal and the ERP with your company email and your own password.</p></header>
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    <form className="sp-form an-form" onSubmit={(event) => void save(event)} aria-label="Change my password">
      <div className="sp-fields an-grid">
        <label>Current password<input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required /></label>
        <label>New password<input type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} required /></label>
        <label>New password again<input type="password" autoComplete="new-password" value={again} onChange={(event) => setAgain(event.target.value)} required /></label>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Change password</button></div>
    </form>
  </section>;
}

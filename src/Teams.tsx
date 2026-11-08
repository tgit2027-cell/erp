import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { Summary } from "./UserAdmin";
import { call, show, when } from "./user-admin-shared";

type TeamRow = { id: number; name: string; description: string; is_active: boolean; members: number; permissions: number };
type Toggle = { action: string; label: string; hint?: string };
type TeamDetail = {
  can_change?: boolean;
  allowed_actions?: string[] | null;
  team: TeamRow & { created_at: string; created_by: string; chat_id: number | null };
  members: { id: number; username: string; name: string; is_active: boolean; added_at: string }[];
  people: { id: number; name: string; username: string }[];
  groups: { name: string; actions: Toggle[] }[];
  units: { id: number; code: string; name: string; actions: string[] }[];
  company: { action: string; label: string; on: boolean }[];
  history: { at: string; by: string; change: string; what: string }[];
};
type Change = Record<string, string | number | boolean>;

const post = (csrfToken: string, body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
});

function NewTeam({ csrfToken, onAdded, onCancel }: { csrfToken: string; onAdded: (detail: TeamDetail) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      onAdded(await call<TeamDetail>("/api/governance/teams/", post(csrfToken, { name, description })));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The team could not be made.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form ua-add" onSubmit={submit} aria-label="Make a team">
    <h3>Make a team</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Team name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={80} required placeholder="e.g. Sales team" /></label>
      <label>What the team does<input value={description} onChange={(event) => setDescription(event.target.value)} maxLength={300} placeholder="Optional" /></label>
    </div>
    <p className="sp-hint">Next you add the people and switch on the team's permissions. Everyone in the team gets them, and the team gets its own chat in Messages.</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Make team</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function Team({ detail, people, csrfToken, onChanged }: {
  detail: TeamDetail; people: Summary[]; csrfToken: string; onChanged: (detail: TeamDetail, message: string) => void;
}) {
  const [unitId, setUnitId] = useState((detail.units.find((row) => row.actions.length > 0) ?? detail.units[0])?.id ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState("");
  const [copyFrom, setCopyFrom] = useState("");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(detail.team.name);
  const [description, setDescription] = useState(detail.team.description);
  const team = detail.team;
  const unit = detail.units.find((row) => row.id === unitId) ?? detail.units[0];
  const send = async (body: Change, message: string) => {
    setBusy(true); setError("");
    try {
      onChanged(await call<TeamDetail>(`/api/governance/teams/${team.id}/`, post(csrfToken, body)), message);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The change could not be saved.");
      return false;
    } finally { setBusy(false); }
  };
  const toggle = (label: string, change: Change, on: boolean) =>
    void send({ ...change, enabled: on }, `${label} ${on ? "switched on" : "switched off"} for everyone in ${show(team.name)}.`);
  const rename = async (event: FormEvent) => {
    event.preventDefault();
    if (await send({ kind: "details", name, description }, "The team's details are saved.")) setEditing(false);
  };
  const addable = detail.people;
  const locked = detail.can_change === false;
  const allowed = (action: string) => !detail.allowed_actions || detail.allowed_actions.includes(action);
  const copyable = people.filter((row) => row.permission_count > 0);
  return <article className="ua-person" aria-label={`Team ${show(team.name)}`}>
    <header className="ua-person-head">
      <div>
        <h3>{show(team.name)}<small>{team.description && `${team.description} · `}{team.members} {team.members === 1 ? "member" : "members"} · made by {team.created_by} on {when(team.created_at)}</small></h3>
        <div className="ua-badges">
          <span className={team.is_active ? "sp-gain" : "sp-loss"}>{team.is_active ? "Open" : "Closed"}</span>
        </div>
      </div>
      <div className="sp-actions">
        <button className="button button-outline" type="button" disabled={busy || locked} onClick={() => setEditing(!editing)}>Rename</button>
        <button className="button button-outline" type="button" disabled={busy || locked}
          onClick={() => void send({ kind: "active", enabled: !team.is_active }, team.is_active
            ? `${show(team.name)} is closed. Its members no longer get its permissions.`
            : `${show(team.name)} is open again.`)}>{team.is_active ? "Close team" : "Reopen team"}</button>
      </div>
    </header>
    {locked && <p className="sp-hint" role="note">This team gives finance, payroll or company-wide permissions, so only the Master Admin can change it.</p>}
    {!locked && detail.allowed_actions && <p className="sp-hint" role="note">You can give the permissions of the Head, Manager and Sales levels. Only the Master Admin can give the others.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    {editing && <form className="sp-form tm-head" onSubmit={rename} aria-label="Rename the team">
      <label>Team name<input name="name" value={name} onChange={(event) => setName(event.target.value)} maxLength={80} required /></label>
      <label>What the team does<input name="description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={300} /></label>
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Save</button></div>
    </form>}
    {!team.is_active && <p className="sp-hint">This team is closed: its members do not get its permissions and its chat is empty. Reopen it to give them back.</p>}

    <section className="ua-block" aria-label="Members">
      <h4>Members</h4>
      {detail.members.length === 0
        ? <p className="sp-empty">Nobody is in this team yet.</p>
        : <ul className="tm-members">{detail.members.map((row) => <li key={row.id}>
          <span><strong>{row.name}</strong><small>{row.username}{!row.is_active && " · switched off"} · added {when(row.added_at)}</small></span>
          <button className="button button-outline" type="button" disabled={busy || locked}
            onClick={() => void send({ kind: "member", user_id: row.id, enabled: false }, `${row.name} is no longer in ${show(team.name)}.`)}>Take out</button>
        </li>)}</ul>}
      {team.is_active && !locked && <div className="tm-add">
        <label>Add someone<select value={adding} onChange={(event) => setAdding(event.target.value)}>
          <option value="">Choose a person…</option>
          {addable.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>
        <button className="button button-dark" type="button" disabled={busy || !adding}
          onClick={() => void send({ kind: "member", user_id: Number(adding), enabled: true },
            `${addable.find((row) => row.id === Number(adding))?.name ?? "The person"} is now in ${show(team.name)} and has all its permissions.`).then((ok) => ok && setAdding(""))}>Add to team</button>
      </div>}
      {team.is_active && <p className="tm-chat">Team chat: a message in the {show(team.name)} chat under Messages reaches everyone in the team. Its members follow this list.</p>}
    </section>

    <section className="ua-block" aria-label="Team permissions">
      <h4>What everyone in the team may do</h4>
      <div className="ua-tabs" role="tablist">
        {detail.units.map((row) => <button key={row.id} type="button" role="tab" aria-selected={row.id === unit?.id}
          className={row.id === unit?.id ? "ua-tab ua-tab-on" : "ua-tab"} onClick={() => setUnitId(row.id)}>
          {row.name}<small>{row.actions.length ? `${row.actions.length} on` : "none"}</small>
        </button>)}
      </div>
      {unit && <div className="ua-unit">
        {detail.groups.map((group) => <fieldset key={group.name} className="ua-group" disabled={busy}>
          <legend>{group.name}</legend>
          {group.actions.map((item) => <label key={item.action} className="bc-check">
            <input type="checkbox" checked={unit.actions.includes(item.action)} disabled={locked || !allowed(item.action)}
              onChange={(event) => toggle(`${item.label} in ${unit.code}`, { kind: "action", unit_id: unit.id, action: item.action }, event.target.checked)} />
            <span>{item.label}{item.hint && <small>{item.hint}</small>}</span>
          </label>)}
        </fieldset>)}
        <fieldset className="ua-group" disabled={busy}>
          <legend>Whole company</legend>
          {detail.company.map((item) => <label key={item.action} className="bc-check">
            <input type="checkbox" checked={item.on} disabled={locked || !allowed(item.action)} onChange={(event) => toggle(item.label, { kind: "action", action: item.action }, event.target.checked)} />
            <span>{item.label}</span>
          </label>)}
        </fieldset>
        {!locked && <div className="ua-copy">
          <label>Start from what a person already has<select value={copyFrom} onChange={(event) => setCopyFrom(event.target.value)}>
            <option value="">Choose a colleague…</option>
            {copyable.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>
          <button className="button button-outline" type="button" disabled={busy || !copyFrom}
            onClick={() => void send({ kind: "copy_person", user_id: Number(copyFrom) }, `${show(team.name)} now has at least the permissions of the colleague chosen, in every unit.`).then((ok) => ok && setCopyFrom(""))}>Copy their permissions</button>
        </div>}
      </div>}
      <p className="sp-hint">Approval roles (salesperson up to CEO) stay personal and are set on each person.</p>
    </section>

    <section className="ua-block" aria-label="Recent changes">
      <h4>Recent changes</h4>
      {detail.history.length === 0
        ? <p className="sp-empty">No changes recorded yet.</p>
        : <ul className="ua-history">{detail.history.map((row, index) => <li key={`${row.at}-${index}`}>
          <time>{when(row.at)}</time> {row.by}: <strong>{row.change}</strong> {show(row.what)}
        </li>)}</ul>}
    </section>
  </article>;
}

export default function Teams({ csrfToken, people, onChanged }: { csrfToken: string; people: Summary[]; onChanged: () => void }) {
  const [teams, setTeams] = useState<TeamRow[] | null>(null);
  const [detail, setDetail] = useState<TeamDetail | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try {
      setTeams((await call<{ teams: TeamRow[] }>("/api/governance/teams/")).teams);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Teams could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const open = async (id: number) => {
    setNotice(""); setAdding(false);
    try {
      setDetail(await call<TeamDetail>(`/api/governance/teams/${id}/`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The team could not be opened.");
    }
  };
  const changed = (next: TeamDetail, message: string) => { setDetail(next); setNotice(message); void load(); onChanged(); };
  return <>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!teams && !error && <p className="sp-empty" role="status">Loading teams…</p>}
    {teams && <div className="ua-layout">
      <aside className="ua-people" aria-label="Teams">
        <button className="button button-dark ua-add-button" type="button" onClick={() => { setAdding(true); setDetail(null); setNotice(""); }}>Make a team</button>
        <ul>{teams.map((row) => <li key={row.id}>
          <button type="button" className={detail?.team.id === row.id ? "ua-row ua-row-on" : "ua-row"} onClick={() => void open(row.id)}>
            <strong>{show(row.name)}</strong>
            <small>{row.members} {row.members === 1 ? "member" : "members"} · {row.permissions} {row.permissions === 1 ? "permission" : "permissions"}{!row.is_active && " · closed"}</small>
            {row.description && <small>{row.description}</small>}
          </button>
        </li>)}</ul>
        {teams.length === 0 && <p className="sp-empty">No teams yet.</p>}
      </aside>
      <div className="ua-main">
        {adding && <NewTeam csrfToken={csrfToken} onCancel={() => setAdding(false)}
          onAdded={(next) => { setAdding(false); changed(next, `${show(next.team.name)} is made. Add its members and switch on its permissions below.`); }} />}
        {!adding && detail && <Team key={detail.team.id} detail={detail} people={people} csrfToken={csrfToken} onChanged={changed} />}
        {!adding && !detail && <p className="sp-empty">Choose a team to see who is in it and what they may do, or make a new one.</p>}
      </div>
    </div>}
  </>;
}

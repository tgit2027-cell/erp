import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./user-admin.css";
import Teams from "./Teams";
import { call, show, when } from "./user-admin-shared";

export type Summary = {
  id: number; username: string; name: string; email: string; is_active: boolean; is_superuser: boolean; is_staff: boolean;
  last_login: string; units: string[]; permission_count: number; role_count: number; level: string; level_label: string;
};
export type People = { people: Summary[]; units: { id: number; code: string; name: string }[]; me: number; is_master_admin: boolean };
type LevelChoice = { level: string; label: string; summary: string; per_unit: boolean };
type AccessLevel = {
  level: string; label: string; based_on: string; unit_ids: number[]; choices: LevelChoice[];
  assignable: string[]; can_edit: boolean; can_customize: boolean;
};
type Toggle = { action: string; label: string; hint?: string };
type Detail = {
  person: Summary; is_me: boolean; access_level: AccessLevel;
  groups: { name: string; actions: Toggle[] }[];
  roles: { role: string; label: string }[];
  units: { id: number; code: string; name: string; actions: string[]; from_teams: Record<string, string[]>; roles: string[] }[];
  company: { action: string; label: string; on: boolean; from_teams: string[] }[];
  teams: { id: number; name: string; on: boolean; is_active: boolean }[];
  reporting_roles: { id: number; name: string; on: boolean; manages_panels: boolean }[];
  report_panels: string[];
  chat_roles: { role: string; label: string; on: boolean }[];
  capacity: { location: string; access: string }[];
  history: { at: string; by: string; what: string; change: string }[];
  /** The areas of the ERP the person sees (Sam, 5 Nov 2026). Older servers leave it out. */
  areas?: {
    choices: { key: string; name: string; kind: string; description: string; coming_soon: boolean }[];
    chosen: string[]; all: boolean; seen: string[]; source: "all" | "chosen" | "from_permissions";
  };
};
type Change = Record<string, string | number | boolean | number[]>;

const PER_UNIT = ["head", "manager", "sales"];
const LEVEL_NAMES: Record<string, string> = {
  master_admin: "Master Admin", general_admin: "General Admin", finance_admin: "Finance Admin", hr_admin: "HR Admin",
  head: "Head", manager: "Manager", sales: "Sales", custom: "Custom",
};

function UnitPicker({ units, chosen, onChange }: {
  units: People["units"]; chosen: number[]; onChange: (next: number[]) => void;
}) {
  return <fieldset className="ua-level-units">
    <legend>In which business units?</legend>
    {units.map((unit) => <label key={unit.id} className="bc-check">
      <input type="checkbox" checked={chosen.includes(unit.id)}
        onChange={(event) => onChange(event.target.checked ? [...chosen, unit.id] : chosen.filter((id) => id !== unit.id))} />
      <span>{unit.name}</span>
    </label>)}
  </fieldset>;
}

function AddPerson({ csrfToken, units, isMaster, onAdded, onCancel }: {
  csrfToken: string; units: People["units"]; isMaster: boolean; onAdded: (detail: Detail) => void; onCancel: () => void;
}) {
  const [form, setForm] = useState({ username: "", first_name: "", last_name: "", email: "", password: "" });
  const [level, setLevel] = useState("");
  const [unitIds, setUnitIds] = useState<number[]>([]);
  const levels = isMaster ? Object.keys(LEVEL_NAMES).filter((key) => key !== "custom") : PER_UNIT;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      onAdded(await call<Detail>("/api/governance/people/", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ ...form, level, unit_ids: unitIds }),
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The person could not be added.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form ua-add" onSubmit={submit} aria-label="Add a person">
    <h3>Add a person</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Username<input value={form.username} onChange={set("username")} maxLength={150} required autoComplete="off" /></label>
      <label>First name<input value={form.first_name} onChange={set("first_name")} maxLength={150} /></label>
      <label>Last name<input value={form.last_name} onChange={set("last_name")} maxLength={150} /></label>
      <label>Email<input type="email" value={form.email} onChange={set("email")} maxLength={254} /></label>
      <label>Temporary password<input type="password" value={form.password} onChange={set("password")} required autoComplete="new-password" /></label>
      <label>Access level<select value={level} onChange={(event) => setLevel(event.target.value)}>
        <option value="">No access yet (choose later)</option>
        {levels.map((key) => <option key={key} value={key}>{LEVEL_NAMES[key]}</option>)}
      </select></label>
    </div>
    {PER_UNIT.includes(level) && <UnitPicker units={units} chosen={unitIds} onChange={setUnitIds} />}
    <p className="sp-hint">Give the person their temporary password in person or by phone, not by email.
      {isMaster ? " To make a custom user, add them with no access, then switch on what they need below." : " Only a Master Admin can make admins or custom users."}</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Add person</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

const fromTeams = (teams: string[], own: boolean) =>
  `${own ? "Also given by" : "Given by"} team: ${teams.map(show).join(", ")}`;

function onCount(row: Detail["units"][number]) {
  return new Set([...row.actions, ...Object.keys(row.from_teams)]).size + row.roles.length;
}

function LevelPicker({ detail, units, busy, onApply }: {
  detail: Detail; units: People["units"]; busy: boolean; onApply: (level: string, unitIds: number[]) => void;
}) {
  const current = detail.access_level;
  const [level, setLevel] = useState(current.level || "");
  const [unitIds, setUnitIds] = useState<number[]>(current.unit_ids);
  const choice = current.choices.find((row) => row.level === level);
  const options = current.choices.filter((row) => current.assignable.includes(row.level));
  const replaces = level && level !== "custom";
  return <section className="ua-block ua-level" aria-label="Access level">
    <h4>Access level</h4>
    <p className="ua-level-now"><span className="ua-badge">{current.label}</span>
      {current.based_on && <small> started from {LEVEL_NAMES[current.based_on] ?? current.based_on}, then changed one by one</small>}
      {current.unit_ids.length > 0 && <small> in {units.filter((unit) => current.unit_ids.includes(unit.id)).map((unit) => unit.name).join(", ")}</small>}
    </p>
    {!current.can_edit && <p className="sp-hint">Only a Master Admin can change admins and custom users.</p>}
    {current.can_edit && <>
      <label className="ua-level-select">Change to<select value={level} disabled={busy} onChange={(event) => setLevel(event.target.value)}>
        {!current.level && <option value="">Choose a level…</option>}
        {options.map((row) => <option key={row.level} value={row.level}>{row.label}</option>)}
      </select></label>
      {choice && <p className="sp-hint">{choice.summary}</p>}
      {choice?.per_unit && <UnitPicker units={units} chosen={unitIds} onChange={setUnitIds} />}
      {replaces && <p className="sp-hint">Applying a level replaces the permissions given to this person directly. Team permissions stay.</p>}
      <div className="sp-actions">
        <button className="button button-dark" type="button" disabled={busy || !level || (choice?.per_unit && unitIds.length === 0)}
          onClick={() => onApply(level, choice?.per_unit ? unitIds : [])}>Apply level</button>
      </div>
    </>}
  </section>;
}

function Person({ detail, people, csrfToken, onChanged }: {
  detail: Detail; people: People; csrfToken: string; onChanged: (detail: Detail, message: string) => void;
}) {
  const [unitId, setUnitId] = useState((detail.units.find((row) => onCount(row) > 0) ?? detail.units[0])?.id ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [password, setPassword] = useState<string | null>(null);
  const [copyFrom, setCopyFrom] = useState("");
  const person = detail.person;
  const canCustomize = detail.access_level.can_customize;
  const canEdit = detail.access_level.can_edit;
  const unit = detail.units.find((row) => row.id === unitId) ?? detail.units[0];
  const send = async (body: Change, message: string) => {
    setBusy(true); setError("");
    try {
      const result = await call<Detail & { done?: boolean }>(`/api/governance/people/${person.id}/`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
      });
      onChanged(result.done ? detail : result, message);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The change could not be saved.");
      return false;
    } finally { setBusy(false); }
  };
  const toggle = (label: string, change: Change, on: boolean) =>
    void send({ ...change, enabled: on }, `${label} ${on ? "switched on" : "switched off"} for ${person.name}.`);
  const savePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (await send({ password: password ?? "" }, `A new temporary password is set for ${person.name}. Pass it on in person.`)) setPassword(null);
  };
  return <article className="ua-person" aria-label={`Permissions for ${person.name}`}>
    <header className="ua-person-head">
      <div>
        <h3>{person.name}<small>{person.username}{person.email && ` · ${person.email}`}{person.last_login ? ` · last signed in ${when(person.last_login)}` : " · never signed in"}</small></h3>
        <div className="ua-badges">
          <span className={person.is_active ? "sp-gain" : "sp-loss"}>{person.is_active ? "Active" : "Switched off"}</span>
          <span className="ua-badge">{person.level_label}</span>
          {person.is_staff && !person.is_superuser && <span className="ua-badge">Staff</span>}
        </div>
      </div>
      <div className="sp-actions">
        <button className="button button-outline" type="button" disabled={busy || detail.is_me || !canEdit}
          title={detail.is_me ? "You cannot switch off your own account." : undefined}
          onClick={() => toggle("The account", { kind: "active" }, !person.is_active)}>{person.is_active ? "Switch account off" : "Switch account on"}</button>
        <button className="button button-outline" type="button" disabled={busy || !canEdit} onClick={() => setPassword(password === null ? "" : null)}>Set a temporary password</button>
      </div>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {password !== null && <form className="sp-form ua-password" onSubmit={savePassword} aria-label="Set a temporary password">
      <label>New temporary password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" required /></label>
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Save password</button></div>
    </form>}
    <LevelPicker key={`${detail.access_level.level}-${detail.access_level.unit_ids.join(",")}`} detail={detail} units={people.units} busy={busy}
      onApply={(level, unitIds) => void send({ kind: "level", level, unit_ids: unitIds },
        `${person.name} is now on the ${LEVEL_NAMES[level] ?? level} level.`)} />
    {detail.areas && <section className="ua-block" aria-label="Areas">
      <h4>Areas this person sees</h4>
      <p className="sp-hint">{person.is_superuser
        ? "The Master Admin always sees every area."
        : detail.areas.source === "from_permissions"
          ? "Nothing is ticked yet, so these areas follow from the person's permissions. Ticking or unticking one saves this list as the person's areas."
          : "Only these areas show on the person's home page and menu. Areas tidy the screen; permissions still decide what the person can do."}</p>
      <label className="bc-check">
        <input type="checkbox" checked={detail.areas.all} disabled={busy || !canEdit || person.is_superuser}
          onChange={(event) => toggle("All areas", { kind: "area", area: "all" }, event.target.checked)} />
        <span>All areas</span>
      </label>
      <div className="ua-area-grid">
        {detail.areas.choices.map((item) => <label key={item.key} className="bc-check" title={item.description}>
          <input type="checkbox" checked={detail.areas!.seen.includes(item.key)}
            disabled={busy || !canEdit || person.is_superuser || detail.areas!.all}
            onChange={(event) => toggle(`The ${item.name} area`, { kind: "area", area: item.key }, event.target.checked)} />
          <span>{item.name}{item.kind === "shared" ? " (shared)" : ""}{item.coming_soon ? " · coming soon" : ""}</span>
        </label>)}
      </div>
    </section>}
    {!canCustomize && <p className="sp-hint">The permissions below are shown for reference. Only a Master Admin can switch them on or off one by one.</p>}
    {canCustomize && <p className="sp-hint">Switching a permission on or off below makes this person a custom user.</p>}

    <section className="ua-block" aria-label="Business units">
      <h4>Business units</h4>
      <div className="ua-tabs" role="tablist">
        {detail.units.map((row) => <button key={row.id} type="button" role="tab" aria-selected={row.id === unit?.id}
          className={row.id === unit?.id ? "ua-tab ua-tab-on" : "ua-tab"} onClick={() => setUnitId(row.id)}>
          {row.name}<small>{onCount(row) ? `${onCount(row)} on` : "none"}</small>
        </button>)}
      </div>
      {unit && <div className="ua-unit">
        {detail.groups.map((group) => <fieldset key={group.name} className="ua-group" disabled={busy || !canCustomize}>
          <legend>{group.name}</legend>
          {group.actions.map((item) => {
            const own = unit.actions.includes(item.action);
            const teams = unit.from_teams[item.action] ?? [];
            return <label key={item.action} className="bc-check">
              <input type="checkbox" checked={own || teams.length > 0} disabled={!own && teams.length > 0}
                onChange={(event) => toggle(`${item.label} in ${unit.code}`, { kind: "action", unit_id: unit.id, action: item.action }, event.target.checked)} />
              <span>{item.label}{teams.length > 0 && <small className="ua-from-team">{fromTeams(teams, own)}</small>}{item.hint && <small>{item.hint}</small>}</span>
            </label>;
          })}
        </fieldset>)}
        <fieldset className="ua-group" disabled={busy || !canCustomize}>
          <legend>Approval roles</legend>
          {detail.roles.map((item) => <label key={item.role} className="bc-check">
            <input type="checkbox" checked={unit.roles.includes(item.role)}
              onChange={(event) => toggle(`${item.label} in ${unit.code}`, { kind: "role", unit_id: unit.id, role: item.role }, event.target.checked)} />
            <span>{item.label}</span>
          </label>)}
        </fieldset>
        {canCustomize && <div className="ua-copy">
          <label>Give the same access in {unit.name} as<select value={copyFrom} onChange={(event) => setCopyFrom(event.target.value)}>
            <option value="">Choose a colleague…</option>
            {people.people.filter((row) => row.id !== person.id).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>
          <button className="button button-outline" type="button" disabled={busy || !copyFrom}
            onClick={() => void send({ copy_from: Number(copyFrom), unit_id: unit.id }, `${person.name} now has at least the same access in ${unit.name} as the colleague chosen.`).then((ok) => ok && setCopyFrom(""))}>Copy access</button>
        </div>}
      </div>}
    </section>

    <div className="ua-columns">
      <fieldset className="ua-group" disabled={busy || !canCustomize}>
        <legend>Teams</legend>
        {detail.teams.length === 0 && <p className="sp-hint">No teams yet. Make one under the Teams tab.</p>}
        {detail.teams.map((item) => <label key={item.id} className="bc-check">
          <input type="checkbox" checked={item.on} disabled={!item.is_active && !item.on}
            onChange={(event) => toggle(`Membership of ${show(item.name)}`, { kind: "team", team_id: item.id }, event.target.checked)} />
          <span>{show(item.name)}{!item.is_active && <small>Closed</small>}</span>
        </label>)}
        {detail.teams.length > 0 && <p className="sp-hint">Members get every permission the team has, and its team chat.</p>}
      </fieldset>
      <fieldset className="ua-group" disabled={busy || !canCustomize}>
        <legend>Whole company</legend>
        {detail.company.map((item) => <label key={item.action} className="bc-check">
          <input type="checkbox" checked={item.on || item.from_teams.length > 0} disabled={!item.on && item.from_teams.length > 0}
            onChange={(event) => toggle(item.label, { kind: "action", action: item.action }, event.target.checked)} />
          <span>{item.label}{item.from_teams.length > 0 && <small className="ua-from-team">{fromTeams(item.from_teams, item.on)}</small>}</span>
        </label>)}
      </fieldset>
      <fieldset className="ua-group" disabled={busy || !canCustomize}>
        <legend>Reporting</legend>
        {detail.reporting_roles.length === 0 && <p className="sp-hint">No reporting roles are set up.</p>}
        {detail.reporting_roles.map((item) => <label key={item.id} className="bc-check">
          <input type="checkbox" checked={item.on} onChange={(event) => toggle(`Reporting role ${show(item.name)}`, { kind: "reporting_role", role_id: item.id }, event.target.checked)} />
          <span>{show(item.name)}{item.manages_panels && <small>Can manage report panels</small>}</span>
        </label>)}
        {detail.report_panels.length > 0 && <p className="sp-hint">Report panels given directly: {detail.report_panels.join(", ")}.</p>}
      </fieldset>
      <fieldset className="ua-group" disabled={busy || !canCustomize}>
        <legend>Chat reviews</legend>
        {detail.chat_roles.map((item) => <label key={item.role} className="bc-check">
          <input type="checkbox" checked={item.on} onChange={(event) => toggle(item.label, { kind: "chat_role", role: item.role }, event.target.checked)} />
          <span>{item.label}</span>
        </label>)}
      </fieldset>
      <div className="ua-group">
        <h5>Warehouse capacity</h5>
        {detail.capacity.length === 0
          ? <p className="sp-hint">No warehouse capacity access.</p>
          : <ul className="ua-list">{detail.capacity.map((row) => <li key={`${row.location}-${row.access}`}>{show(row.location)}: {row.access.toLowerCase()}</li>)}</ul>}
        <p className="sp-hint">Changed under Capacity locations and assignments.</p>
      </div>
    </div>

    <section className="ua-block" aria-label="Recent changes">
      <h4>Recent changes</h4>
      {detail.history.length === 0
        ? <p className="sp-empty">No changes recorded yet.</p>
        : <ul className="ua-history">{detail.history.map((row, index) => <li key={`${row.at}-${index}`}>
          <time>{when(row.at)}</time> {row.by}: <strong>{row.change}</strong> {row.what}
        </li>)}</ul>}
    </section>
  </article>;
}

export default function UserAdmin({ csrfToken }: { csrfToken: string }) {
  const [people, setPeople] = useState<People | null>(null);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState<"people" | "teams">("people");

  const load = useCallback(async (term: string) => {
    try {
      setPeople(await call<People>(`/api/governance/people/?search=${encodeURIComponent(term)}`));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "People could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(""); }, [load]);
  const open = async (id: number) => {
    setNotice(""); setAdding(false);
    try {
      setDetail(await call<Detail>(`/api/governance/people/${id}/`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The person could not be opened.");
    }
  };
  const changed = (next: Detail, message: string) => { setDetail(next); setNotice(message); void load(search); };

  return <section className="content-card supplier-payments user-admin" aria-labelledby="user-admin-title">
    <header className="sp-head">
      <span className="eyebrow">GOVERNANCE · USERS AND PERMISSIONS · TEST ONLY</span>
      <h2 id="user-admin-title">Users and permissions</h2>
      <p>Everyone who can sign in, and everything each person is allowed to do, in one place.</p>
    </header>
    <div className="ua-views" role="tablist" aria-label="People or teams">
      {(people ? ["people", "teams"] as const : ["people"] as const).map((key) => <button key={key} type="button" role="tab" aria-selected={view === key}
        className={view === key ? "ua-tab ua-tab-on" : "ua-tab"} onClick={() => { setView(key); setNotice(""); if (key === "people" && detail) void open(detail.person.id); }}>
        {key === "people" ? "People" : "Teams"}
      </button>)}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && view === "people" && <p className="sp-notice" role="status">{notice}</p>}
    {view === "teams" && people && <Teams csrfToken={csrfToken} people={people.people} onChanged={() => void load(search)} />}
    {view === "people" && !people && !error && <p className="sp-empty" role="status">Loading people…</p>}
    {view === "people" && people && <div className="ua-layout">
      <aside className="ua-people" aria-label="People">
        <form className="ua-search" onSubmit={(event) => { event.preventDefault(); void load(search); }}>
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, username or email" aria-label="Search people" />
        </form>
        <button className="button button-dark ua-add-button" type="button" onClick={() => { setAdding(true); setDetail(null); setNotice(""); }}>Add a person</button>
        <ul>{people.people.map((row) => <li key={row.id}>
          <button type="button" className={detail?.person.id === row.id ? "ua-row ua-row-on" : "ua-row"} onClick={() => void open(row.id)}>
            <strong>{row.name}</strong>
            <small>{row.level_label}{!row.is_active && " · switched off"}</small>
            <small>{row.units.length ? row.units.join(", ") : "No business unit access"}</small>
          </button>
        </li>)}</ul>
        {people.people.length === 0 && <p className="sp-empty">Nobody matches.</p>}
      </aside>
      <div className="ua-main">
        {adding && <AddPerson csrfToken={csrfToken} units={people.units} isMaster={people.is_master_admin} onCancel={() => setAdding(false)}
          onAdded={(next) => { setAdding(false); changed(next, next.person.level
            ? `${next.person.name} was added on the ${next.person.level_label} level.`
            : `${next.person.name} was added with no access yet. Choose a level below.`); }} />}
        {!adding && detail && <Person key={detail.person.id} detail={detail} people={people} csrfToken={csrfToken} onChanged={changed} />}
        {!adding && !detail && <p className="sp-empty">Choose a person to see and change what they may do.</p>}
      </div>
    </div>}
  </section>;
}

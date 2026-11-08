import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Letters from "../src/Letters";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const letter = (id: number, extra = {}) => ({
  id, employee: { id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00012" }, kind: "loan", kind_label: "Loan certificate (financing)",
  addressee: "TEST ONLY QNB, Doha", purpose: "", include_salary: true, include_iban: true, status: "issued", status_label: "Ready to download",
  number: `HRL-TVL-2026-000${id}`, requested_by: "Aisha", requested_at: "2026-11-02T08:00:00", decided_by: "Issued automatically",
  decided_at: "2026-11-02T08:00:00", rejection_reason: "", downloads: 0, missing: [], can_decide: false, can_download: true, ...extra,
});
const kinds = [
  { value: "bank", label: "Bank letter (account or credit)", needs_addressee: true, addressee: "", iban: true },
  { value: "loan", label: "Loan certificate (financing)", needs_addressee: true, addressee: "", iban: true },
  { value: "to_whom", label: "To whom it may concern", needs_addressee: false, addressee: "To whom it may concern", iban: false },
  { value: "embassy", label: "Embassy letter", needs_addressee: true, addressee: "", iban: false },
];
let current: () => Record<string, unknown>;
const staff = () => ({
  unit: null, me: { id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00012", job_title: "Consultant", missing: [] },
  mine: [letter(1)], to_issue: [], recent: [], people: [], kinds,
  settings: { company_name: "TEST ONLY Tivoli", address: "", signatory_name: "TEST ONLY Hana", signatory_title: "HR Manager", has_signature: true,
    hr_checks_first: false, set_by: "Admin", set_at: "2026-11-01T09:00:00" },
  can_issue: false, policy: "TEST ONLY policy",
});
const hr = () => ({
  ...staff(), unit: { id: 7, name: "Tivoli" }, me: null, mine: [], people: [{ id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00012" }],
  to_issue: [letter(2, { kind: "to_whom", kind_label: "To whom it may concern", status: "requested", status_label: "Waiting for HR", number: "",
    decided_by: "", can_decide: true, can_download: false, missing: ["passport"] }),
  letter(3, { kind: "embassy", kind_label: "Embassy letter", status: "requested", status_label: "Waiting for HR", number: "", decided_by: "",
    can_decide: true, can_download: false })],
  recent: [letter(1, { downloads: 2 })], can_issue: true, settings: null,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.trim().startsWith(label))?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement;
function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}
const posts = () => calls.filter((call) => call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  current = staff;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/letters/" && init?.method === "POST") return json(letter(4, { kind: "embassy", kind_label: "Embassy letter" }), 201);
    if (url.pathname === "/api/hr/letters/") return json(current());
    if (url.pathname === "/api/hr/letters/1/pdf/") {
      return new Response("%PDF-1.4 synthetic", { headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="HRL-TVL-2026-0001.pdf"' } });
    }
    if (url.pathname === "/api/hr/letters/settings/") return json({});
    if (/^\/api\/hr\/letters\/\d+\/$/.test(url.pathname)) return json(letter(3));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("staff ask for a letter with the addressee it needs, and download it", async () => {
  const oldCreate = URL.createObjectURL;
  const oldRevoke = URL.revokeObjectURL;
  const oldClick = HTMLAnchorElement.prototype.click;
  const downloads: string[] = [];
  URL.createObjectURL = () => "blob:synthetic";
  URL.revokeObjectURL = () => {};
  HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
  try {
    act(() => root.render(<Letters selectedUnitId={null} csrfToken="csrf" />));
    await flush();
    assert.equal(field("For"), undefined, "Staff ask only for themselves");
    assert.equal(button("Letter set-up"), undefined);
    assert.match(container.querySelector("[aria-label='My letters']")?.textContent ?? "", /Loan certificate.*QNB, Doha · with IBANAsked on|Ready to downloadHRL-TVL-2026-0001 · Issued automatically/);

    await act(async () => setValue(field("Letter"), "embassy"));
    assert.ok(button("Ask for the letter")!.disabled, "The embassy is needed first");
    assert.equal(container.textContent?.includes("Show the bank account"), false, "No IBAN on an embassy letter");
    await act(async () => {
      setValue(field("Addressed to"), "TEST ONLY Embassy of the Philippines, Doha");
      setValue(field("What it is for"), "a visit visa");
    });
    await act(async () => (container.querySelector("form[aria-label='Ask for a letter']") as HTMLFormElement).requestSubmit());
    await flush();
    assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { kind: "embassy", addressee: "TEST ONLY Embassy of the Philippines, Doha",
      purpose: "a visit visa", include_salary: true, include_iban: false });
    assert.match(container.querySelector("[role=status]")?.textContent ?? "", /HRL-TVL-2026-0004 for Aisha Rahman is ready/);

    await act(async () => button("Download PDF")!.click());
    await flush();
    assert.deepEqual(downloads, ["HRL-TVL-2026-0001.pdf"]);
  } finally {
    URL.createObjectURL = oldCreate;
    URL.revokeObjectURL = oldRevoke;
    HTMLAnchorElement.prototype.click = oldClick;
  }
});

test("a loan letter can show the IBAN", async () => {
  act(() => root.render(<Letters selectedUnitId={null} csrfToken="csrf" />));
  await flush();
  await act(async () => setValue(field("Letter"), "loan"));
  assert.match(container.textContent ?? "", /no liability for any loan/);
  await act(async () => {
    setValue(field("Addressed to"), "TEST ONLY Doha Bank");
    (field("Show the bank account") as HTMLInputElement).click();
  });
  await act(async () => (container.querySelector("form[aria-label='Ask for a letter']") as HTMLFormElement).requestSubmit());
  await flush();
  assert.equal(JSON.parse(String(posts()[0].init?.body)).include_iban, true);
});

test("HR sets up the letters, issues or refuses a waiting one", async () => {
  current = hr;
  act(() => root.render(<Letters selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /Letters are not set up yet/);
  const waiting = container.querySelector("[aria-label='Waiting for HR']")!;
  assert.match(waiting.textContent ?? "", /The record has no passport\./);
  const issueButtons = [...waiting.querySelectorAll("button")].filter((node) => node.textContent === "Issue") as HTMLButtonElement[];
  assert.deepEqual(issueButtons.map((node) => node.disabled), [true, false], "Cannot issue while the record is missing something");
  await act(async () => issueButtons[1].click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/letters/3/");
  assert.equal(JSON.parse(String(posts()[0].init?.body)).action, "issue");

  await act(async () => button("Do not issue")!.click());
  await act(async () => setValue(container.querySelector("input[aria-label='Why it is not issued']") as HTMLInputElement, "TEST ONLY add the passport"));
  await act(async () => button("Confirm")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { action: "reject", reason: "TEST ONLY add the passport" });

  await act(async () => button("Letter set-up")!.click());
  await act(async () => {
    setValue(field("Company name"), "TEST ONLY Tivoli Group");
    setValue(field("Signed by"), "TEST ONLY Hana Saleh");
    setValue(field("Job title"), "HR Manager");
  });
  await act(async () => (container.querySelector("form[aria-label='Letter set-up']") as HTMLFormElement).requestSubmit());
  await flush();
  const sent = JSON.parse(String(posts()[2].init?.body));
  assert.equal(posts()[2].url.pathname, "/api/hr/letters/settings/");
  assert.deepEqual([sent.company_name, sent.signatory_name, sent.hr_checks_first, "signature" in sent], ["TEST ONLY Tivoli Group", "TEST ONLY Hana Saleh", false, false]);
  assert.match(container.querySelector("[aria-label='Letters issued in this unit']")?.textContent ?? "", /downloaded 2×/);
});

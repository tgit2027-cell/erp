import { useEffect, useRef, useState } from "react";
import "./retail-collections.css";
import "./scenario-reconciliation.css";

type Check = {
  area: string;
  label: string;
  unit: "QAR" | "count" | "quantity";
  expected: string;
  actual: string;
  difference: string;
  matches: boolean;
};
type Report = {
  business_unit: { id: number; code: string; name: string };
  currency: string;
  summary: Record<string, string | number>;
  checks: Check[];
  all_match: boolean;
  policy_label: string;
};
type State =
  | { status: "loading" }
  | { status: "ready"; data: Report }
  | { status: "error"; error: string; denied: boolean };

const AREAS: [string, string][] = [
  ["receipts", "Receipts and landed cost"],
  ["stock", "Stock"],
  ["deliveries", "Deliveries"],
  ["invoices", "Invoices"],
  ["payments", "Payments"],
  ["journals", "Journals"],
];
const SUMMARY: [string, string][] = [
  ["supplier_value", "Supplier value received"],
  ["landed_charges", "Eligible landed charges"],
  ["received_value", "Stock value received"],
  ["stock_value", "Stock value on hand"],
  ["revenue", "Revenue at delivery"],
  ["cost_of_goods_sold", "Cost of goods sold"],
  ["gross_margin", "Gross margin"],
  ["invoiced_total", "Invoiced"],
  ["collected_on_invoices", "Collected on invoices"],
  ["still_to_collect", "Still to collect"],
  ["deposits_held", "Deposits held before delivery"],
];

export default function ScenarioReconciliation({ selectedUnitId, selectedUnitName }: { selectedUnitId: number; selectedUnitName: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [retry, setRetry] = useState(0);
  const version = useRef(0);

  useEffect(() => {
    const current = ++version.current;
    setState({ status: "loading" });
    fetch(`/api/reports/scenario-reconciliation/?business_unit_id=${selectedUnitId}`, {
      method: "GET", credentials: "same-origin", headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (current !== version.current) return;
        if (!response.ok) {
          setState({
            status: "error",
            error: typeof body?.detail === "string" ? body.detail : `Request unavailable (${response.status}).`,
            denied: [401, 403, 404].includes(response.status),
          });
          return;
        }
        setState({ status: "ready", data: body as Report });
      })
      .catch(() => {
        if (current === version.current) setState({ status: "error", error: "The reconciliation is unavailable.", denied: false });
      });
  }, [selectedUnitId, retry]);

  const format = (check: Check, value: string) => check.unit === "QAR" ? `QAR ${value}` : value;

  return (
    <section className="retail-collections scenario-reconciliation" aria-labelledby="scenario-reconciliation-title">
      <header className="rc-heading">
        <div>
          <span className="rc-kicker">REPORTS · STAFF ONLY · READ ONLY</span>
          <h2 id="scenario-reconciliation-title">Scenario reconciliation</h2>
          <p>Checks that purchases, stock, deliveries, invoices and payments agree with the journal in <strong>{selectedUnitName}</strong>.</p>
        </div>
        <button type="button" className="rc-button" onClick={() => setRetry((value) => value + 1)} disabled={state.status === "loading"}>Run again</button>
      </header>

      {state.status === "loading" && <div className="rc-loading" role="status" aria-label="Loading reconciliation"><i /><i /><i /></div>}
      {state.status === "error" && <div className="rc-message rc-error" role={state.denied ? "status" : "alert"}>
        <strong>{state.denied ? "Reconciliation not authorized" : "Reconciliation unavailable"}</strong>
        <p>{state.denied ? "Only staff accounts can see this report because it shows cost and margin." : state.error}</p>
      </div>}
      {state.status === "ready" && <>
        <div className={`sr-verdict ${state.data.all_match ? "is-ok" : "is-off"}`} role="status">
          <strong>{state.data.all_match ? "Everything reconciles" : "Some figures do not agree"}</strong>
          <span>{state.data.checks.filter((check) => check.matches).length} of {state.data.checks.length} checks match.</span>
        </div>
        <dl className="sr-summary">
          {SUMMARY.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>QAR {state.data.summary[key]}</dd></div>)}
        </dl>
        {AREAS.map(([area, title]) => {
          const checks = state.data.checks.filter((check) => check.area === area);
          if (!checks.length) return null;
          return (
            <section key={area} className="sr-area" aria-labelledby={`sr-${area}`}>
              <h3 id={`sr-${area}`}>{title}</h3>
              <div className="rc-table-scroll">
                <table className="rc-table sr-table">
                  <thead><tr><th scope="col">Check</th><th scope="col">Expected</th><th scope="col">Recorded</th><th scope="col">Difference</th><th scope="col">Result</th></tr></thead>
                  <tbody>{checks.map((check) => (
                    <tr key={check.label} className={check.matches ? "" : "sr-off"}>
                      <th scope="row">{check.label}</th>
                      <td className="rc-number">{format(check, check.expected)}</td>
                      <td className="rc-number">{format(check, check.actual)}</td>
                      <td className="rc-number">{format(check, check.difference)}</td>
                      <td><span className={`sr-badge ${check.matches ? "is-ok" : "is-off"}`}>{check.matches ? "Matches" : "Mismatch"}</span></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </section>
          );
        })}
        <small className="rc-test-label">{state.data.policy_label}</small>
      </>}
    </section>
  );
}

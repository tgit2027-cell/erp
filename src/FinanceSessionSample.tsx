import { FinanceDraftWorkspace } from "./FinanceDraftWorkspace";

// Same explicit session-only sample as before; not persisted lab records.
export function FinanceSessionSample() {
  return <FinanceDraftWorkspace
    legalEntityLabel="TEST ONLY — synthetic Finance review entity"
    businessUnits={[
      { code: "TEST-BU-A", nameEn: "Synthetic unit A", nameAr: "وحدة تجريبية أ" },
      { code: "TEST-BU-B", nameEn: "Synthetic unit B", nameAr: "وحدة تجريبية ب" },
    ]}
  />;
}

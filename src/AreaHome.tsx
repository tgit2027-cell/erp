// The landing page's area tiles (Sam, 5 Nov 2026): each person sees only the
// areas allocated to them, and a tile opens that area's first section.

export type MyArea = {
  key: string;
  name: string;
  kind: "business" | "shared";
  description: string;
  sections: string[];
  coming_soon: boolean;
  unit: { id: number; code: string; name: string } | null;
};

export type MyAreas = { areas: MyArea[]; sections: string[]; source: "all" | "chosen" | "from_permissions" };

const MARKS: Record<string, string> = {
  furniture: "TF", "roche-bobois": "RB", natuzzi: "NA", factory: "FA", projects: "PF", smart: "SM",
  finance: "FI", warehouse: "WH", logistics: "LI", purchasing: "PU", hr: "HR", setup: "ST",
};

function Tile({ area, current, onOpen }: { area: MyArea; current: boolean; onOpen: (area: MyArea) => void }) {
  return (
    <button
      type="button"
      className={`area-tile area-${area.kind}${current ? " area-current" : ""}`}
      onClick={() => onOpen(area)}
      disabled={area.coming_soon}
      data-area={area.key}
    >
      <span className="area-mark" aria-hidden="true">{MARKS[area.key] ?? area.name.slice(0, 2).toUpperCase()}</span>
      <span className="area-text">
        <strong>{area.name}</strong>
        <small>{area.description}</small>
      </span>
      {area.coming_soon ? <span className="area-tag">Coming soon</span> : <span className="area-arrow" aria-hidden="true">→</span>}
    </button>
  );
}

export default function AreaHome({ areas, selectedUnitId, onOpen }: {
  areas: MyAreas;
  selectedUnitId?: number;
  onOpen: (area: MyArea) => void;
}) {
  const business = areas.areas.filter((area) => area.kind === "business");
  const shared = areas.areas.filter((area) => area.kind === "shared");
  if (!areas.areas.length) {
    return (
      <section className="area-home" aria-label="Your areas">
        <h2>Your areas</h2>
        <p className="empty-state">No area of the ERP is open to you yet. Ask your admin to give you the areas you work in.</p>
      </section>
    );
  }
  return (
    <section className="area-home" aria-label="Your areas">
      <div className="section-title-row">
        <div>
          <h2>Your areas</h2>
          <p>{areas.source === "all" ? "You see every part of the ERP." : "Only the parts of the ERP you work in. Choose one to open it."}</p>
        </div>
      </div>
      {business.length > 0 && <>
        <h3 className="area-group-label">Business</h3>
        <div className="area-grid">
          {business.map((area) => <Tile key={area.key} area={area} current={Boolean(area.unit && area.unit.id === selectedUnitId)} onOpen={onOpen} />)}
        </div>
      </>}
      {shared.length > 0 && <>
        <h3 className="area-group-label">Shared</h3>
        <div className="area-grid">
          {shared.map((area) => <Tile key={area.key} area={area} current={false} onOpen={onOpen} />)}
        </div>
      </>}
    </section>
  );
}

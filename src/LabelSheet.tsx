import { useState } from "react";
import { createPortal } from "react-dom";
import { Barcode, canEncode } from "./Barcode";
import "./labels.css";

export type LabelItem = {
  variant_id: number; item_code: string; sku: string; description: string; variant_description: string;
  name_ar?: string; selling_price: string; brand: string;
};
export type LabelChoice = { item: LabelItem; copies: number };

const SIZES = {
  small: { label: "Small, 50 × 25 mm (shelf and box)", width: 50, height: 25 },
  tag: { label: "Showroom tag, 100 × 50 mm", width: 100, height: 50 },
} as const;
type Size = keyof typeof SIZES;
const MAX_COPIES = 200;

const price = (value: string) => value ? `QAR ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}` : "";

function Label({ item, size, withPrice }: { item: LabelItem; size: Size; withPrice: boolean }) {
  const tag = size === "tag";
  return <div className={`label label-${size}`} style={{ width: `${SIZES[size].width}mm`, height: `${SIZES[size].height}mm` }}>
    <div className="label-text">
      <strong>{item.description}</strong>
      {tag && item.name_ar && <span className="label-ar" dir="rtl" lang="ar">{item.name_ar}</span>}
      <span>{item.variant_description}{tag && item.brand ? ` · ${item.brand}` : ""}</span>
    </div>
    {withPrice && item.selling_price && <b className="label-price">{price(item.selling_price)}</b>}
    <Barcode value={item.sku} height={tag ? 34 : 26} label={`Barcode ${item.sku}`} />
    <span className="label-code">{item.sku}<small>{item.item_code}</small></span>
  </div>;
}

/** Choose copies and size, preview, and print. Printing shows only the labels. */
export default function LabelSheet({ choices, onChange, onClose }: {
  choices: LabelChoice[]; onChange: (choices: LabelChoice[]) => void; onClose: () => void;
}) {
  const [size, setSize] = useState<Size>("small");
  const [withPrice, setWithPrice] = useState(true);
  const printable = choices.filter((choice) => canEncode(choice.item.sku));
  const total = printable.reduce((sum, choice) => sum + choice.copies, 0);
  const setCopies = (variantId: number, value: string) => {
    const copies = Math.max(0, Math.min(MAX_COPIES, Math.floor(Number(value) || 0)));
    onChange(choices.map((choice) => choice.item.variant_id === variantId ? { ...choice, copies } : choice));
  };
  const labels = printable.flatMap((choice) => Array.from({ length: choice.copies }, (_, index) =>
    <Label key={`${choice.item.variant_id}-${index}`} item={choice.item} size={size} withPrice={withPrice} />));
  return <section className="label-sheet" aria-labelledby="label-sheet-title">
    <div className="label-controls">
      <h3 id="label-sheet-title">Print labels <small>{total} {total === 1 ? "label" : "labels"}</small></h3>
      <p>Each label carries a barcode of the item's SKU, so scanning it finds the exact item and finish. Use label paper of the chosen size, or plain A4 and cut.</p>
      <div className="label-options">
        <label>Label size<select value={size} onChange={(event) => setSize(event.target.value as Size)}>
          {Object.entries(SIZES).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
        </select></label>
        <label className="label-check"><input type="checkbox" checked={withPrice} onChange={(event) => setWithPrice(event.target.checked)} /> Show the selling price</label>
      </div>
      <table className="label-copies" aria-label="Labels to print">
        <thead><tr><th scope="col">Item</th><th scope="col">Copies</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>{choices.map((choice) => <tr key={choice.item.variant_id}>
          <th scope="row">{choice.item.sku}<small>{choice.item.description} · {choice.item.variant_description}</small>
            {!canEncode(choice.item.sku) && <small className="label-warning">This SKU has characters a barcode cannot hold.</small>}</th>
          <td><input aria-label={`Copies of ${choice.item.sku}`} type="number" min={0} max={MAX_COPIES} value={choice.copies}
            onChange={(event) => setCopies(choice.item.variant_id, event.target.value)} /></td>
          <td><button type="button" className="button button-outline ls-small" onClick={() => onChange(choices.filter((each) => each !== choice))}>Remove</button></td>
        </tr>)}</tbody>
      </table>
      <div className="label-buttons">
        <button className="button button-dark" type="button" disabled={total === 0} onClick={() => window.print()}>Print {total} {total === 1 ? "label" : "labels"}</button>
        <button className="button button-outline" type="button" onClick={onClose}>Close</button>
      </div>
    </div>
    <div className="label-print-area" aria-label="Label preview">{labels}</div>
    {typeof document !== "undefined" && createPortal(<div className="label-print-root" aria-hidden="true">{labels}</div>, document.body)}
  </section>;
}

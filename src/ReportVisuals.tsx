import { useState, type ReactNode } from "react";
import { useReportVisual } from "./reporting-api";
import type { ReportFilters, ReportPanelCode, ReportVisual, VisualChart } from "./reporting-api";
import "./report-visuals.css";

// Categorical slots in fixed order (validated: adjacent CVD ΔE ≥ 9 on white). A chart never
// carries more than three series; the table view relieves the lower-contrast third slot.
const SERIES = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)"];

const W = 560;
const H = 240;
const PAD = { top: 16, right: 12, bottom: 34, left: 52 };

function compact(value: number) {
  const size = Math.abs(value);
  if (size >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (size >= 1_000) return `${+(value / 1_000).toFixed(1)}K`;
  return `${+value.toFixed(1)}`;
}

export function formatValue(value: number, unit: string) {
  if (unit === "%") return `${value.toFixed(1)}%`;
  const text = value.toLocaleString("en-US", { minimumFractionDigits: unit === "QAR" ? 2 : 0, maximumFractionDigits: 2 });
  return unit === "QAR" ? `${text} QAR` : unit === "count" ? text : `${text} ${unit}`;
}

function ticks(min: number, max: number) {
  const span = max - min || 1;
  const raw = span / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= raw) ?? raw;
  const low = Math.floor(min / step) * step;
  const high = Math.ceil(max / step) * step;
  const values = [];
  for (let value = low; value <= high + step / 2; value += step) values.push(+value.toFixed(10));
  return values;
}

function domain(chart: VisualChart) {
  const numbers = chart.series.map((series) => series.values.map(Number));
  const totals = chart.stacked
    ? chart.categories.map((_, index) => numbers.reduce((sum, values) => sum + Math.max(0, values[index] ?? 0), 0))
    : numbers.flat();
  const values = ticks(Math.min(0, ...totals, ...numbers.flat()), Math.max(0, ...totals));
  return { values, min: values[0], max: values[values.length - 1] };
}

function Legend({ chart }: { chart: VisualChart }) {
  if (chart.series.length < 2) return null;
  return (
    <ul className="viz-legend">
      {chart.series.map((series, index) => <li key={series.key}><span className="viz-key" style={{ background: SERIES[index] }} />{series.label}</li>)}
    </ul>
  );
}

function Tooltip({ chart, index, left }: { chart: VisualChart; index: number; left: number }) {
  return (
    <div className="viz-tooltip" role="status" style={{ left: `${Math.min(Math.max(left, 18), 82)}%` }}>
      <strong>{chart.categories[index]}</strong>
      {chart.series.map((series, slot) => <span key={series.key}>
        {chart.series.length > 1 && <i className="viz-key" style={{ background: SERIES[slot] }} />}
        {series.label}: {formatValue(Number(series.values[index]), chart.unit)}
      </span>)}
    </div>
  );
}

function PlotFrame({ chart, children }: { chart: VisualChart; children: (scale: { x: (index: number) => number; band: number; y: (value: number) => number }) => ReactNode }) {
  const { values, min, max } = domain(chart);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const band = plotW / Math.max(chart.categories.length, 1);
  const y = (value: number) => PAD.top + plotH - ((value - min) / (max - min || 1)) * plotH;
  const x = (index: number) => PAD.left + band * index;
  const every = Math.ceil(chart.categories.length / 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg" role="img" aria-label={`${chart.title}. Values are in the table view.`}>
      {values.map((value) => <g key={value}>
        <line x1={PAD.left} x2={W - PAD.right} y1={y(value)} y2={y(value)} className={value === 0 ? "viz-baseline" : "viz-grid"} />
        <text x={PAD.left - 8} y={y(value)} className="viz-tick" textAnchor="end" dominantBaseline="middle">{chart.unit === "%" ? `${value}%` : compact(value)}</text>
      </g>)}
      {chart.categories.map((label, index) => index % every === 0 && (
        <text key={label} x={x(index) + band / 2} y={H - PAD.bottom + 16} className="viz-tick" textAnchor="middle">{label.length > 12 ? `${label.slice(0, 11)}…` : label}</text>
      ))}
      {children({ x, band, y })}
    </svg>
  );
}

function ColumnChart({ chart }: { chart: VisualChart }) {
  const [hover, setHover] = useState<number | null>(null);
  const groups = chart.stacked ? 1 : chart.series.length;
  return (
    <div className="viz-plot" onMouseLeave={() => setHover(null)}>
      <PlotFrame chart={chart}>{({ x, band, y }) => {
        const width = Math.min(24, (band * 0.7 - (groups - 1) * 2) / groups);
        const groupWidth = width * groups + (groups - 1) * 2;
        return chart.categories.map((_, index) => {
          let stackTop = 0;
          return (
            <g key={index} className={hover !== null && hover !== index ? "viz-dim" : undefined}>
              {chart.series.map((series, slot) => {
                const value = Number(series.values[index]);
                const base = chart.stacked ? stackTop : 0;
                const top = base + value;
                if (chart.stacked) stackTop = top;
                const left = x(index) + (band - groupWidth) / 2 + (chart.stacked ? 0 : slot * (width + 2));
                const y1 = y(Math.max(base, top));
                const y0 = y(Math.min(base, top));
                // 2px surface gap between stacked segments; only the top segment gets the rounded end.
                const gap = chart.stacked && slot > 0 ? 2 : 0;
                const height = Math.max(0, y0 - y1 - gap);
                const rounded = !chart.stacked || slot === chart.series.length - 1;
                return height > 0 && <path key={series.key} fill={SERIES[slot]} d={columnPath(left, y1, width, height, rounded && value > 0 ? 4 : 0)} />;
              })}
              <rect x={x(index)} y={PAD.top} width={band} height={H - PAD.top - PAD.bottom} fill="transparent"
                onMouseEnter={() => setHover(index)} onFocus={() => setHover(index)} tabIndex={0}
                aria-label={`${chart.categories[index]}: ${chart.series.map((series) => `${series.label} ${formatValue(Number(series.values[index]), chart.unit)}`).join(", ")}`} />
            </g>
          );
        });
      }}</PlotFrame>
      {hover !== null && <Tooltip chart={chart} index={hover} left={((PAD.left + (W - PAD.left - PAD.right) / chart.categories.length * (hover + 0.5)) / W) * 100} />}
    </div>
  );
}

function columnPath(x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.min(radius, width / 2, height);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
}

function LineChart({ chart }: { chart: VisualChart }) {
  const [hover, setHover] = useState<number | null>(null);
  return (
    <div className="viz-plot" onMouseLeave={() => setHover(null)}>
      <PlotFrame chart={chart}>{({ x, band, y }) => <>
        {chart.series.map((series, slot) => {
          const points = series.values.map((value, index) => `${x(index) + band / 2},${y(Number(value))}`);
          const last = series.values.length - 1;
          return <g key={series.key}>
            <polyline points={points.join(" ")} fill="none" stroke={SERIES[slot]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {series.values.map((value, index) => (index === last || index === hover || series.values.length === 1) && (
              <circle key={index} cx={x(index) + band / 2} cy={y(Number(value))} r={4} fill={SERIES[slot]} stroke="var(--viz-surface)" strokeWidth={2} />
            ))}
          </g>;
        })}
        {hover !== null && <line className="viz-crosshair" x1={x(hover) + band / 2} x2={x(hover) + band / 2} y1={PAD.top} y2={H - PAD.bottom} />}
        {chart.categories.map((label, index) => <rect key={label} x={x(index)} y={PAD.top} width={band} height={H - PAD.top - PAD.bottom}
          fill="transparent" tabIndex={0} onMouseEnter={() => setHover(index)} onFocus={() => setHover(index)}
          aria-label={`${label}: ${chart.series.map((series) => formatValue(Number(series.values[index]), chart.unit)).join(", ")}`} />)}
      </>}</PlotFrame>
      {hover !== null && <Tooltip chart={chart} index={hover} left={((PAD.left + (W - PAD.left - PAD.right) / chart.categories.length * (hover + 0.5)) / W) * 100} />}
    </div>
  );
}

function BarChart({ chart }: { chart: VisualChart }) {
  const max = Math.max(0, ...chart.series.flatMap((series) => series.values.map(Number))) || 1;
  return (
    <ol className="viz-bars">
      {chart.categories.map((label, index) => <li key={`${label}-${index}`}>
        <span className="viz-bar-label" title={label}>{label}</span>
        <span className="viz-bar-tracks">
          {chart.series.map((series, slot) => {
            const value = Number(series.values[index]);
            return <span key={series.key} className="viz-bar-row" title={`${series.label}: ${formatValue(value, chart.unit)}`}>
              <span className="viz-bar" style={{ width: `${Math.max(0, value) / max * 100}%`, background: SERIES[slot] }} />
              <span className="viz-bar-value">{formatValue(value, chart.unit)}</span>
            </span>;
          })}
        </span>
      </li>)}
    </ol>
  );
}

function ChartTable({ chart }: { chart: VisualChart }) {
  return (
    <details className="viz-table">
      <summary>Show as table</summary>
      <div className="viz-table-scroll"><table>
        <thead><tr><th scope="col">{chart.kind === "bar" ? "Name" : "Period"}</th>{chart.series.map((series) => <th scope="col" key={series.key}>{series.label}</th>)}</tr></thead>
        <tbody>{chart.categories.map((label, index) => <tr key={`${label}-${index}`}>
          <th scope="row">{label}</th>{chart.series.map((series) => <td key={series.key}>{formatValue(Number(series.values[index]), chart.unit)}</td>)}
        </tr>)}</tbody>
      </table></div>
    </details>
  );
}

export function VisualChartCard({ chart }: { chart: VisualChart }) {
  return (
    <figure className={`viz-chart viz-kind-${chart.kind}`} data-chart={chart.id}>
      <figcaption>{chart.title}</figcaption>
      <Legend chart={chart} />
      {!chart.categories.length
        ? <p className="viz-empty">No data for this period.</p>
        : chart.kind === "bar" ? <BarChart chart={chart} />
          : chart.kind === "line" ? <LineChart chart={chart} /> : <ColumnChart chart={chart} />}
      {chart.note && <p className="viz-note">{chart.note}</p>}
      {chart.categories.length > 0 && <ChartTable chart={chart} />}
    </figure>
  );
}

export function VisualPanel({ visual }: { visual: ReportVisual }) {
  return (
    <>
      <div className="viz-kpis">
        {visual.kpis.map((kpi) => <div key={kpi.label} className="viz-kpi"><small>{kpi.label}</small><strong>{formatValue(Number(kpi.value), kpi.unit)}</strong></div>)}
      </div>
      <div className="viz-charts">{visual.charts.map((chart) => <VisualChartCard key={chart.id} chart={chart} />)}</div>
    </>
  );
}

function PanelCard({ panel, label, filters }: { panel: ReportPanelCode; label: string; filters: ReportFilters }) {
  const visual = useReportVisual(panel, filters);
  return (
    <section className="viz-panel" aria-labelledby={`viz-${panel}`} data-visual-panel={panel}>
      <header><h3 id={`viz-${panel}`}>{label}</h3>{visual.data && <small>{visual.data.row_count} records</small>}</header>
      {visual.loading && <div className="report-skeleton" role="status" aria-label={`Loading ${label}`}><span /><span /></div>}
      {!visual.loading && visual.error && <p className="viz-denied" role="status">{visual.error}</p>}
      {!visual.loading && visual.data && <VisualPanel visual={visual.data} />}
    </section>
  );
}

export function VisualDashboard({ panels, filters }: {
  panels: Array<{ code: ReportPanelCode; label: string }>;
  filters: ReportFilters;
}) {
  return (
    <div className="viz-dashboard">
      <p className="viz-intro">Charts use the same data and permissions as the standard reports. Hover or tab to a chart for exact values, or open “Show as table”. A panel without data access for this scope says so.</p>
      {panels.map((panel) => <PanelCard key={panel.code} panel={panel.code} label={panel.label} filters={filters} />)}
    </div>
  );
}

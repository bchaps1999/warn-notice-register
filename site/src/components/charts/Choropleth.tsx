import { useMemo, useState } from "react";
import { geoAlbersUsa, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import type { FeatureCollection, Geometry } from "geojson";
import statesTopo from "us-atlas/states-10m.json";
import { useNavigate } from "react-router-dom";
import { num } from "../../lib/format";

// FIPS -> postal for us-atlas state ids
const FIPS: Record<string, string> = {
  "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
  "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
  "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
  "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
  "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
  "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
  "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
  "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
  "54": "WV", "55": "WI", "56": "WY",
};

// Sequential oxide ramp, light -> dark (monotonic lightness), per mode via CSS.
const RAMP_LIGHT = ["#f3e2d8", "#e5bda9", "#d4977c", "#c07052", "#a84a2e", "#8c2f1b"];
const RAMP_DARK = ["#3a2018", "#5a3021", "#7c422b", "#9e5535", "#c06a42", "#e08154"];

export function Choropleth({
  values,
  activeStates,
  label,
}: {
  values: Record<string, number>; // postal -> workers (trailing 12mo)
  activeStates: Set<string>;
  label: string;
}) {
  const navigate = useNavigate();
  const [hover, setHover] = useState<{ postal: string; x: number; y: number } | null>(null);
  const dark = document.documentElement.classList.contains("dark");
  const ramp = dark ? RAMP_DARK : RAMP_LIGHT;

  const { features, path } = useMemo(() => {
    const topo = statesTopo as unknown as {
      objects: { states: Parameters<typeof feature>[1] };
    } & Parameters<typeof feature>[0];
    const fc = feature(topo, topo.objects.states) as unknown as FeatureCollection<
      Geometry,
      { name: string }
    >;
    const projection = geoAlbersUsa().fitSize([900, 520], fc);
    return { features: fc.features, path: geoPath(projection) };
  }, []);

  const max = Math.max(1, ...Object.values(values));
  const bucket = (v: number) =>
    ramp[Math.min(ramp.length - 1, Math.floor((v / max) ** 0.5 * ramp.length))];

  return (
    <div className="relative">
      <svg viewBox="0 0 900 520" role="img" aria-label={label} className="w-full h-auto">
        <defs>
          <pattern id="inactive-hatch" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="var(--color-surface)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--color-rule)" strokeWidth="1.5" />
          </pattern>
        </defs>
        {features.map((f) => {
          const postal = FIPS[String(f.id).padStart(2, "0")];
          if (!postal) return null;
          const active = activeStates.has(postal);
          const v = values[postal] ?? 0;
          return (
            <path
              key={postal}
              d={path(f) ?? undefined}
              fill={active ? bucket(v) : "url(#inactive-hatch)"}
              stroke="var(--color-bg)"
              strokeWidth={1}
              className={active ? "cursor-pointer" : undefined}
              onClick={active ? () => navigate(`/states/${postal.toLowerCase()}`) : undefined}
              onMouseMove={(e) => {
                const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                setHover({ postal, x: e.clientX - box.left, y: e.clientY - box.top });
              }}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}
      </svg>
      {hover && (
        <div
          className="absolute pointer-events-none border border-rule bg-surface px-2.5 py-1.5 text-xs tabular z-10"
          style={{ left: hover.x + 12, top: hover.y - 8 }}
        >
          <span className="smallcaps text-[10px] text-ink-muted mr-2">{hover.postal}</span>
          {activeStates.has(hover.postal)
            ? `${num(values[hover.postal] ?? 0)} workers`
            : "no automated coverage"}
        </div>
      )}
      <div className="flex items-center gap-3 mt-2">
        <span className="text-[10px] smallcaps text-ink-muted">{label}</span>
        <div className="flex">
          {ramp.map((c) => (
            <div key={c} style={{ background: c }} className="w-6 h-2" />
          ))}
        </div>
        <span className="tabular text-[10px] text-ink-faint">0 → {num(max)}</span>
        <span className="text-[10px] text-ink-faint flex items-center gap-1.5 ml-2">
          <svg width="14" height="10"><rect width="14" height="10" fill="url(#inactive-hatch)" stroke="var(--color-rule)" /></svg>
          not covered
        </span>
      </div>
    </div>
  );
}

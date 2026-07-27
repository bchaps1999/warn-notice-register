import { useMemo, useState } from "react";
import { geoAlbersUsa, geoPath } from "d3-geo";
import { feature, mesh } from "topojson-client";
import type { FeatureCollection, Geometry } from "geojson";
import countiesTopo from "us-atlas/counties-10m.json";
import { num } from "../../lib/format";
import { RAMP_DARK, RAMP_LIGHT } from "./ramp";

/**
 * Workers by county.
 *
 * A county with no shading has no *placed* notices, which is not the same as
 * no layoffs: a state that files against workforce investment areas rather
 * than towns cannot be mapped at all. The caller says so above the map; here
 * an unplaced county simply reads as zero, and state outlines are drawn over
 * the top so an entirely blank state is visible as such.
 */
export function CountyChoropleth({
  values,
  label,
  onSelect,
}: {
  values: Record<string, number>; // county FIPS -> workers
  label: string;
  onSelect?: (fips: string) => void;
}) {
  const [hover, setHover] = useState<
    { fips: string; name: string; x: number; y: number } | null
  >(null);
  const dark = document.documentElement.classList.contains("dark");
  const ramp = dark ? RAMP_DARK : RAMP_LIGHT;

  const { features, path, borders } = useMemo(() => {
    const topo = countiesTopo as unknown as {
      objects: {
        counties: Parameters<typeof feature>[1];
        states: Parameters<typeof mesh>[1];
      };
    } & Parameters<typeof feature>[0];
    const fc = feature(topo, topo.objects.counties) as unknown as FeatureCollection<
      Geometry,
      { name: string }
    >;
    const projection = geoAlbersUsa().fitSize([900, 520], fc);
    const draw = geoPath(projection);
    return {
      features: fc.features,
      path: draw,
      borders: draw(mesh(topo, topo.objects.states, (a, b) => a !== b)) ?? undefined,
    };
  }, []);

  const max = Math.max(1, ...Object.values(values));
  const bucket = (v: number) =>
    ramp[Math.min(ramp.length - 1, Math.floor((v / max) ** 0.5 * ramp.length))];

  return (
    <div className="relative">
      <svg viewBox="0 0 900 520" role="img" aria-label={label} className="w-full h-auto">
        {features.map((f) => {
          const fips = String(f.id).padStart(5, "0");
          const v = values[fips] ?? 0;
          return (
            <path
              key={fips}
              d={path(f) ?? undefined}
              fill={v > 0 ? bucket(v) : "var(--color-surface)"}
              stroke="var(--color-rule)"
              strokeWidth={0.2}
              className={v > 0 && onSelect ? "cursor-pointer" : undefined}
              onClick={v > 0 && onSelect ? () => onSelect(fips) : undefined}
              onMouseMove={(e) => {
                const box = (
                  e.currentTarget.ownerSVGElement as SVGSVGElement
                ).getBoundingClientRect();
                setHover({
                  fips,
                  name: f.properties?.name ?? fips,
                  x: e.clientX - box.left,
                  y: e.clientY - box.top,
                });
              }}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}
        <path d={borders} fill="none" stroke="var(--color-bg)" strokeWidth={0.8} />
      </svg>
      {hover && (
        <div
          className="absolute pointer-events-none border border-rule bg-surface px-2.5 py-1.5 text-xs tabular z-10"
          style={{ left: hover.x + 12, top: hover.y - 8 }}
        >
          <span className="smallcaps text-[10px] text-ink-muted mr-2">{hover.name}</span>
          {(values[hover.fips] ?? 0) > 0
            ? `${num(values[hover.fips])} workers`
            : "none placed here"}
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
      </div>
    </div>
  );
}

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MonthPoint } from "../../lib/types";
import { monthLabel, num } from "../../lib/format";
import clsx from "clsx";

const RANGES = [
  { label: "1 yr", months: 12 },
  { label: "5 yr", months: 60 },
  { label: "10 yr", months: 120 },
  { label: "All", months: Infinity },
] as const;

type Metric = "notices" | "workers";

export function MonthlyTrend({ monthly, anchor }: { monthly: MonthPoint[]; anchor: string }) {
  const [range, setRange] = useState(60);
  const [metric, setMetric] = useState<Metric>("notices");

  const data = useMemo(() => {
    const cutoff = cutoffMonth(anchor, range);
    const ceiling = anchor.slice(0, 7);
    return monthly
      .filter((m) => m.month >= cutoff && m.month <= ceiling)
      .map((m) => ({
        month: m.month,
        closure: m.by_type.closure,
        mass_layoff: m.by_type.mass_layoff,
        unknown: m.by_type.unknown,
        workers: m.workers,
      }));
  }, [monthly, anchor, range]);

  const yearly = range > 120;

  return (
    <div>
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="flex gap-px border border-rule">
          {(["notices", "workers"] as Metric[]).map((m) => (
            <Toggle key={m} active={metric === m} onClick={() => setMetric(m)}>
              {m === "notices" ? "Notices" : "Workers affected"}
            </Toggle>
          ))}
        </div>
        <div className="flex gap-px border border-rule">
          {RANGES.map((r) => (
            <Toggle
              key={r.label}
              active={range === r.months}
              onClick={() => setRange(r.months)}
            >
              {r.label}
            </Toggle>
          ))}
        </div>
      </div>
      <div className="h-72">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }} barCategoryGap="18%">
            <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
            <XAxis
              dataKey="month"
              tickFormatter={(m: string) => (yearly ? m.slice(0, 4) : monthLabel(m))}
              tick={{ fill: "var(--color-ink-muted)", fontSize: 11, fontFamily: "Inter Variable" }}
              tickLine={false}
              axisLine={{ stroke: "var(--color-rule)" }}
              minTickGap={40}
            />
            <YAxis
              tickFormatter={(v: number) => num(v)}
              tick={{ fill: "var(--color-ink-muted)", fontSize: 11, fontFamily: "Inter Variable" }}
              tickLine={false}
              axisLine={false}
              width={54}
            />
            <Tooltip
              cursor={{ fill: "var(--color-rule)", opacity: 0.35 }}
              contentStyle={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-rule)",
                fontFamily: "Inter Variable",
                fontSize: 12,
              }}
              labelFormatter={(m) => monthLabel(String(m))}
              formatter={(value, name) => [num(Number(value ?? 0)), LABEL[String(name)] ?? String(name)]}
            />
            {metric === "notices" ? (
              <>
                <Legend
                  formatter={(v: string) => (
                    <span style={{ color: "var(--color-ink-muted)", fontSize: 11 }}>
                      {LABEL[v] ?? v}
                    </span>
                  )}
                  iconSize={9}
                />
                <Bar dataKey="closure" stackId="n" fill="var(--chart-closure)"
                     stroke="var(--color-bg)" strokeWidth={1} />
                <Bar dataKey="mass_layoff" stackId="n" fill="var(--chart-layoff)"
                     stroke="var(--color-bg)" strokeWidth={1} />
                <Bar dataKey="unknown" stackId="n" fill="var(--chart-unknown)"
                     stroke="var(--color-bg)" strokeWidth={1} radius={[3, 3, 0, 0]} />
              </>
            ) : (
              <Bar dataKey="workers" fill="var(--color-federal)" radius={[3, 3, 0, 0]} />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const LABEL: Record<string, string> = {
  closure: "Closures",
  mass_layoff: "Mass layoffs",
  unknown: "Unspecified",
  workers: "Workers affected",
};

function Toggle({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "smallcaps text-[10px] px-2.5 py-1 transition-colors",
        active ? "bg-ink text-bg" : "text-ink-muted hover:text-ink"
      )}
      style={active ? { background: "var(--color-ink)", color: "var(--color-bg)" } : undefined}
    >
      {children}
    </button>
  );
}

function cutoffMonth(anchor: string, months: number): string {
  if (!isFinite(months)) return "0000";
  const y = parseInt(anchor.slice(0, 4));
  const m = parseInt(anchor.slice(5, 7));
  const total = y * 12 + (m - 1) - months;
  return `${String(Math.floor(total / 12)).padStart(4, "0")}-${String((total % 12) + 1).padStart(2, "0")}`;
}

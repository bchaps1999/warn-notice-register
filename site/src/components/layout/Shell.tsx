import { Link, NavLink, Outlet } from "react-router-dom";
import { useMeta } from "../../lib/hooks";
import { date, num } from "../../lib/format";
import { ThemeToggle } from "./ThemeToggle";
import clsx from "clsx";

const NAV = [
  { to: "/", label: "Register" },
  { to: "/explore", label: "Explorer" },
  { to: "/employers", label: "Employers" },
  { to: "/states", label: "States" },
  { to: "/methods", label: "Methods" },
];

export function Shell() {
  const { data: meta } = useMeta();
  return (
    <div className="min-h-screen flex flex-col">
      <header className="px-6 pt-8 pb-0 max-w-6xl w-full mx-auto">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="smallcaps text-[11px] text-ink-muted mb-2">
              Worker Adjustment and Retraining Notification Act · Consolidated Notices
            </p>
            <Link to="/" className="block">
              <h1 className="font-display text-4xl sm:text-5xl font-semibold tracking-tight">
                WARN Notice Register
              </h1>
            </Link>
          </div>
          <ThemeToggle />
        </div>
        <div className="double-rule mt-4" />
        <div className="flex items-center justify-between py-2.5">
          <nav className="flex gap-6">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  clsx(
                    "smallcaps text-xs py-0.5 border-b-2 transition-colors",
                    isActive
                      ? "border-oxide text-ink"
                      : "border-transparent text-ink-muted hover:text-ink"
                  )
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          {meta && (
            <p className="tabular text-[11px] text-ink-faint hidden sm:block">
              Compiled {date(meta.built_at.slice(0, 10))} · {num(meta.totals.notices)} notices ·{" "}
              {meta.totals.states} states
            </p>
          )}
        </div>
        <div className="border-t border-rule" />
      </header>

      <main className="px-6 py-8 max-w-6xl w-full mx-auto flex-1">
        <Outlet />
      </main>

    </div>
  );
}

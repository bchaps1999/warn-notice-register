import clsx from "clsx";

const TONE: Record<string, string> = {
  closure: "border-[var(--chart-closure)] text-[var(--chart-closure)]",
  mass_layoff: "border-[var(--chart-layoff)] text-[var(--chart-layoff)]",
  unknown: "border-ink-faint text-ink-faint",
  neutral: "border-ink-muted text-ink-muted",
  oxide: "border-oxide text-oxide",
};

export function Stamp({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: string;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "smallcaps inline-block border px-1.5 py-px text-[9px] leading-4 whitespace-nowrap",
        TONE[tone] ?? TONE.neutral,
        className
      )}
    >
      {children}
    </span>
  );
}

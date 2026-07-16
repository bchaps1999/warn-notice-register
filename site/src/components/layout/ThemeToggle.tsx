import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark")
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);
  return (
    <button
      onClick={() => setDark((d) => !d)}
      className="smallcaps text-[10px] border border-rule px-2.5 py-1.5 hover:border-ink-muted transition-colors"
      aria-label="Toggle color theme"
    >
      {dark ? "Day edition" : "Night edition"}
    </button>
  );
}

import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <div className="py-16 text-center">
      <p className="smallcaps text-xs text-ink-muted">Document not found</p>
      <h2 className="font-display text-3xl mt-2">No such record in the register</h2>
      <Link to="/" className="inline-block mt-6 smallcaps text-[11px] text-oxide hover:underline">
        Return to front page →
      </Link>
    </div>
  );
}

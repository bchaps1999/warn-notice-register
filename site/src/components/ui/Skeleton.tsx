export function Skeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div className="animate-pulse space-y-3 py-4" aria-busy>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="h-4 bg-rule/60" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p className="border border-oxide/40 text-oxide px-3 py-2 text-sm font-serif my-4">
      {message}
    </p>
  );
}

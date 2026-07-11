export default function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="text-[13.5px] font-semibold">{label}</div>
      <div className="mt-3 flex h-32 items-center justify-center rounded-lg bg-app/70">
        <span className="text-4xl font-semibold tabular-nums tracking-tight">{value}</span>
      </div>
    </div>
  );
}

export default function Logo() {
  return (
    <div className="flex items-center gap-2 px-2">
      {/* geometric mark: three stacked chevrons forming an "A" */}
      <svg viewBox="0 0 24 24" className="size-5" aria-hidden>
        <path d="M12 2L2 22h5l5-10 5 10h5L12 2z" fill="#17181C" />
        <path d="M12 13l-3.2 6.4h6.4L12 13z" fill="#3B82F6" />
      </svg>
      <span className="text-[17px] font-semibold tracking-tight">Architeq</span>
    </div>
  );
}

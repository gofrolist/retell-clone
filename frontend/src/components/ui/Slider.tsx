"use client";

export default function Slider({
  label,
  min = 0,
  max = 1,
  step = 0.01,
  value,
  onChange,
  format,
  leftHint,
  rightHint,
}: {
  label?: string;
  min?: number;
  max?: number;
  step?: number;
  value: number;
  onChange?: (v: number) => void;
  format?: (v: number) => string;
  leftHint?: string;
  rightHint?: string;
}) {
  return (
    <div className="w-full">
      {label && (
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[13px] font-medium text-ink">{label}</span>
          <span className="text-[13px] tabular-nums text-sub">
            {format ? format(value) : value}
          </span>
        </div>
      )}
      <div className="flex items-center gap-3">
        <input
          type="range"
          className="w-full"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange?.(parseFloat(e.target.value))}
        />
        {!label && (
          <span className="w-12 text-right text-[13px] tabular-nums text-sub shrink-0">
            {format ? format(value) : value}
          </span>
        )}
      </div>
      {(leftHint || rightHint) && (
        <div className="mt-1 flex justify-between text-xs text-faint">
          <span>{leftHint}</span>
          <span>{rightHint}</span>
        </div>
      )}
    </div>
  );
}

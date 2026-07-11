"use client";

import Slider from "@/components/ui/Slider";
import Toggle from "@/components/ui/Toggle";

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
  title,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange?: (v: boolean) => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <div className={disabled ? "flex items-start justify-between gap-4 opacity-50" : "flex items-start justify-between gap-4"} title={title}>
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {hint && <p className="text-xs text-sub">{hint}</p>}
      </div>
      <Toggle checked={checked} onChange={onChange} disabled={disabled} />
    </div>
  );
}

export default function CallSettingsSection({
  voicemail,
  onVoicemail,
  endCallAfterSilenceMs,
  onEndCallAfterSilenceMs,
  maxCallDurationMs,
  onMaxCallDurationMs,
}: {
  voicemail: boolean;
  onVoicemail: (v: boolean) => void;
  endCallAfterSilenceMs: number;
  onEndCallAfterSilenceMs: (v: number) => void;
  maxCallDurationMs: number;
  onMaxCallDurationMs: (v: number) => void;
}) {
  return (
    <div className="space-y-5">
      <ToggleRow
        label="Voicemail Detection"
        hint="Hang up or leave a message when reaching voicemail."
        checked={voicemail}
        onChange={onVoicemail}
      />
      <ToggleRow
        label="iOS/Android Call Screen Handling"
        hint="Detect and respond to smartphone call screening."
        checked={false}
        disabled
        title="Not available yet"
      />
      <ToggleRow
        label="IVR Hangup"
        hint="Hang up when an IVR system is detected."
        checked={false}
        disabled
        title="Not available yet"
      />
      <ToggleRow
        label="User Keypad Input Detection"
        hint="Capture DTMF keypad input from the user."
        checked={false}
        disabled
        title="Not available yet"
      />
      <Slider
        label="End Call on Silence"
        min={10}
        max={600}
        step={10}
        value={Math.round(endCallAfterSilenceMs / 1000)}
        onChange={(v) => onEndCallAfterSilenceMs(v * 1000)}
        format={(v) => `${v}s`}
      />
      <Slider
        label="Max Call Duration"
        min={1}
        max={120}
        step={1}
        value={Math.round(maxCallDurationMs / 60000)}
        onChange={(v) => onMaxCallDurationMs(v * 60000)}
        format={(v) => `${v} min`}
      />
    </div>
  );
}

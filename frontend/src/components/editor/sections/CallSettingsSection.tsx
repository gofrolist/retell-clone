"use client";

import Select from "@/components/ui/Select";
import Slider from "@/components/ui/Slider";
import Toggle from "@/components/ui/Toggle";
import type { UserDtmfOptions } from "@/lib/api";

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

const TERMINATION_KEYS = ["#", "*", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

export default function CallSettingsSection({
  voicemail,
  onVoicemail,
  endCallAfterSilenceMs,
  onEndCallAfterSilenceMs,
  maxCallDurationMs,
  onMaxCallDurationMs,
  callScreening,
  onCallScreening,
  ivrHangup,
  onIvrHangup,
  allowUserDtmf,
  onAllowUserDtmf,
  userDtmfOptions,
  onUserDtmfOptions,
}: {
  voicemail: boolean;
  onVoicemail: (v: boolean) => void;
  endCallAfterSilenceMs: number;
  onEndCallAfterSilenceMs: (v: number) => void;
  maxCallDurationMs: number;
  onMaxCallDurationMs: (v: number) => void;
  callScreening: boolean;
  onCallScreening: (v: boolean) => void;
  ivrHangup: boolean;
  onIvrHangup: (v: boolean) => void;
  allowUserDtmf: boolean;
  onAllowUserDtmf: (v: boolean) => void;
  userDtmfOptions: UserDtmfOptions | null;
  onUserDtmfOptions: (v: UserDtmfOptions | null) => void;
}) {
  const dtmf = userDtmfOptions ?? {};
  const patchDtmf = (patch: Partial<UserDtmfOptions>) => {
    const next = { ...dtmf, ...patch };
    // Drop cleared keys so an all-empty object saves as null.
    const cleaned = Object.fromEntries(
      Object.entries(next).filter(([, v]) => v !== null && v !== undefined && v !== ""),
    );
    onUserDtmfOptions(Object.keys(cleaned).length ? cleaned : null);
  };

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
        hint="Hang up when a smartphone call screen answers instead of the user."
        checked={callScreening}
        onChange={onCallScreening}
      />
      <ToggleRow
        label="IVR Hangup"
        hint="Hang up when an IVR system is detected."
        checked={ivrHangup}
        onChange={onIvrHangup}
      />
      <ToggleRow
        label="User Keypad Input Detection"
        hint="Capture DTMF keypad input from the user."
        checked={allowUserDtmf}
        onChange={onAllowUserDtmf}
      />
      {allowUserDtmf && (
        <div className="space-y-4 rounded-lg border border-line bg-app/50 p-3">
          <Slider
            label="Termination Timeout"
            min={1}
            max={15}
            step={0.5}
            value={(dtmf.timeout_ms ?? 2500) / 1000}
            onChange={(v) => patchDtmf({ timeout_ms: Math.round(v * 1000) })}
            format={(v) => `${v}s`}
          />
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[13px] font-medium">Termination Key</div>
              <p className="text-xs text-sub">Key that ends keypad input immediately.</p>
            </div>
            <Select
              value={dtmf.termination_key ?? "none"}
              onChange={(v) => patchDtmf({ termination_key: v === "none" ? null : v })}
              options={[
                { value: "none", label: "None" },
                ...TERMINATION_KEYS.map((k) => ({ value: k, label: k })),
              ]}
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[13px] font-medium">Digit Limit</div>
              <p className="text-xs text-sub">Stop after this many digits (1–50).</p>
            </div>
            <input
              value={dtmf.digit_limit ?? ""}
              onChange={(e) => {
                const raw = e.target.value.trim();
                if (raw === "") {
                  patchDtmf({ digit_limit: null });
                  return;
                }
                const n = Number(raw);
                if (Number.isInteger(n) && n >= 1 && n <= 50) patchDtmf({ digit_limit: n });
              }}
              inputMode="numeric"
              placeholder="—"
              className="h-9 w-16 rounded-lg border border-line bg-white px-2.5 text-center text-[13px] outline-none focus:border-accent"
            />
          </div>
        </div>
      )}
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

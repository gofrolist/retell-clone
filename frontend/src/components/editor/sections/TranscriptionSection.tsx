"use client";

import { Field, TextInput } from "@/components/ui/Field";
import { RadioRow } from "@/components/ui/RadioRow";
import { useState } from "react";

function RadioGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Field label={label}>
      <div>
        {options.map((o) => (
          <RadioRow
            key={o.value}
            checked={value === o.value}
            onSelect={() => onChange(o.value)}
            label={o.label}
          />
        ))}
      </div>
    </Field>
  );
}

export default function TranscriptionSection({
  denoisingMode,
  onDenoisingMode,
  sttMode,
  onSttMode,
  keywords,
  onKeywords,
}: {
  denoisingMode: string;
  onDenoisingMode: (v: string) => void;
  sttMode: string;
  onSttMode: (v: string) => void;
  keywords: string[];
  onKeywords: (k: string[]) => void;
}) {
  const [boosted, setBoosted] = useState(keywords.join(", "));

  return (
    <div className="space-y-5">
      <RadioGroup
        label="Denoising Mode"
        options={[
          { value: "noise-cancellation", label: "Remove noise" },
          {
            value: "noise-and-background-speech-cancellation",
            label: "Remove noise + background speech",
          },
        ]}
        value={denoisingMode}
        onChange={onDenoisingMode}
      />
      <RadioGroup
        label="Transcription Mode"
        options={[
          { value: "fast", label: "Optimize for speed" },
          { value: "accurate", label: "Optimize for accuracy" },
        ]}
        value={sttMode}
        onChange={onSttMode}
      />
      <Field label="Boosted Keywords" hint="Comma separated list of keywords to boost.">
        <TextInput
          value={boosted}
          onChange={(e) => {
            setBoosted(e.target.value);
            onKeywords(
              e.target.value
                .split(",")
                .map((k) => k.trim())
                .filter(Boolean),
            );
          }}
          placeholder="e.g. Architeq, check-in, medicare"
        />
      </Field>
    </div>
  );
}

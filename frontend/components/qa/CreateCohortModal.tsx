"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { CheckboxRow } from "@/components/ui/RadioRow";
import Select from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useState } from "react";

function StepDots({ step }: { step: 1 | 2 }) {
  return (
    <div className="flex items-center gap-2 text-[13px] text-sub">
      {[1, 2].map((s) => (
        <span key={s} className="flex items-center gap-2">
          <span
            className={cn(
              "flex size-5 items-center justify-center rounded-full text-[11px] font-semibold",
              step === s ? "bg-ink text-white" : "bg-app text-sub border border-line",
            )}
          >
            {s}
          </span>
          {s === 1 ? "Define QA Cohort" : "Success criteria"}
          {s === 1 && <span className="mx-1 h-px w-8 bg-line" />}
        </span>
      ))}
    </div>
  );
}

export default function CreateCohortModal({
  open,
  onClose,
  agents,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  agents: Agent[];
  onCreated: () => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [name, setName] = useState("");
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [sampling, setSampling] = useState("100");
  const [weeklyMax, setWeeklyMax] = useState("100");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setStep(1);
    setName("");
    setSelectedAgents([]);
    setSampling("100");
    setWeeklyMax("100");
    setError(null);
    onClose();
  };

  const create = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await api.createCohort({
        name: name.trim(),
        agents: selectedAgents,
        sampling_pct: Number(sampling) || 0,
        weekly_max: Number(weeklyMax) || 0,
      });
      onCreated();
      close();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create cohort");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Create QA Cohort"
      width="max-w-xl"
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          {step === 1 ? (
            <Button variant="primary" onClick={() => setStep(2)} disabled={!name.trim()}>
              Next
            </Button>
          ) : (
            <>
              <Button onClick={() => setStep(1)}>Back</Button>
              <Button variant="primary" onClick={create} disabled={submitting || !name.trim()}>
                {submitting ? "Creating…" : "Create"}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="mb-4">
        <StepDots step={step} />
      </div>

      {step === 1 ? (
        <div className="space-y-4">
          <Field label="Cohort Name">
            <TextInput
              placeholder="e.g. Transfer quality — Check-in agents"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>

          <Field label="Agents">
            <div className="max-h-40 space-y-0.5 overflow-y-auto rounded-lg border border-line p-2">
              {agents.length === 0 && (
                <p className="px-1 py-1.5 text-[13px] text-sub">No agents in this workspace.</p>
              )}
              {agents.map((a) => (
                <CheckboxRow
                  key={a.agent_id}
                  checked={selectedAgents.includes(a.agent_id)}
                  onChange={(v) =>
                    setSelectedAgents((cur) =>
                      v ? [...cur, a.agent_id] : cur.filter((id) => id !== a.agent_id),
                    )
                  }
                  label={a.agent_name}
                />
              ))}
            </div>
          </Field>

          <Field label="Filters" hint="Date-range and call filters are not available yet.">
            <div className="flex items-center gap-2 rounded-lg border border-line bg-app/50 p-2 opacity-50">
              <Select
                value="duration"
                className="pointer-events-none"
                options={[{ value: "duration", label: "Duration" }]}
              />
              <Select
                value="gt"
                className="pointer-events-none"
                options={[{ value: "gt", label: ">" }]}
              />
              <TextInput defaultValue={30} disabled className="w-20 text-center" />
              <span className="text-[13px] text-sub">s</span>
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Sampling %">
              <TextInput
                value={sampling}
                onChange={(e) => setSampling(e.target.value)}
                inputMode="decimal"
              />
            </Field>
            <Field label="Weekly Max">
              <TextInput
                value={weeklyMax}
                onChange={(e) => setWeeklyMax(e.target.value)}
                inputMode="numeric"
              />
            </Field>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Field
            label="Success Criteria"
            hint="The scoring pipeline is not available yet — criteria are not stored."
          >
            <textarea
              rows={5}
              disabled
              title="Not available yet"
              placeholder="e.g. The agent completed the wellness check, logged the outcome, and the user did not express frustration…"
              className="w-full resize-none rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </Field>
          <Field label="Scoring Metric">
            <div title="Not available yet" className="opacity-50">
              <Select
                value="transfer"
                className="w-full pointer-events-none"
                options={[
                  { value: "transfer", label: "Transfer Success Rate" },
                  { value: "wait", label: "Transfer Wait Time" },
                  { value: "custom", label: "Custom rubric" },
                ]}
              />
            </div>
          </Field>
          {error && <p className="text-[12.5px] text-bad">{error}</p>}
        </div>
      )}
    </Modal>
  );
}

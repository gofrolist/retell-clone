// Selectable LLM catalog for the dashboard. This is the single extension
// point for adding models/providers later (widen LlmProvider, add entries,
// and add provider dispatch in the worker). The backend stores the id as a
// free-form Retell-compatible string; the worker runs conversation on
// Gemini only and maps unknown ids to its default Gemini model.
// Cost/latency estimates for these ids live in lib/estimates.ts (LLM_RATES); add a rate there when adding a model (unknown ids fall back to a default rate).
export type LlmProvider = "google";

export interface LlmModel {
  id: string; // wire value stored in the Retell LLM `model` field
  label: string;
  provider: LlmProvider;
}

// Stable (non-preview) conversational Gemini models,
// per https://ai.google.dev/gemini-api/docs/models (2026-07-12).
export const LLM_MODELS: LlmModel[] = [
  { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash", provider: "google" },
  { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite", provider: "google" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", provider: "google" },
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "google" },
  { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite", provider: "google" },
];

// Post-call analysis dropdown (cosmetic today: analysis actually runs on the
// backend's global ARCHITEQ_ANALYSIS_MODEL setting, not this per-agent field).
export const POST_CALL_ANALYSIS_MODELS: LlmModel[] = LLM_MODELS.filter((m) =>
  ["gemini-3.5-flash", "gemini-3.1-flash-lite"].includes(m.id),
);

// Default for the agent-level `post_call_analysis_model` field; the lite
// model is the cheap/fast tier, which extraction tasks don't need more than.
export const DEFAULT_POST_CALL_ANALYSIS_MODEL = "gemini-3.1-flash-lite";

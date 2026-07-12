// Selectable LLM catalog for the dashboard. This is the single extension
// point for adding models/providers later (widen LlmProvider, add entries,
// and add provider dispatch in the worker). The backend stores the id as a
// free-form Retell-compatible string; the worker runs conversation on
// Gemini only and maps unknown ids to its default Gemini model.
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

// Post-call analysis dropdown (cosmetic today: analysis runs on the
// backend-configured global model). Cheap/fast models only.
export const POST_CALL_ANALYSIS_MODELS: LlmModel[] = [
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "google" },
  { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite", provider: "google" },
];

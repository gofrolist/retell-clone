import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { RawConversationFlow } from "@/lib/api";

// The sanitized real-Retell captures are the shared schema authority for the
// backend, the worker AND this editor. Reading them from here is deliberate:
// if the editor drifts from what Retell actually sends, these tests fail.
const FIXTURES = join(import.meta.dir, "../../../../../backend/tests/fixtures/retell_flows");

export const NAMES = [
  "prior_auth_hotline.json",
  "clara_outbound.json",
  "identity_verify_transfer.json",
] as const;

export const load = (name: string): RawConversationFlow =>
  JSON.parse(readFileSync(join(FIXTURES, name), "utf8"));

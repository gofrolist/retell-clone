"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { api, inviteLink, type WorkspaceInvite } from "@/lib/api";
import { useCopied } from "@/lib/useCopied";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

const ROLES = [
  { value: "member", label: "Member" },
  { value: "admin", label: "Admin" },
];

// The TTL is a backend setting (ARCHITEQ_INVITE_TTL_HOURS) — describe the
// actual expiry from the response instead of hardcoding a duration.
function expiresText(expiresAtMs: number): string {
  const hours = Math.max(1, Math.round((expiresAtMs - Date.now()) / 3_600_000));
  if (hours >= 48) return `in ${Math.round(hours / 24)} days`;
  return hours === 1 ? "in 1 hour" : `in ${hours} hours`;
}

export default function InviteMemberModal({
  open,
  onClose,
  onInvited,
}: {
  open: boolean;
  onClose: () => void;
  onInvited: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // No email delivery yet: after creating we show the link to share by hand.
  const [created, setCreated] = useState<WorkspaceInvite | null>(null);
  const { copiedKey, copy } = useCopied();

  const close = () => {
    setEmail("");
    setRole("member");
    setError(null);
    setCreated(null);
    onClose();
  };

  const invite = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const inv = await api.createInvite({ email: email.trim(), role });
      setCreated(inv);
      onInvited();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create invite");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Invite Member"
      width="max-w-md"
      footer={
        created ? (
          <Button variant="primary" onClick={close}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={invite}
              disabled={submitting || !email.trim()}
            >
              {submitting ? "Inviting…" : "Invite"}
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="space-y-3">
          <p className="text-[13px] text-sub">
            Invite created for <span className="font-medium text-ink">{created.email}</span>.
            Share this link — it works only when they sign in with that Google
            account, and expires {expiresText(created.expires_at_ms)}.
          </p>
          <div className="flex items-center gap-2">
            <TextInput readOnly value={inviteLink(created)} onFocus={(e) => e.target.select()} />
            <Button variant="secondary" onClick={() => copy(inviteLink(created))}>
              {copiedKey ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copiedKey ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Email">
            <TextInput
              type="email"
              autoFocus
              placeholder="teammate@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && email.trim() && !submitting) invite();
              }}
            />
          </Field>
          <Field label="Role">
            <Select value={role} onChange={setRole} options={ROLES} />
          </Field>
          {error && <p className="text-[12.5px] text-bad">{error}</p>}
        </div>
      )}
    </Modal>
  );
}

"use client";

import InviteMemberModal from "@/components/settings/InviteMemberModal";
import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import { api, inviteLink, type WorkspaceInvite, type WorkspaceMember } from "@/lib/api";
import {
  getServerSessionSnapshot,
  getSessionSnapshot,
  subscribeSession,
} from "@/lib/auth";
import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

function MemberAvatar({ label, picture }: { label: string; picture?: string }) {
  if (picture) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- tiny remote avatar; next/image needs remotePatterns config
      <img
        src={picture}
        alt=""
        referrerPolicy="no-referrer"
        className="size-6 shrink-0 rounded-full object-cover"
      />
    );
  }
  return (
    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-amber-400 to-rose-400 text-[10px] font-semibold text-white">
      {(label || "?").charAt(0).toUpperCase()}
    </span>
  );
}

export default function WorkspacePage() {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invites, setInvites] = useState<WorkspaceInvite[]>([]);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const session = useSyncExternalStore(
    subscribeSession,
    getSessionSnapshot,
    getServerSessionSnapshot,
  );

  const refreshMembers = useCallback(() => {
    api.listMembers().then(setMembers).catch(() => {});
    api.listInvites().then(setInvites).catch(() => {});
  }, []);

  useEffect(() => {
    api
      .getWorkspace()
      .then((ws) => setName(ws.name))
      .catch(() => {}); // backend banner covers unreachable
    refreshMembers();
  }, [refreshMembers]);

  const save = async () => {
    setSaving(true);
    setSaveState("idle");
    setSaveError(null);
    try {
      const ws = await api.updateWorkspace({ name: name.trim() });
      setName(ws.name);
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2000);
    } catch (e) {
      setSaveState("error");
      setSaveError(e instanceof Error ? e.message : "Failed to rename workspace");
    } finally {
      setSaving(false);
    }
  };

  const copyLink = async (invite: WorkspaceInvite) => {
    await navigator.clipboard.writeText(inviteLink(invite));
    setCopiedId(invite.invite_id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const revoke = async (invite: WorkspaceInvite) => {
    try {
      await api.revokeInvite(invite.invite_id);
    } catch {
      // refetch below reconciles either way
    }
    refreshMembers();
  };

  // Before the first login lands a member row (or in API-key dev mode) the
  // list is empty — fall back to showing the current identity as owner.
  const memberRows = members.length
    ? members
    : [
        {
          email: session?.email ?? "API key (dev)",
          name: session?.name ?? null,
          role: "owner",
          created_at_ms: 0,
        } satisfies WorkspaceMember,
      ];

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Workspace" />
        <div className="space-y-4">
          <SettingsCard title="Workspace Name">
            <div className="flex items-center gap-2">
              <TextInput value={name} onChange={(e) => setName(e.target.value)} />
              <Button variant="primary" onClick={save} disabled={saving || !name.trim()}>
                {saving ? "Saving…" : saveState === "saved" ? "Saved" : "Save"}
              </Button>
            </div>
            {saveState === "error" && saveError && (
              <p className="mt-2 text-[12.5px] text-bad">{saveError}</p>
            )}
          </SettingsCard>

          <SettingsCard
            title="Members"
            description="People with access to this workspace."
            right={
              <Button size="sm" onClick={() => setInviteOpen(true)}>
                Invite
              </Button>
            }
          >
            <div className="divide-y divide-line rounded-lg border border-line">
              {memberRows.map((m) => (
                <div key={m.email} className="flex items-center justify-between px-3 py-2.5">
                  <span className="flex items-center gap-2.5 text-[13px]">
                    <MemberAvatar
                      label={m.name ?? m.email}
                      picture={m.email === session?.email ? session?.picture : undefined}
                    />
                    <span>
                      {m.email}
                      {m.name && <span className="ml-1.5 text-sub">{m.name}</span>}
                    </span>
                  </span>
                  <Badge tone={m.role === "owner" ? "blue" : "gray"}>
                    {ROLE_LABEL[m.role] ?? m.role}
                  </Badge>
                </div>
              ))}
              {invites.map((inv) => (
                <div
                  key={inv.invite_id}
                  className="flex items-center justify-between px-3 py-2.5"
                >
                  <span className="flex items-center gap-2.5 text-[13px]">
                    <MemberAvatar label={inv.email} />
                    <span>
                      {inv.email}
                      <span className="ml-1.5 text-sub">
                        Invited{inv.invited_by ? ` by ${inv.invited_by}` : ""}
                      </span>
                    </span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <Badge tone="outline">Pending</Badge>
                    <Button size="sm" variant="ghost" onClick={() => copyLink(inv)}>
                      {copiedId === inv.invite_id ? (
                        <Check className="size-3.5" />
                      ) : (
                        <Copy className="size-3.5" />
                      )}
                      {copiedId === inv.invite_id ? "Copied" : "Copy link"}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => revoke(inv)}>
                      Revoke
                    </Button>
                  </span>
                </div>
              ))}
            </div>
          </SettingsCard>

          <SettingsCard
            title="Billing"
            description="Current plan and payment method."
            right={<Badge tone="blue">Pay As You Go</Badge>}
          >
            <Field label="Billing Email">
              <div className="flex items-center gap-2">
                <TextInput value={session?.email ?? ""} readOnly disabled />
                <Button disabled title="Not available yet">
                  Update
                </Button>
              </div>
            </Field>
          </SettingsCard>

          <SettingsCard
            title="Danger Zone"
            description="Deleting a workspace removes all agents, calls and data permanently."
            right={
              <Button size="sm" variant="danger" disabled title="Not available yet">
                Delete Workspace
              </Button>
            }
          />
        </div>
      </div>

      <InviteMemberModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        onInvited={refreshMembers}
      />
    </div>
  );
}

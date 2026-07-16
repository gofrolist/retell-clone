"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { Field, TextInput } from "@/components/ui/Field";
import LoadError from "@/components/ui/LoadError";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";
import { cn, formatDate } from "@/lib/utils";
import { KeyRound, Plus, TriangleAlert } from "lucide-react";
import { useState } from "react";

export default function ApiKeysPage() {
  const { data, loading, error, setError, reload } = useApiData(() => api.listApiKeys());
  const keys = data ?? [];
  const [freshSecret, setFreshSecret] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const createKey = async () => {
    setCreating(true);
    setCreateError(null);
    try {
      const created = await api.createApiKey(newName.trim() || "API key");
      setFreshSecret(created.secret ?? null);
      setCreateOpen(false);
      setNewName("");
      reload();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : "Failed to create API key");
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (id: string) => {
    try {
      await api.revokeApiKey(id);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to revoke API key");
    }
  };

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader
          title="API Keys"
          action={
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              <Plus className="size-3.5" />
              Create API Key
            </Button>
          }
        />

        {freshSecret && (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="flex items-center gap-2 text-[13.5px] font-semibold text-amber-800">
              <TriangleAlert className="size-4" />
              Copy your new API key now
            </div>
            <p className="mt-0.5 text-[12.5px] text-amber-700">
              For security reasons it will not be shown again.
            </p>
            <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2">
              <CopyId value={freshSecret} className="grow justify-between" />
            </div>
            <Button size="sm" className="mt-2" onClick={() => setFreshSecret(null)}>
              Done
            </Button>
          </div>
        )}

        <SettingsCard title="Keys" description="Authenticate server-side requests to the Arhiteq API.">
          {loading && (
            <p className="py-6 text-center text-[13px] text-sub">Loading API keys…</p>
          )}
          {!loading && error && (
            <p className="py-6 text-center text-[13px]">
              <LoadError error={error} onRetry={reload} />
            </p>
          )}
          {!loading && !error && keys.length === 0 && (
            <p className="py-6 text-center text-[13px] text-sub">No API keys yet.</p>
          )}
          {!loading && !error && keys.length > 0 && (
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line text-[12.5px] text-sub">
                  <th className="py-2 pr-3 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Key</th>
                  <th className="px-3 py-2 font-medium">Created</th>
                  <th className="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.key_id} className="border-b border-line/70 last:border-b-0">
                    <td className="py-2.5 pr-3">
                      <span
                        className={cn(
                          "flex items-center gap-2 text-[13px] font-medium",
                          k.revoked && "text-faint line-through",
                        )}
                      >
                        <KeyRound className="size-3.5 text-faint" />
                        {k.name}
                      </span>
                    </td>
                    <td
                      className={cn(
                        "px-3 py-2.5 font-mono text-[12px] text-sub",
                        k.revoked && "line-through",
                      )}
                    >
                      {k.prefix}
                    </td>
                    <td className="px-3 py-2.5 text-[12.5px] text-sub">{formatDate(k.created_at)}</td>
                    <td className="px-3 py-2.5 text-right">
                      {k.revoked ? (
                        <span className="rounded-full border border-line bg-app px-2 py-0.5 text-[11.5px] font-medium text-faint">
                          Revoked
                        </span>
                      ) : (
                        <Button size="sm" variant="danger" onClick={() => revoke(k.key_id)}>
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </SettingsCard>
      </div>

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create API Key"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={createKey} disabled={creating}>
              {creating ? "Creating…" : "Create"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name" hint="A label to tell your keys apart.">
            <TextInput
              placeholder="e.g. Production server"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !creating && createKey()}
            />
          </Field>
          {createError && <p className="text-[12.5px] text-bad">{createError}</p>}
        </div>
      </Modal>
    </div>
  );
}

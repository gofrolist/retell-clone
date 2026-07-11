"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";

export default function WebhookSection({
  url,
  onUrl,
}: {
  url: string;
  onUrl: (v: string) => void;
}) {
  return (
    <div className="space-y-5">
      <Field label="Agent Level Webhook URL">
        <div className="flex items-center gap-2">
          <TextInput
            value={url}
            onChange={(e) => onUrl(e.target.value)}
            placeholder="https://your-server.com/webhook"
          />
          <Button disabled title="Not available yet">
            Test
          </Button>
        </div>
      </Field>
    </div>
  );
}

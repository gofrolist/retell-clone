import Badge from "@/components/ui/Badge";
import type { TestRunStatus } from "@/lib/api";
import { Check, CircleDashed, Loader2, TriangleAlert, X } from "lucide-react";

const LABELS: Record<TestRunStatus, string> = {
  pending: "Queued",
  in_progress: "Running",
  pass: "Passed",
  fail: "Failed",
  error: "Error",
};

/** One run's verdict, styled the same wherever it appears. */
export function RunStatusBadge({ status }: { status: TestRunStatus }) {
  if (status === "pass") {
    return (
      <Badge tone="green">
        <Check className="size-3" /> {LABELS.pass}
      </Badge>
    );
  }
  if (status === "fail") {
    return (
      <Badge tone="red">
        <X className="size-3" /> {LABELS.fail}
      </Badge>
    );
  }
  if (status === "error") {
    return (
      <Badge tone="red">
        <TriangleAlert className="size-3" /> {LABELS.error}
      </Badge>
    );
  }
  return (
    <Badge tone="gray">
      {status === "in_progress" ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <CircleDashed className="size-3" />
      )}
      {LABELS[status]}
    </Badge>
  );
}

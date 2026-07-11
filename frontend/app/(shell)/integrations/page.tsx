import EmptyState from "@/components/ui/EmptyState";
import { Blocks } from "lucide-react";

export default function IntegrationsPage() {
  return (
    <div className="px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <Blocks className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Integrations</h1>
      </div>
      <EmptyState
        icon={Blocks}
        title="No integrations installed"
        description="Connect calendars, CRMs and ticketing systems to give your agents superpowers."
      />
    </div>
  );
}

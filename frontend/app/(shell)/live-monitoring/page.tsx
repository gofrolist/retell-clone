import EmptyState from "@/components/ui/EmptyState";
import { Headphones } from "lucide-react";

export default function LiveMonitoringPage() {
  return (
    <div className="px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <Headphones className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Live Monitoring</h1>
      </div>
      <EmptyState
        icon={Headphones}
        title="No live calls"
        description="Ongoing calls will appear here in real time so you can listen in and whisper to agents."
      />
    </div>
  );
}

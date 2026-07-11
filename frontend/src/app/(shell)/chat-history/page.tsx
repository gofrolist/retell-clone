import EmptyState from "@/components/ui/EmptyState";
import { MessageSquareText } from "lucide-react";

export default function ChatHistoryPage() {
  return (
    <div className="px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <MessageSquareText className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Chat History</h1>
      </div>
      <EmptyState
        icon={MessageSquareText}
        title="No chat sessions yet"
        description="Chat sessions from your web and SMS agents will appear here once you deploy a chat channel."
      />
    </div>
  );
}

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export default function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-line bg-white shadow-sm">
        <Icon className="size-5 text-faint" />
      </div>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-[13px] text-sub">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

import type { ReactNode } from "react";

export default function SettingsCard({
  title,
  description,
  children,
  right,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-[13.5px] font-semibold">{title}</div>
          {description && <p className="mt-0.5 text-[12.5px] text-sub">{description}</p>}
        </div>
        {right}
      </div>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}

export function SettingsPageHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <h1 className="text-[17px] font-semibold">{title}</h1>
      {action}
    </div>
  );
}

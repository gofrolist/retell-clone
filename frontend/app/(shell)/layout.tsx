import AuthGuard from "@/components/shell/AuthGuard";
import BackendBanner from "@/components/shell/BackendBanner";
import Sidebar from "@/components/shell/Sidebar";

export default function ShellLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 grow flex-col overflow-hidden">
          <BackendBanner />
          <main className="min-w-0 grow overflow-hidden bg-card">{children}</main>
        </div>
      </div>
    </AuthGuard>
  );
}

import AuthGuard from "@/components/shell/AuthGuard";

// The agent editor renders full-screen (outside the (shell) group, no
// sidebar) but is still a protected page, so it gets its own AuthGuard.
export default function AgentEditorLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AuthGuard>{children}</AuthGuard>;
}

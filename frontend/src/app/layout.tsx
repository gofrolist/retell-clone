import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Arhiteq Dashboard",
  description: "Build, deploy and monitor AI voice agents",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased">{children}</body>
    </html>
  );
}

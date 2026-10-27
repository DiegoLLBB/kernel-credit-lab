import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kernel Credit Lab",
  description: "Agentic, verifiable research for USD investment-grade bonds.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}

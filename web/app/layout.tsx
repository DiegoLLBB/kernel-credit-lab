import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Kernel Credit Lab",
    template: "%s · Kernel Credit Lab",
  },
  description:
    "Kernel watches USD investment-grade issuers and alerts when their debt maturity wall changes.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <Nav />
        {children}
      </body>
    </html>
  );
}

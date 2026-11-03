import type { Metadata } from "next";
import "@fontsource-variable/public-sans";
import "@fontsource-variable/source-serif-4";
// Only for the module name beside the logo, matching the logo's monospace.
import "@fontsource/jetbrains-mono/latin-500.css";
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

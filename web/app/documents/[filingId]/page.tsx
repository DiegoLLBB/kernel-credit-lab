import type { Metadata } from "next";
import { Suspense } from "react";
import { LoadingLine, PageShell } from "@/components/loading-error";
import { DocumentClient } from "./document-client";

export const metadata: Metadata = {
  title: "Document",
};

export default function DocumentPage() {
  return (
    <PageShell title="Document">
      <Suspense fallback={<LoadingLine label="Loading document…" />}>
        <DocumentClient />
      </Suspense>
    </PageShell>
  );
}

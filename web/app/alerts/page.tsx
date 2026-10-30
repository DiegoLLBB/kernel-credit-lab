import type { Metadata } from "next";
import { Suspense } from "react";
import { LoadingLine, PageShell } from "@/components/loading-error";
import { AlertsClient } from "./alerts-client";

export const metadata: Metadata = {
  title: "Alerts",
};

export default function AlertsPage() {
  return (
    <PageShell title="Alerts">
      <Suspense fallback={<LoadingLine label="Loading alerts…" />}>
        <AlertsClient />
      </Suspense>
    </PageShell>
  );
}

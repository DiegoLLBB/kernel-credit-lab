import type { Metadata } from "next";
import { AlertDetailClient } from "./alert-detail-client";

export const metadata: Metadata = {
  title: "Alert",
};

export default function AlertDetailPage() {
  return <AlertDetailClient />;
}

import type { Metadata } from "next";
import { IssuerDetailClient } from "./issuer-detail-client";

export const metadata: Metadata = {
  title: "Issuer",
};

export default function IssuerPage() {
  return <IssuerDetailClient />;
}

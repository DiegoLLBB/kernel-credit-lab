import type { Metadata } from "next";
import { IssuersClient } from "./issuers-client";

export const metadata: Metadata = {
  title: "Issuers",
};

export default function IssuersPage() {
  return <IssuersClient />;
}

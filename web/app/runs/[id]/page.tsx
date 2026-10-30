import type { Metadata } from "next";
import { TraceClient } from "./trace-client";

export const metadata: Metadata = {
  title: "Run trace",
};

export default function RunTracePage() {
  return <TraceClient />;
}

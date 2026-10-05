import type { ReactNode } from "react";
import { dashboardTenantProfile } from "@/lib/tenant-access";

/** Presentation only; the server independently checks every operation. */
export function OperatorOnly({ children }: { children: ReactNode }) {
  return dashboardTenantProfile() ? null : children;
}

declare global {
  interface Window {
    __HERMES_TENANT_PROFILE__?: string;
  }
}

/** Presentation only. The HTTP/WS server independently enforces ownership. */
export function dashboardTenantProfile(): string {
  return typeof window === "undefined" ? "" : window.__HERMES_TENANT_PROFILE__ ?? "";
}

const PLAYER_PAGES = new Set(["/", "/chat", "/sessions", "/profiles", "/skills", "/cron", "/files", "/memory"]);

export function tenantPageAllowed(path: string, profile = dashboardTenantProfile()): boolean {
  return !profile || PLAYER_PAGES.has(path);
}

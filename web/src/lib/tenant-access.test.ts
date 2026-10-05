import { describe, expect, it } from "vitest";
import { tenantPageAllowed } from "./tenant-access";

describe("tenant dashboard navigation", () => {
  it("retains personal pages and omits operator surfaces", () => {
    for (const path of ["/chat", "/sessions", "/profiles", "/skills", "/cron", "/files", "/memory"]) {
      expect(tenantPageAllowed(path, "steam_player")).toBe(true);
    }
    for (const path of ["/config", "/env", "/mcp", "/plugins", "/system", "/profiles/new"]) {
      expect(tenantPageAllowed(path, "steam_player")).toBe(false);
      expect(tenantPageAllowed(path, "")).toBe(true);
    }
  });
});

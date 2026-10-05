import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadSkillsPageResources } from "./personal-skills";

const mocks = vi.hoisted(() => ({ skills: vi.fn(), toolsets: vi.fn(), tenant: vi.fn() }));
vi.mock("./api", () => ({ api: { getSkills: mocks.skills, getToolsets: mocks.toolsets } }));
vi.mock("./tenant-access", () => ({ dashboardTenantProfile: mocks.tenant }));
beforeEach(() => vi.clearAllMocks());

describe("skills page resources", () => {
  it("loads personal skills without requesting operator-only settings", async () => {
    mocks.tenant.mockReturnValue("steam_player");
    mocks.skills.mockResolvedValue([{ name: "my-skill" }]);
    expect(await loadSkillsPageResources("steam_player")).toEqual([[{ name: "my-skill" }], []]);
    expect(mocks.skills).toHaveBeenCalledWith("steam_player");
    expect(mocks.toolsets).not.toHaveBeenCalled();
  });

  it("retains the native operator catalog", async () => {
    mocks.tenant.mockReturnValue("");
    mocks.skills.mockResolvedValue([]);
    mocks.toolsets.mockResolvedValue([{ name: "terminal" }]);
    expect(await loadSkillsPageResources("personal")).toEqual([[], [{ name: "terminal" }]]);
    expect(mocks.toolsets).toHaveBeenCalledWith("personal");
  });
});

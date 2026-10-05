import { api } from "./api";
import { dashboardTenantProfile } from "./tenant-access";

export function loadSkillsPageResources(profile?: string) {
  const toolsets = dashboardTenantProfile() ? Promise.resolve([]) : api.getToolsets(profile);
  return Promise.all([api.getSkills(profile), toolsets]);
}

import { test as setup } from "@playwright/test";

import { authFile, ROLES, signIn, type RoleName } from "./helpers";

for (const role of Object.keys(ROLES) as RoleName[]) {
  setup(`sign in as ${role}`, async ({ page }) => {
    await signIn(page, role);
    await page.context().storageState({ path: authFile(role) });
  });
}

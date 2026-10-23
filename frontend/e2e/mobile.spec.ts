import { expect, test } from "@playwright/test";

import { authFile } from "./helpers";

test.use({ storageState: authFile("operator") });

test("phone layout: menu drawer, Today tiles and Approvals", async ({ page }) => {
  await page.goto("/today");
  await expect(page.getByRole("heading", { level: 1, name: "Today's Work" })).toBeVisible();
  await expect(page.getByText("Needs your attention").first()).toBeVisible();
  // The sidebar is hidden on phones; the menu button opens it as a drawer.
  await page.getByRole("button", { name: /Menu/ }).click();
  const drawer = page.getByRole("dialog", { name: "Menu" });
  await expect(drawer.getByText("Workspace", { exact: true })).toBeVisible();
  await drawer.getByRole("link", { name: /^Approvals/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Approvals" })).toBeVisible();
  await expect(drawer).toHaveCount(0); // closes after navigating
  // Nothing on the page is wider than the screen.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

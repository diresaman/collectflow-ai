import { expect, type Page } from "@playwright/test";

export const PASSWORD = "e2e-pass-123";
export const ROLES = { operator: "e2e-operator", approver: "e2e-approver", approver2: "e2e-approver2" } as const;
export type RoleName = keyof typeof ROLES;

export const authFile = (role: RoleName) => `e2e/.auth/${role}.json`;

export async function signIn(page: Page, role: RoleName): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(/user ?name/i).fill(ROLES[role]);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

/** The sidebar (desktop) holding the main navigation. */
export const sidebar = (page: Page) => page.getByRole("navigation", { name: "Main" }).first();

/** Looks up an invoice by its sheet ID through the app's own API (signed-in cookies). */
export async function invoiceId(page: Page, externalId: string): Promise<string> {
  const response = await page.request.get("/api/backend/invoices");
  expect(response.ok()).toBeTruthy();
  const rows = (await response.json()) as { id: string; external_id: string }[];
  const row = rows.find((r) => r.external_id === externalId);
  if (!row) throw new Error(`invoice ${externalId} not seeded`);
  return row.id;
}

export async function openInvoice(page: Page, externalId: string): Promise<void> {
  await page.goto(`/?invoice=${await invoiceId(page, externalId)}`);
  await expect(page.getByRole("dialog").first()).toBeVisible();
}

export async function csrfToken(page: Page): Promise<string> {
  await page.request.get("/api/backend/auth/csrf");
  const cookie = (await page.context().cookies()).find((c) => c.name === "csrftoken");
  return cookie?.value ?? "";
}

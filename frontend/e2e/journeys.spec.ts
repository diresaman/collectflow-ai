import { expect, test, type Browser, type Page } from "@playwright/test";

import { authFile, csrfToken, invoiceId, openInvoice, sidebar, type RoleName } from "./helpers";

/** A page signed in as `role` (storage state from auth.setup.ts). */
async function as(browser: Browser, role: RoleName): Promise<Page> {
  const context = await browser.newContext({ storageState: authFile(role), viewport: { width: 1366, height: 900 } });
  return context.newPage();
}

test.describe("1 sign-in and role-specific navigation", () => {
  test("signed-out users are sent to the sign-in page", async ({ page }) => {
    await page.goto("/today");
    await expect(page).toHaveURL(/\/login/);
  });

  test("the grouped sidebar shows the work areas and the user's role", async ({ browser }) => {
    const page = await as(browser, "operator");
    await page.goto("/today");
    const nav = sidebar(page);
    for (const group of ["Workspace", "Customers & Payments", "Reports", "Settings"]) {
      await expect(nav.getByText(group, { exact: true })).toBeVisible();
    }
    await expect(page.getByText("Operator", { exact: true }).first()).toBeVisible();
    await expect(nav.getByRole("list", { name: "Workspace" })).toBeVisible(); // groups are named for screen readers
    // Every menu item opens a page whose heading uses the same words, is highlighted, and keeps the shell.
    for (const item of [
      "Today's Work", "Customer Replies", "Approvals", "Tasks", "Disputes", "Customers", "Invoices",
      "Reports & Insights", "Reminder Schedule", "Automation Settings", "Connected Services",
    ]) {
      await nav.getByRole("link", { name: new RegExp(`^${item.replace(/[&']/g, ".")}`) }).click();
      await expect(page.getByRole("heading", { level: 1, name: item, exact: true })).toBeVisible();
      await expect(nav.locator('[aria-current="page"]')).toContainText(item);
      await expect(page.getByText("Checking your session")).toHaveCount(0);
    }
  });

  test("the home address opens Today's Work, and older invoice links still open the invoice", async ({ browser }) => {
    const page = await as(browser, "operator");
    await page.goto("/");
    await expect(page).toHaveURL(/\/today$/);
    await expect(page.getByRole("heading", { level: 1, name: "Today's Work" })).toBeVisible();
    const id = await invoiceId(page, "EL-400");
    await page.goto(`/?invoice=${id}`);
    await expect(page).toHaveURL(new RegExp(`/invoices\\?invoice=${id}$`));
    await expect(page.getByRole("dialog").first()).toBeVisible();
    await expect(sidebar(page).locator('[aria-current="page"]')).toContainText("Invoices");
  });
});

test("2 Today shows what needs attention and the next action", async ({ browser }) => {
  const page = await as(browser, "operator");
  await page.goto("/today");
  await expect(page.locator("main section h2").first()).toContainText("Needs your attention");
  await expect(page.getByRole("link", { name: "Review reply" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Review dispute" }).first()).toBeVisible();
  await expect(page.getByText("For an approver").first()).toBeVisible(); // the firm draft
  // Following an action link inside the app opens the right record (deep link without a page reload).
  await page.getByRole("link", { name: "Review reply" }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Customer Replies" })).toBeVisible();
  await expect(page.getByText("What the customer wrote")).toBeVisible();
});

test("3 a client's risk score is explained", async ({ browser }) => {
  const page = await as(browser, "operator");
  await page.goto("/clients");
  await page.getByRole("option", { name: /Harbor Analytics/ }).first().click();
  await expect(page.getByText("Why this score")).toBeVisible();
  await expect(page.getByRole("row", { name: /Broken promises/ })).toContainText("15 / 25");
  await expect(page.getByRole("row", { name: /Oldest invoice overdue/ })).toBeVisible();
});

test("4 an invoice explains what blocks chasing", async ({ browser }) => {
  const page = await as(browser, "operator");
  await openInvoice(page, "DW-200");
  const drawer = page.getByRole("dialog").first();
  await expect(drawer.getByText(/disputed/i).first()).toBeVisible();
  await expect(drawer.getByText(/approver/i).first()).toBeVisible();
});

test("5 reviewing and classifying a customer reply", async ({ browser }) => {
  const page = await as(browser, "operator");
  await page.goto("/inbox");
  await page.getByRole("option", { name: /RB-300/ }).click();
  await expect(page.getByText("What the customer wrote")).toBeVisible();
  await expect(page.getByText("What the AI suggests")).toBeVisible();
  await page.getByRole("radio", { name: /Requests info/ }).click();
  await expect(page.getByText("Requested information")).toBeVisible();
  await expect(page.getByText("Payment reference")).toHaveCount(0); // only the fields for this label
  await page.getByRole("button", { name: "Save decision: Requests info" }).click();
  // The handled reply leaves "Needs review" and the list moves on to the next one; the decision is on the reply.
  await expect(page.getByRole("option", { name: /RB-300/ })).toHaveCount(0);
  await page.getByRole("tab", { name: "All replies" }).click();
  await page.getByRole("option", { name: /RB-300/ }).click();
  await expect(page.getByText("Decided by a person")).toBeVisible();
});

test("6 editing a friendly reminder, approving it and sending it (fake Gmail)", async ({ browser }) => {
  const page = await as(browser, "operator");
  await openInvoice(page, "EL-400");
  const drawer = page.getByRole("dialog").first();
  const message = drawer.getByLabel("Message");
  await message.fill(`${await message.inputValue()}\n\nThank you.`);
  await drawer.getByRole("button", { name: "Save changes" }).click();
  await drawer.getByRole("button", { name: "Approve reminder" }).click();
  await expect(drawer.getByText(/Approved/).first()).toBeVisible();
  await drawer.getByRole("button", { name: "Send email…" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Send email" }).click();
  await expect(drawer.getByText("Sent via Gmail")).toBeVisible({ timeout: 60_000 });
});

test("7 four-eyes: an editor cannot approve a firm, high-value reminder", async ({ browser }) => {
  const operator = await as(browser, "operator");
  await openInvoice(operator, "FA-500");
  let drawer = operator.getByRole("dialog").first();
  await expect(drawer.getByRole("button", { name: "Approve reminder" })).toBeDisabled();
  await expect(drawer.getByText(/You can.t approve this/)).toBeVisible();

  const approver = await as(browser, "approver");
  await openInvoice(approver, "FA-500");
  drawer = approver.getByRole("dialog").first();
  const message = drawer.getByLabel("Message");
  await message.fill(`${await message.inputValue()}\n\nKind regards.`);
  await drawer.getByRole("button", { name: "Save changes" }).click();
  await expect(drawer.getByRole("button", { name: "Approve reminder" })).toBeDisabled();
  await expect(drawer.getByText(/another approver must approve it/)).toBeVisible();

  const second = await as(browser, "approver2");
  await openInvoice(second, "FA-500");
  drawer = second.getByRole("dialog").first();
  await drawer.getByRole("button", { name: "Approve reminder" }).click();
  await expect(drawer.getByRole("button", { name: "Send email…" })).toBeVisible();
});

test("8 managing a dispute: notes by an operator, resolution by an approver", async ({ browser }) => {
  const operator = await as(browser, "operator");
  await operator.goto("/disputes");
  await operator.getByRole("option", { name: /DW-201/ }).click();
  await operator.getByLabel("Internal note").fill("Asked the client for their timesheet.");
  await operator.getByRole("button", { name: "Add note" }).click();
  await expect(operator.getByText("Asked the client for their timesheet.")).toBeVisible();
  await expect(operator.getByText(/Only an approver can resolve/).first()).toBeVisible();

  const approver = await as(browser, "approver");
  await approver.goto("/disputes");
  await approver.getByRole("option", { name: /DW-201/ }).click();
  await approver.getByText("Invoice stands (chasing may resume)").click();
  await approver.getByLabel("Resolution").fill("Hours confirmed by the project lead.");
  await approver.getByRole("button", { name: "Resolve dispute" }).click();
  await expect(approver.getByText(/Resolved: invoice stands|Invoice stands/).first()).toBeVisible();
});

test("9 recording a promise to pay from a reply", async ({ browser }) => {
  const page = await as(browser, "operator");
  await page.goto("/inbox");
  await page.getByRole("option", { name: /RB-301/ }).click();
  await page.getByRole("radio", { name: /Promise to pay/ }).click();
  const form = page.getByRole("region", { name: "Record the promise to pay" });
  await expect(form.getByLabel(/Promised date/)).not.toHaveValue("");
  await form.getByRole("button", { name: "Record promise to pay" }).click();
  await expect(page.getByText("Promise recorded", { exact: true })).toBeVisible();
});

test("10 assigning and completing a task", async ({ browser }) => {
  const page = await as(browser, "operator");
  await page.goto("/tasks");
  const task = page.getByRole("listitem").filter({ hasText: "Call Tidewater about the missing purchase order" });
  await task.getByLabel("Assignee").selectOption({ label: "Olga (operator)" });
  await expect(page.getByText(/was assigned/)).toBeVisible();
  await task.getByLabel("Task note").fill("Called; PO number received.");
  await task.getByRole("button", { name: "Mark task complete" }).click();
  await expect(page.getByText(/marked complete/)).toBeVisible();
});

test("11 analytics are explained and labelled as estimates", async ({ browser }) => {
  const page = await as(browser, "operator");
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { name: "Aging" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Cash forecast.*estimate/ })).toBeVisible();
  await expect(page.getByText(/not the bank date/).first()).toBeVisible();
  await expect(page.getByText(/Opening balances are never estimated/).first()).toBeVisible();
});

test.describe("12 consolidated statements", () => {
  test("prepare a statement (fake Gemini) and review it as an approver", async ({ browser }) => {
    const operator = await as(browser, "operator");
    await operator.goto("/clients");
    await operator.getByRole("option", { name: /Summit Partners/ }).first().click();
    const panel = operator.getByRole("region", { name: "Consolidated statement" });
    await expect(panel.getByText(/Total:/)).toBeVisible();
    await panel.getByRole("button", { name: "Prepare statement draft" }).click();
    await expect(panel.getByText(/Statement drafted/)).toBeVisible({ timeout: 60_000 });

    const approver = await as(browser, "approver");
    await approver.goto("/approvals");
    const row = approver.getByRole("listitem").filter({ hasText: "Summit Partners" });
    await expect(row.getByText(/Statement · 3 invoices/)).toBeVisible();
    await row.getByRole("link", { name: "Review draft" }).click();
    const drawer = approver.getByRole("dialog").first();
    await expect(drawer.getByText("Invoices in this statement")).toBeVisible();
    await expect(drawer.getByRole("button", { name: "Approve statement" })).toBeEnabled();
  });

  test("a stale statement cannot be approved", async ({ browser }) => {
    const approver = await as(browser, "approver");
    await approver.goto("/approvals");
    await approver.getByRole("listitem").filter({ hasText: "Stale Supplies" }).getByRole("link").click();
    const drawer = approver.getByRole("dialog").first();
    await expect(drawer.getByText(/changed since the statement was prepared/)).toBeVisible();
    await drawer.getByRole("button", { name: "Approve statement" }).click();
    await expect(drawer.getByRole("alert").filter({ hasText: /changed/ }).first()).toBeVisible();
    await expect(drawer.getByRole("button", { name: "Send email…" })).toHaveCount(0);
  });
});

test.describe("13 delivery and integration statuses", () => {
  test("an unknown delivery outcome is explained and never offered for resending", async ({ browser }) => {
    const page = await as(browser, "operator");
    await page.goto("/today");
    await expect(page.getByText(/Delivery outcome unknown: Reminder: invoice UN-900/)).toBeVisible();
    await openInvoice(page, "UN-900");
    const drawer = page.getByRole("dialog").first();
    await expect(drawer.getByText("Delivery outcome unknown").first()).toBeVisible();
    await expect(drawer.getByText(/send it again/)).toBeVisible();
    await expect(drawer.getByRole("button", { name: /Send email/ })).toHaveCount(0);
  });

  test("Integrations summarises health and lists every scheduled job", async ({ browser }) => {
    const page = await as(browser, "operator");
    await page.goto("/settings/integrations");
    await expect(page.getByText(/Everything is working|problem\(s\)/)).toBeVisible();
    await expect(page.getByRole("cell", { name: /Risk and analytics/ })).toBeVisible();
  });
});

test.describe("negative paths enforced by the backend", () => {
  test("writes without the CSRF token are refused", async ({ browser }) => {
    const page = await as(browser, "operator");
    await page.goto("/today");
    const id = await invoiceId(page, "HA-101");
    const response = await page.request.post("/api/backend/tasks", {
      data: { invoice_id: id, title: "should not be created" },
      headers: { Origin: "http://127.0.0.1:3002" },
    });
    expect(response.status()).toBe(403);
    expect((await response.json()).code).toBe("csrf_failed");
  });

  test("an operator cannot resolve a dispute even through the API", async ({ browser }) => {
    const page = await as(browser, "operator");
    await page.goto("/disputes");
    const disputes = (await (await page.request.get("/api/backend/disputes?status=OPEN")).json()) as { id: string }[];
    const response = await page.request.post(`/api/backend/disputes/${disputes[0]!.id}/resolve`, {
      data: { outcome: "INVALID", resolution: "operator attempt" },
      headers: { "X-CSRFToken": await csrfToken(page), Origin: "http://127.0.0.1:3002" },
    });
    expect(response.status()).toBe(403);
    expect((await response.json()).code).toBe("approver_required");
  });

  test("requests from another site are refused by the app's proxy", async ({ browser }) => {
    const page = await as(browser, "approver");
    const response = await page.request.post("/api/backend/clients/recompute", {
      headers: { Origin: "http://evil.example" },
    });
    expect(response.status()).toBe(403);
  });
});

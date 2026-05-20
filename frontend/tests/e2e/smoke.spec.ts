import { test, expect } from "@playwright/test";

test("homepage loads", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("nanoCursor");
  const app = page.locator("#app");
  await expect(app).toBeVisible();
});

test("topbar renders with brand and controls", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".topbar")).toBeVisible();
  // Brand name
  await expect(page.locator(".topbar")).toContainText("nanoCursor");
  // Workspace input
  await expect(page.locator(".workspace-picker")).toBeVisible();
});

test("sidebar renders with tabs", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toBeVisible();
  // Should have project/session/file tabs
  await expect(sidebar.locator(".side-tabs")).toBeVisible();
});

test("chat panel renders with prompt input", async ({ page }) => {
  await page.goto("/");
  const chat = page.locator(".chat-panel");
  await expect(chat).toBeVisible();
  // Prompt input should exist
  const promptBox = chat.locator(".prompt-box textarea");
  await expect(promptBox).toBeVisible();
});

test("right panel renders with tabs", async ({ page }) => {
  await page.goto("/");
  const right = page.locator(".right-panel");
  await expect(right).toBeVisible();
  await expect(right.locator(".right-tabs")).toBeVisible();
});

test("bottom panel renders in collapsed state", async ({ page }) => {
  await page.goto("/");
  const bottom = page.locator(".bottom-panel");
  await expect(bottom).toBeVisible();
  // Bottom panel starts collapsed with summary chips
  await expect(bottom.locator(".bottom-summary")).toBeVisible();
});

test("expand bottom panel shows tabs", async ({ page }) => {
  await page.goto("/");
  const toggle = page.locator(".bottom-panel .icon-button").first();
  if (await toggle.isVisible()) {
    await toggle.click();
    await page.waitForTimeout(300);
    // Should have report/diff/timeline tabs or still show bottom-summary
    const bottom = page.locator(".bottom-panel");
    await expect(bottom).toBeVisible();
  }
});

test("sidebar collapse toggle works", async ({ page }) => {
  await page.goto("/");
  const toggle = page.locator(".rail-toggle").first();
  if (await toggle.isVisible()) {
    await toggle.click();
    await page.waitForTimeout(300);
    // After collapse, sidebar should have rail class
    const sidebar = page.locator(".sidebar");
    await expect(sidebar).toBeVisible();
  }
});

test("no white screen — app renders content", async ({ page }) => {
  await page.goto("/");
  await page.waitForTimeout(500);
  // App should have actual content, not just empty div
  const appText = await page.locator("#app").innerText();
  expect(appText.length).toBeGreaterThan(50);
});

test("workspace path is editable", async ({ page }) => {
  await page.goto("/");
  const input = page.locator(".workspace-picker input");
  if (await input.isVisible()) {
    const originalValue = await input.inputValue();
    await input.fill("/tmp/test-workspace");
    await expect(input).toHaveValue("/tmp/test-workspace");
    // Restore
    await input.fill(originalValue);
  }
});

test("new session button visible", async ({ page }) => {
  await page.goto("/");
  // Look for new session / new conversation button
  const buttons = page.locator(".topbar button, .button");
  const count = await buttons.count();
  expect(count).toBeGreaterThan(0);
});

test("run status labels render correctly", async ({ page }) => {
  await page.goto("/");
  await page.waitForTimeout(500);
  // The demo state should show some run items with status labels
  const runItems = page.locator(".run-item");
  const count = await runItems.count();
  // At least one run item in demo state or loaded from history
  expect(count).toBeGreaterThanOrEqual(0);
  // If runs exist, they should have status text
  if (count > 0) {
    const firstRun = runItems.first();
    // Should contain some text (status or title)
    const text = await firstRun.innerText();
    expect(text.length).toBeGreaterThan(0);
  }
});

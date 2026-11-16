import { test, expect, type APIRequestContext, type BrowserContext, type Page } from "@playwright/test";

const apiBase = process.env.MARS_COLONY_API_URL || "http://127.0.0.1:8000/api/v1";

async function createSession(request: APIRequestContext) {
  const response = await request.post(`${apiBase}/sessions`, {
    data: { scenario_id: "ares-01", starter_preset_id: "balanced" },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function installSession(page: Page, auth: Record<string, string>) {
  await page.addInitScript((value) => {
    sessionStorage.setItem("marscolony.auth", JSON.stringify(value));
  }, auth);
  await page.goto("/");
}

async function openMap(page: Page) {
  await page.getByRole("button", { name: "Карта колонии" }).click();
  await expect(page.getByRole("heading", { name: "Гексагональная карта Ares-1" })).toBeVisible();
}

test("two independent players see the same hex map and can select a cell", async ({ browser, request }) => {
  const session = await createSession(request);
  const joinResponse = await request.post(`${apiBase}/sessions/${session.id}/players`, {
    data: { display_name: "Наблюдатель E2E", invite_code: session.join_code },
  });
  expect(joinResponse.ok()).toBeTruthy();
  const joined = await joinResponse.json();

  const roleResponse = await request.put(`${apiBase}/sessions/${session.id}/players/${joined.player_id}/roles`, {
    headers: { "X-Player-Token": session.owner_token },
    data: { roles: ["domain_lead"], responsibility_domains: ["transport"] },
  });
  expect(roleResponse.ok()).toBeTruthy();

  const featureResponse = await request.post(`${apiBase}/sessions/${session.id}/map/features`, {
    headers: { "X-Player-Token": session.owner_token },
    data: {
      object_type: "vehicle", name: "Ровер E2E", q: 3, r: -2,
      description: "Проверочный транспорт", expected_map_version: 0,
    },
  });
  expect(featureResponse.ok()).toBeTruthy();

  const ownerContext: BrowserContext = await browser.newContext();
  const guestContext: BrowserContext = await browser.newContext();
  const ownerPage = await ownerContext.newPage();
  const guestPage = await guestContext.newPage();
  for (const [label, page] of [["owner", ownerPage], ["guest", guestPage]] as const) {
    page.on("websocket", (socket) => {
      console.log(`[${label}] WebSocket opened: ${socket.url()}`);
      socket.on("close", () => console.log(`[${label}] WebSocket closed`));
      socket.on("framereceived", (frame) => console.log(`[${label}] WebSocket frame: ${String(frame.payload).slice(0, 160)}`));
    });
    page.on("console", (message) => { if (message.type() === "error") console.log(`[${label}] browser console: ${message.text()}`); });
    page.on("pageerror", (error) => console.log(`[${label}] page error: ${error.message}`));
  }
  await installSession(ownerPage, {
    sessionId: session.id, token: session.owner_token,
    playerId: session.owner_player_id, displayName: "Командир колонии",
  });
  await installSession(guestPage, {
    sessionId: session.id, token: joined.player_token,
    playerId: joined.player_id, displayName: joined.display_name,
  });

  await openMap(ownerPage);
  await openMap(guestPage);
  await expect(ownerPage.locator('.hex-map-marker[title="Ровер E2E"]')).toBeVisible();
  await expect(guestPage.locator('.hex-map-marker[title="Ровер E2E"]')).toBeVisible();
  console.log("Owner connection state:", await ownerPage.locator(".connection").textContent(), await ownerPage.locator(".connection").getAttribute("title"));
  console.log("Guest connection state:", await guestPage.locator(".connection").textContent(), await guestPage.locator(".connection").getAttribute("title"));
  const guestMarker = guestPage.locator('.hex-map-marker[title="Ровер E2E"]');
  const ownerMarker = ownerPage.locator('.hex-map-marker[title="Ровер E2E"]');
  const initialPosition = await guestMarker.getAttribute("style");
  const moveResponse = await request.post(`${apiBase}/sessions/${session.id}/map/features/${(await featureResponse.json()).id}/position`, {
    headers: { "X-Player-Token": joined.player_token },
    data: { q: 4, r: -2, expected_map_version: 1, reason: "Проверка синхронизации двух клиентов" },
  });
  expect(moveResponse.ok()).toBeTruthy();
  await expect.poll(async () => guestMarker.getAttribute("style"), { timeout: 15000 }).not.toBe(initialPosition);
  await expect.poll(async () => ownerMarker.getAttribute("style"), { timeout: 15000 }).not.toBe(initialPosition);

  const cell = ownerPage.locator('.hex-cell[aria-label="Гекс q=4, r=4"]');
  await cell.click();
  await expect(cell).toHaveClass(/chosen/);
  await expect(ownerPage.getByText("Выбран гекс q=4, r=4")).toBeVisible();

  const viewport = ownerPage.locator(".hex-map-viewport");
  const world = ownerPage.locator(".hex-map-world");
  const transformBeforeWheel = await world.getAttribute("style");
  await viewport.hover();
  const scrollBeforeWheel = await ownerPage.evaluate(() => window.scrollY);
  await ownerPage.mouse.wheel(0, -360);
  await expect.poll(() => world.getAttribute("style")).not.toBe(transformBeforeWheel);
  expect(await ownerPage.evaluate(() => window.scrollY)).toBe(scrollBeforeWheel);

  const transformBeforeDrag = await world.getAttribute("style");
  const dragCell = ownerPage.locator('.hex-cell[aria-label="Гекс q=-4, r=3"]');
  const dragBox = await dragCell.boundingBox();
  expect(dragBox).not.toBeNull();
  await ownerPage.mouse.move(dragBox!.x + dragBox!.width / 2, dragBox!.y + dragBox!.height / 2);
  await ownerPage.mouse.down();
  await ownerPage.mouse.move(dragBox!.x + dragBox!.width / 2 + 70, dragBox!.y + dragBox!.height / 2 + 35, { steps: 5 });
  await ownerPage.mouse.up();
  await expect.poll(() => world.getAttribute("style")).not.toBe(transformBeforeDrag);
  await expect(ownerPage.getByText("Выбран гекс q=4, r=4")).toBeVisible();

  await ownerPage.locator(".map-object-list-item").filter({ hasText: "Ровер E2E" }).click();
  const markerBox = await ownerMarker.boundingBox();
  const viewportBox = await viewport.boundingBox();
  expect(markerBox).not.toBeNull();
  expect(viewportBox).not.toBeNull();
  expect(Math.abs((markerBox!.x + markerBox!.width / 2) - (viewportBox!.x + viewportBox!.width / 2))).toBeLessThan(8);
  expect(Math.abs((markerBox!.y + markerBox!.height / 2) - (viewportBox!.y + viewportBox!.height / 2))).toBeLessThan(8);

  await ownerContext.close();
  await guestContext.close();
});

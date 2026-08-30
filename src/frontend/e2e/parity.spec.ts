import fs from "node:fs";
import path from "node:path";

import {expect, test, type Page, type TestInfo} from "@playwright/test";

import {parityFixture, parityRoutes, type ParityRoute} from "./routes";

const updateBaselines = process.env.UPDATE_PARITY_BASELINES === "1";

async function authenticate(page: Page): Promise<void> {
  const username = process.env.PARITY_USERNAME ?? "admin";
  const password = process.env.PARITY_PASSWORD ?? "admin";
  const response = await page.request.post("/api/v3/session", {
    data: {username, password},
  });
  expect(
    response.ok(),
    `Parity login failed for ${username}; set PARITY_USERNAME and PARITY_PASSWORD`,
  ).toBe(true);
}

async function resolvedPath(page: Page, route: ParityRoute): Promise<string> {
  let routePath = route.path;
  if (routePath.includes("__latest_pull__")) {
    const response = await page.request.get(
      `/api/v3/repos/${parityFixture.owner}/${parityFixture.repository}/pulls?state=all&per_page=1`,
    );
    expect(response.ok(), "Unable to discover a parity pull request").toBe(
      true,
    );
    const pulls = (await response.json()) as Array<{number: number}>;
    expect(
      pulls.length,
      "Parity repository has no pull requests",
    ).toBeGreaterThan(0);
    routePath = routePath.replace("__latest_pull__", String(pulls[0].number));
  }
  if (routePath.includes("__latest_run__")) {
    const response = await page.request.get(
      `/api/v3/repos/${parityFixture.owner}/${parityFixture.repository}/actions/runs?per_page=1`,
    );
    expect(response.ok(), "Unable to discover a parity workflow run").toBe(
      true,
    );
    const payload = (await response.json()) as {
      workflow_runs: Array<{id: number}>;
    };
    expect(
      payload.workflow_runs.length,
      "Parity repository has no workflow runs",
    ).toBeGreaterThan(0);
    routePath = routePath.replace(
      "__latest_run__",
      String(payload.workflow_runs[0].id),
    );
  }
  return routePath;
}

async function stabilize(page: Page): Promise<void> {
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-duration: 0s !important;
        caret-color: transparent !important;
        transition-duration: 0s !important;
      }
      time, [data-volatile], .runner-heartbeat { visibility: hidden !important; }
    `,
  });
  await page.evaluate(() => document.fonts.ready);
}

async function semanticSnapshot(page: Page) {
  return page.locator("body").evaluate((body) => ({
    headings: Array.from(body.querySelectorAll("h1,h2,h3")).map((node) =>
      node.textContent?.trim(),
    ),
    controls: Array.from(
      body.querySelectorAll("a,button,input,select,textarea"),
    ).map((node) => ({
      tag: node.tagName.toLowerCase(),
      name:
        node.getAttribute("aria-label") ??
        node.textContent?.trim() ??
        node.getAttribute("name"),
    })),
  }));
}

async function assertParity(
  route: ParityRoute,
  page: Page,
  legacyPage: Page,
  testInfo: TestInfo,
): Promise<void> {
  const routePath = await resolvedPath(page, route);
  await Promise.all([
    page.goto(`/ui${routePath}`),
    legacyPage.goto(`/ui-legacy${routePath}`),
  ]);
  await Promise.all([stabilize(page), stabilize(legacyPage)]);

  const legacyScreenshot = await legacyPage.screenshot({fullPage: true});
  const baselinePath = testInfo.snapshotPath(`${route.id}.png`);
  if (updateBaselines) {
    fs.mkdirSync(path.dirname(baselinePath), {recursive: true});
    fs.writeFileSync(baselinePath, legacyScreenshot);
  }

  expect(await page.screenshot({fullPage: true})).toMatchSnapshot(
    `${route.id}.png`,
    {maxDiffPixelRatio: 0.01},
  );
  expect(await semanticSnapshot(page)).toEqual(
    await semanticSnapshot(legacyPage),
  );
}

test("route manifest has unique IDs and paths", () => {
  expect(new Set(parityRoutes.map(({id}) => id)).size).toBe(
    parityRoutes.length,
  );
  expect(new Set(parityRoutes.map(({path: routePath}) => routePath)).size).toBe(
    parityRoutes.length,
  );
});

test("candidate repository routes do not overflow a narrow viewport", async ({
  page,
}) => {
  await authenticate(page);
  await page.setViewportSize({width: 480, height: 900});
  for (const route of parityRoutes.filter(
    ({state, path: routePath}) =>
      state === "candidate" && routePath.startsWith(`/${parityFixture.owner}/`),
  )) {
    const routePath = await resolvedPath(page, route);
    const response = await page.goto(`/ui${routePath}`);
    expect(response?.ok(), `${route.id} did not load`).toBe(true);
    await stabilize(page);
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(
      overflow,
      `${route.id} overflows by ${overflow}px`,
    ).toBeLessThanOrEqual(0);
  }
});

for (const route of parityRoutes) {
  test(`${route.id}: new and legacy surfaces are reachable`, async ({
    page,
    browser,
  }, testInfo) => {
    const legacyPage = await browser.newPage();
    await Promise.all([authenticate(page), authenticate(legacyPage)]);
    if (route.state === "parity") {
      await assertParity(route, page, legacyPage, testInfo);
    } else {
      const routePath = await resolvedPath(page, route);
      const [response, legacyResponse] = await Promise.all([
        page.goto(`/ui${routePath}`),
        legacyPage.goto(`/ui-legacy${routePath}`),
      ]);
      expect(response?.ok()).toBe(true);
      expect(legacyResponse?.ok()).toBe(true);
      if (route.state === "fallback") {
        await expect(
          page.getByRole("heading", {name: "This route has not migrated yet"}),
        ).toBeVisible();
      }
    }
    await legacyPage.close();
  });
}

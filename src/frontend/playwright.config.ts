import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../../test-results/frontend-parity",
  snapshotDir: "./e2e/parity-baselines",
  fullyParallel: false,
  retries: 0,
  reporter: [
    ["list"],
    ["html", {outputFolder: "../../playwright-report", open: "never"}],
  ],
  use: {
    baseURL: process.env.GITHUB_EMULATOR_URL ?? "https://github.local",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: {width: 1440, height: 1000},
      },
    },
    {
      name: "narrow",
      use: {...devices["Desktop Chrome"], viewport: {width: 480, height: 900}},
    },
  ],
});

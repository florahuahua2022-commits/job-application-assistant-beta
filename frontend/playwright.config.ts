import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

// Never start Next with a local configuration that could target production.
for (const name of [".env", ".env.local", ".env.development", ".env.development.local", ".env.production", ".env.production.local", ".env.test", ".env.test.local"]) {
  if (existsSync(name)) throw new Error("UI acceptance requires a clean checkout without dotenv files");
}

const processEssentials = new Set(["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CI", "PLAYWRIGHT_BROWSERS_PATH", "LANG", "LC_ALL"]);
for (const name of Object.keys(process.env)) {
  if (!processEssentials.has(name.toUpperCase())) delete process.env[name];
}

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: 0,
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:3100", browserName: "chromium", headless: true },
  webServer: {
    command: "pnpm exec next dev --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:3100/__test_api",
      NEXT_PUBLIC_SUPABASE_URL: "",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "",
      NEXT_PUBLIC_BETA_SUPPORT_CONTACT: "offline fixture operator",
      NEXT_TELEMETRY_DISABLED: "1",
    },
  },
});

import { expect, test, type Page } from "@playwright/test";

const messages = {
  timeout: "Evidence matching timed out before the application diagnosis finished. No Pack credit was charged. Retry the entire diagnosis. Ref: 12345678",
  output_too_long: "The evidence-matching response exceeded the safe output limit, so the application diagnosis could not finish. No Pack credit was charged. Retrying is likely to fail again; contact the beta operator with this reference. Ref: 12345678",
  invalid_response_format: "The AI provider returned an invalid evidence-matching response. No Pack credit was charged. Retry the entire diagnosis. Ref: 12345678",
  configuration_error: "Evidence matching is not configured correctly. No Pack credit was charged. Contact the beta operator and retry the entire diagnosis after the configuration is corrected. Ref: 12345678",
  rate_limited_or_busy: "The AI provider is temporarily busy or rate-limiting requests. No Pack credit was charged. Wait a few minutes, then retry the entire diagnosis. Ref: 12345678",
  network_error: "The application could not reach the AI provider. No Pack credit was charged. Wait a few minutes, then retry the entire diagnosis. Ref: 12345678",
  provider_unavailable: "The AI provider account or service is unavailable. No Pack credit was charged. Contact the beta operator, then retry the entire diagnosis after service is restored. Ref: 12345678",
  criterion_failed: "Evidence matching could not complete all job criteria. No Pack credit was charged. Retry the entire diagnosis. Ref: 12345678",
} as const;

const statuses: Record<keyof typeof messages, number> = {
  timeout: 504,
  output_too_long: 502,
  invalid_response_format: 502,
  configuration_error: 503,
  rate_limited_or_busy: 503,
  network_error: 503,
  provider_unavailable: 503,
  criterion_failed: 502,
};

async function openDiagnosis(page: Page, category: keyof typeof messages) {
  const application = { id: 1, company: "Synthetic Organisation", position_title: "Synthetic Officer", job_description: "Synthetic duties.", status: "draft", selection_criteria: "", selection_plan_json: "{}" };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://127.0.0.1:3100") return route.abort();
    if (!url.pathname.startsWith("/__test_api")) return route.continue();
    const path = url.pathname.slice("/__test_api".length);
    if (path === "/applications") return route.fulfill({ json: [application] });
    if (path === "/resumes") return route.fulfill({ json: [{ id: 1, title: "Synthetic CV", source_text: "Synthetic source", experiences_json: "[]" }] });
    if (path === "/profile") return route.fulfill({ json: { id: 1, first_name: "Synthetic", last_name: "Applicant", phone: "0000000000", email: "synthetic@example.invalid", country: "Australia", work_rights: "not_specified", referees: [] } });
    if (path === "/pack-credits/access") return route.fulfill({ json: { balance: 2 } });
    if (path === "/applications/1/decision" && route.request().method() === "POST") {
      return route.fulfill({ status: statuses[category], json: { detail: { code: category, message: messages[category] } } });
    }
    if (path === "/applications/1/decision") return route.fulfill({ json: { decision: {}, current: false } });
    if (path.endsWith("/application-requirements")) return route.fulfill({ status: 404, json: {} });
    if (path.endsWith("/release-checklist")) return route.fulfill({ status: 404, json: {} });
    return route.fulfill({ json: [] });
  });
  await page.goto("/applications?application=1");
  await page.getByRole("button", { name: "Check application", exact: true }).click();
}

for (const category of Object.keys(messages) as (keyof typeof messages)[]) {
  test(`renders ${category} diagnosis message`, async ({ page }) => {
    await openDiagnosis(page, category);
    await expect(page.locator(".applicationNotice")).toHaveText(messages[category]);
  });
}

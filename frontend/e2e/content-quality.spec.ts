import { expect, test, type Page } from "@playwright/test";

test("review findings keep factual errors visible and Dayforce advice collapsed once", async ({ page }) => {
  const advice = { type: "generation_under_utilized", severity: "advisory", blocks_release: false,
    description: "Dayforce is repeated in Technical Skills and Work Experience.", category: "consistency" };
  const error = { type: "unsupported_claim", severity: "critical", blocks_release: true,
    description: "The source says assisted; the CV says led." };
  const warning = { type: "unknown_reviewer_issue", severity: "major", display_level: "warning", blocks_release: true,
    description: "This finding needs verification." };
  const brief = { type: "resume_too_brief", severity: "advisory", blocks_release: false,
    description: "The source needs more detail." };
  const review = { status: "fail", factual_status: "fail", quality_status: "fail", results: [{ criteria_id: "tailored_resume", status: "fail", issues: [
    warning, brief,
    advice, { ...advice }, error, { ...error }, { type: "unsupported_claim", description: "No issue with content." },
    { type: "unsupported_claim", description: "" },
  ] }] };
  await showReview(page, review);
  await expect(page.getByRole("region", { name: "Error findings" }).getByRole("listitem").filter({ hasText: error.description })).toHaveCount(1);
  const suggestions = page.locator("details.reviewAdvisory");
  await expect(suggestions).toHaveCount(1);
  await expect(suggestions).not.toHaveAttribute("open", "");
  await expect(suggestions.getByRole("listitem").filter({ hasText: advice.description })).toBeHidden();
  await suggestions.locator("summary").click();
  await expect(suggestions.getByRole("listitem").filter({ hasText: advice.description })).toBeVisible();
  await expect(page.getByText("No issue with content.", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Generation needs review", { exact: true })).toHaveCount(0);
  await expect.soft(page.getByText("Factual check:", { exact: false })).toContainText("Writing quality:");
  await expect.soft(page.getByRole("region", { name: "Warning findings" })).toContainText("Verify before continuing");
  await expect.soft(page.getByText("Source detail needs assessment", { exact: true })).toBeVisible();
});

async function showReview(page: Page, review: unknown) {
  const application = { id: 1, company: "Example Organisation", position_title: "Synthetic Officer", job_description: "Support reports.", status: "draft", selection_criteria: "", selection_plan_json: "{}" };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://127.0.0.1:3100") return route.abort();
    if (!url.pathname.startsWith("/__test_api")) return route.continue();
    const path = url.pathname.slice("/__test_api".length);
    if (path === "/applications") return route.fulfill({ json: [application] });
    if (path === "/resumes") return route.fulfill({ json: [{ id: 1, title: "Synthetic CV", source_text: "Synthetic source", experiences_json: "[]" }] });
    if (path === "/profile") return route.fulfill({ json: { id: 1, first_name: "Synthetic", last_name: "Applicant", phone: "0400000000", email: "test@example.invalid", country: "Australia", work_rights: "not_specified", referees: [] } });
    if (path === "/pack-credits/access") return route.fulfill({ json: { balance: 2 } });
    if (path === "/applications/1/documents") return route.fulfill({ json: [{ id: 332, document_type: "tailored_resume", content: "Synthetic CV text", reviewer_json: JSON.stringify(review), created_at: "2026-10-29T00:00:00Z" }] });
    if (path.endsWith("/decision")) return route.fulfill({ json: {} });
    if (path.endsWith("/application-requirements")) return route.fulfill({ status: 404, json: {} });
    if (path.endsWith("/release-checklist")) return route.fulfill({ status: 404, json: {} });
    return route.fulfill({ json: [] });
  });
  await page.goto("/applications?application=1");
}


test("missing source context stays blocking with pending status lines", async ({ page }) => {
  const description = "Source context unavailable — cannot re-verify";
  await showReview(page, { status: "fail", factual_status: "pending", quality_status: "pending", results: [
    { criteria_id: "tailored_resume", status: "fail", issues: [
      { type: "evidence_mismatch", severity: "major", display_level: "warning", blocks_release: true, description },
    ] },
  ] });
  const warning = page.getByRole("region", { name: "Warning findings" });
  await expect(warning).toContainText(description);
  await expect(warning).toContainText("Verify before continuing");
  const statuses = page.getByText("Factual check:", { exact: false });
  await expect(statuses).toHaveText(/Factual check:\s*pending.*Writing quality:\s*pending/);
  await expect(statuses).not.toContainText("pass");
  await expect(page.getByRole("region", { name: "Error findings" })).toHaveCount(0);
  await expect(page.getByText("Tailored CV needs verification", { exact: true })).toBeVisible();
});

import { expect, test, type Page, type Route } from "@playwright/test";

const retry = "未能确认保存，输入已保留，请重试。";
const uncertain = "未能确认保存。职位可能已创建，请先检查已保存职位；确认没有保存后，点击“确认没有保存，重新保存”，再点击 Save Job。";

async function fixtures(page: Page) {
  const state = { mode: "success", held: null as Route | null, posts: 0, writes: 0, lists: 0,
    holdReload: false, reloadHeld: null as Route | null, completedViews: 0,
    profile: () => profile, applications: () => applications };
  let profile = { id: 1, first_name: "Synthetic", last_name: "Applicant", phone: "0400000000", email: "synthetic@example.invalid",
    country: "Australia", state: "WA", title: "Dr", work_rights: "permanent_resident", availability_notice: "one_month", writing_tone: "concise_direct", referees: [],
    updated_at: "2026-01-01T00:00:00Z" };
  const applications = [1, 2].map((id) => ({ id, company: `Company ${id}`, position_title: `Role ${id}`, job_description: "Synthetic duties",
    status: "draft", selection_criteria: "", selection_plan_json: "{}", selection_confirmations_json: "[]", archived_at: null }));
  await page.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:3100") return route.abort();
    if (!url.pathname.startsWith("/__test_api")) return route.continue();
    const path = url.pathname.slice("/__test_api".length), method = request.method();
    if (path === "/resumes/upload" && method === "POST") return route.fulfill({ json: {
      id: 10, title: "Synthetic resume", source_text: "Detected Person\n0400000000\ndetected@example.invalid",
      experiences_json: "[]", updated_at: "2026-01-01T00:00:00Z",
    } });
    if ((path === "/profile" && method === "PUT") || (path === "/applications" && method === "POST") || (/^\/applications\/\d+$/.test(path) && method === "PATCH")) {
      state.writes++;
      if (method === "POST") state.posts++;
      if (state.mode === "hold" || state.mode === "timeout") { state.held = route; return; }
      if (state.mode === "network") return route.abort("failed");
      if (state.mode === "422" || state.mode === "500") return route.fulfill({ status: Number(state.mode), json: { detail: "PRIVATE_SERVER_DETAIL" } });
      if (state.mode === "badjson") return route.fulfill({ status: 200, body: "not-json" });
      const payload = request.postDataJSON();
      if (path === "/profile") {
        profile = { ...profile, ...payload, updated_at: "2026-01-02T00:00:00Z" };
        return route.fulfill({ json: profile });
      }
      if (method === "POST") {
        const application = { ...applications[0], ...payload, id: 3 };
        applications.push(application);
        return route.fulfill({ json: application });
      }
      const application = applications.find((item) => item.id === Number(path.split("/").pop()))!;
      Object.assign(application, payload);
      return route.fulfill({ json: application });
    }
    if (path === "/profile") return route.fulfill({ json: profile });
    if (path.endsWith("/release-checklist")) state.completedViews++;
    if (state.holdReload && path === "/applications/1/documents") { state.reloadHeld = route; return; }
    if (path === "/applications") { state.lists++; return route.fulfill({ json: applications }); }
    if (path === "/health") return route.fulfill({ json: { ai_configured: false } });
    if (path === "/resumes/risk-scan") return route.fulfill({ json: { resumes: [] } });
    if (path === "/pack-credits/access") return route.fulfill({ json: { balance: 2 } });
    if (["/resumes", "/backups"].includes(path) || /\/(sources|documents|document-history)$/.test(path)) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "Offline fixture unavailable" } });
  });
  await page.clock.install();
  return state;
}

test("profile save: persistent adjacent error, retained input, timeout and successful retry", async ({ page }) => {
  const state = await fixtures(page);
  await page.goto("/");
  await page.getByText("Something is wrong? Edit it", { exact: true }).waitFor({ state: "attached" });
  await page.getByText("Contact check", { exact: true }).click();
  await page.getByText("Something is wrong? Edit it", { exact: true }).click();
  const form = page.locator("form").filter({ has: page.locator('input[name="full_name"]') }).first();
  const button = form.getByRole("button", { name: "Confirm details", exact: true });
  await expect(form.locator('[name="title"]')).toHaveValue("Dr");
  await expect(form.locator('[name="work_rights"]')).toHaveValue("permanent_resident");
  await expect(form.locator('[name="availability_notice"]')).toHaveValue("one_month");
  await expect(form.locator('[name="writing_tone"]')).toHaveValue("concise_direct");
  await form.locator('[name="full_name"]').fill("Changed Synthetic");
  state.mode = "hold";
  await button.click();
  await expect(button).toBeDisabled();
  await expect.poll(() => state.held).not.toBeNull();
  await form.evaluate((element) => element.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(state.writes).toBe(1);
  await state.held!.fulfill({ status: 422, json: { detail: "PRIVATE_SERVER_DETAIL" } });
  await expect(form.getByRole("alert")).toHaveText(retry);
  await page.clock.fastForward(60_000);
  await expect(form.getByRole("alert")).toHaveText(retry);
  for (const mode of ["500", "network", "timeout", "badjson"]) {
    state.mode = mode; state.held = null;
    await button.click();
    if (mode === "timeout") {
      await expect.poll(() => state.held).not.toBeNull();
      await page.clock.fastForward(30_001);
    }
    await expect(button).toBeEnabled();
    await expect(form.getByRole("alert")).toHaveText(retry);
    await expect(form.locator('[name="full_name"]')).toHaveValue("Changed Synthetic");
    await expect(page.getByText("PRIVATE_SERVER_DETAIL", { exact: true })).toHaveCount(0);
  }
  state.mode = "success";
  await button.click();
  await expect(form.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("Contact details confirmed. You will not need to enter them again.", { exact: true })).toBeVisible();
  expect(state.profile().work_rights).toBe("permanent_resident");
  expect(state.profile().availability_notice).toBe("one_month");
  // Confirmed global profile data must update even after the view changes.
  for (const entry of ["main", "application"]) {
    if (entry === "application") {
      await page.getByText("Edit applicant details, availability, position & organisation", { exact: true }).click();
    }
    const entryForm = entry === "main" ? form : page.locator("form").filter({ has: page.getByRole("button", { name: "Save applicant details", exact: true }) });
    state.mode = "hold"; state.held = null;
    await entryForm.getByRole("button", { name: entry === "main" ? "Confirm details" : "Save applicant details", exact: true }).click();
    await expect.poll(() => state.held).not.toBeNull();
    const before = state.completedViews;
    await page.locator(".jobList").getByRole("button", { name: entry === "main" ? /Role 2/ : /Role 1/ }).click();
    await expect.poll(() => state.completedViews).toBeGreaterThan(before);
    await form.locator('[name="full_name"]').fill(`Current unsaved ${entry}`);
    const returned = { ...state.profile(), first_name: "Global", last_name: entry, updated_at: `2026-01-0${entry === "main" ? 3 : 4}T00:00:00Z` };
    const received = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith("/__test_api/profile"));
    await state.held!.fulfill({ json: returned });
    await received;
    await page.clock.runFor(250);
    await expect(page.locator(".contactConfirm")).toContainText(`Global ${entry}`);
    await expect(form.locator('[name="full_name"]')).toHaveValue(`Current unsaved ${entry}`);
    await expect(page.locator(".selectedJob")).toContainText(entry === "main" ? "Role 2" : "Role 1");
  }
  // The detected-contact retry is exercised inside the same profile entry test.
  state.mode = "500";
  const upload = page.locator("form.quickStartUpload");
  await upload.locator('input[type="file"]').setInputFiles({ name: "synthetic.txt", mimeType: "text/plain", buffer: Buffer.from("synthetic") });
  await upload.getByRole("button", { name: "Upload & auto-fill", exact: true }).click();
  const contactRetry = page.getByRole("button", { name: "重试保存联系信息", exact: true });
  await expect(contactRetry).toBeVisible();
  state.mode = "hold"; state.held = null;
  await contactRetry.click();
  await expect.poll(() => state.held).not.toBeNull();
  const before = state.completedViews;
  await page.locator(".jobList").getByRole("button", { name: /Role 2/ }).click();
  await expect.poll(() => state.completedViews).toBeGreaterThan(before);
  await form.locator('[name="full_name"]').fill("Keep this unsaved input");
  const received = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith("/__test_api/profile"));
  await state.held!.fulfill({ json: { ...state.profile(), first_name: "Detected", last_name: "Person", updated_at: "2026-01-05T00:00:00Z" } });
  await received;
  await page.clock.runFor(250);
  await expect(page.locator(".contactConfirm")).toContainText("Detected Person");
  await expect(form.locator('[name="full_name"]')).toHaveValue("Keep this unsaved input");
  await expect(contactRetry).toHaveCount(0);
});

test("job save: uncertain POST is not repeated; PATCH retries and errors stay with their application", async ({ page }) => {
  const state = await fixtures(page);
  const create = page.locator("#add-job form");
  const save = create.getByRole("button", { name: "Save Job", exact: true });
  async function fillJob() {
    await page.goto("/");
    await create.locator('[name="company"]').fill("New Synthetic Company");
    await create.locator('[name="position_title"]').fill("New Synthetic Role");
    await create.locator('[name="job_description"]').fill("Synthetic new duties");
  }
  await fillJob();
  state.mode = "hold";
  await save.click();
  await expect(save).toBeDisabled();
  await expect.poll(() => state.held).not.toBeNull();
  await state.held!.fulfill({ status: 422, json: { detail: "PRIVATE_SERVER_DETAIL" } });
  await expect(create.getByRole("alert")).toHaveText(retry);
  await expect(save).toBeEnabled();
  for (const mode of ["500", "network", "timeout", "badjson"]) {
    if (mode !== "500") await fillJob();
    state.mode = mode; state.held = null;
    await save.click();
    if (mode === "timeout") {
      await expect.poll(() => state.held).not.toBeNull();
      await page.clock.fastForward(30_001);
    }
    await expect(create.getByRole("alert")).toHaveText(uncertain);
    await expect(save).toBeDisabled();
    const posts = state.posts, lists = state.lists;
    await create.evaluate((element) => element.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await create.getByRole("button", { name: "检查已保存职位", exact: true }).click();
    await expect.poll(() => state.lists).toBe(lists + 1);
    expect(state.posts).toBe(posts);
    await expect(create.locator('[name="company"]')).toHaveValue("New Synthetic Company");
    await expect(create.getByRole("alert")).toHaveText(uncertain);
    state.mode = "422";
    await create.getByRole("button", { name: "确认没有保存，重新保存", exact: true }).click();
    await expect(save).toBeEnabled();
    expect(state.posts).toBe(posts);
    await save.click();
    await expect.poll(() => state.posts).toBe(posts + 1);
    await expect(create.getByRole("alert")).toHaveText(retry);
  }
  await fillJob();
  state.mode = "success";
  await save.click();
  await expect(page.locator(".selectedJob")).toContainText("New Synthetic Role");
  await expect(create.locator('[name="company"]')).toHaveValue("");
  const edit = page.locator("form").filter({ has: page.locator('[name="submission_reference"]') });
  async function openEdit(id: number) {
    await page.locator(".jobList").getByRole("button", { name: new RegExp(`Role ${id}`) }).click();
    await page.getByText("Edit job or resume details", { exact: true }).click();
    await page.getByText("Edit saved job details", { exact: true }).click();
  }
  await openEdit(1);
  await edit.locator('[name="company"]').fill("Unsaved Company");
  state.mode = "hold"; state.held = null;
  await edit.getByRole("button", { name: "Save job changes", exact: true }).click();
  await expect.poll(() => state.held).not.toBeNull();
  await openEdit(2);
  await state.held!.fulfill({ status: 500, json: { detail: "PRIVATE_SERVER_DETAIL" } });
  await expect(edit.getByRole("alert")).toHaveCount(0);
  await expect(edit.getByRole("button", { name: "Save job changes", exact: true })).toBeEnabled();
  await openEdit(1);
  await expect(edit.getByRole("alert")).toHaveText(retry);
  await expect(edit.locator('[name="company"]')).toHaveValue("Company 1");
  await expect(edit.getByRole("button", { name: "检查已保存职位" })).toHaveCount(0);
  state.mode = "success";
  await edit.getByRole("button", { name: "Save job changes", exact: true }).click();
  await expect(edit.getByRole("alert")).toHaveCount(0);
  state.holdReload = true;
  await edit.getByRole("button", { name: "Save job changes", exact: true }).click();
  await expect.poll(() => state.reloadHeld).not.toBeNull();
  await openEdit(2);
  const lateResponse = page.waitForResponse((response) => response.url().endsWith("/__test_api/applications/1/documents"));
  await state.reloadHeld!.fulfill({ json: [{ id: 999, application_id: 1, document_type: "cover_letter", content: "STALE_RELOAD_DOCUMENT",
    created_at: "2026-01-01T00:00:00Z", reviewer_json: "{}", trace_json: "{}", structured_content_json: "{}", used_experiences_json: "[]" }] });
  await lateResponse;
  await page.clock.runFor(250);
  await expect(page.locator(".selectedJob")).toContainText("Role 2");
  await expect(page.locator(".editor")).toHaveCount(0);
  state.holdReload = false;
  await openEdit(1);
  state.mode = "hold"; state.held = null;
  await edit.getByRole("button", { name: "Save job changes", exact: true }).click();
  await expect.poll(() => state.held).not.toBeNull();
  await openEdit(2);
  const beforeReturn = state.completedViews;
  await openEdit(1);
  await expect.poll(() => state.completedViews).toBeGreaterThan(beforeReturn);
  await edit.locator('[name="company"]').fill("New unsaved input after returning");
  const beforeLateSave = state.completedViews;
  const lateSaveResponse = page.waitForResponse((response) => response.request().method() === "PATCH" && response.url().endsWith("/__test_api/applications/1"));
  await state.held!.fulfill({ json: { id: 1, company: "Old saved input", position_title: "Role 1", job_description: "Synthetic duties", status: "draft" } });
  await lateSaveResponse;
  await page.clock.runFor(250);
  expect(state.completedViews).toBe(beforeLateSave);
  await expect(edit.locator('[name="company"]')).toHaveValue("New unsaved input after returning");
  await expect(page.locator(".jobList").getByRole("button", { name: /Role 1/ })).toContainText("Old saved input");
  await page.getByText("Edit applicant details, availability, position & organisation", { exact: true }).click();
  const identity = page.locator("form").filter({ has: page.getByRole("button", { name: "Save job details", exact: true }) });
  state.mode = "hold"; state.held = null;
  await identity.getByRole("button", { name: "Save job details", exact: true }).click();
  await expect.poll(() => state.held).not.toBeNull();
  await openEdit(2);
  const identityResponse = page.waitForResponse((response) => response.request().method() === "PATCH" && response.url().endsWith("/__test_api/applications/1"));
  const changedIdentity = { ...state.applications()[0], position_title: "Updated Identity A" };
  Object.assign(state.applications()[0], changedIdentity);
  await state.held!.fulfill({ json: changedIdentity });
  await identityResponse;
  await page.clock.runFor(250);
  await expect(page.locator(".jobList").getByRole("button", { name: /Updated Identity A/ })).toBeVisible();
  await expect(page.locator(".selectedJob")).toContainText("Role 2");
  // A successful POST must refresh the list and announce success without opening it.
  await fillJob();
  state.mode = "hold"; state.held = null;
  await save.click();
  await expect.poll(() => state.held).not.toBeNull();
  await page.locator(".jobList").getByRole("button", { name: /Role 2/ }).click();
  await create.locator('[name="company"]').fill("Current form input");
  const backgroundCreated = { ...state.applications()[0], id: 4, company: "Background company", position_title: "Background created role" };
  state.applications().push(backgroundCreated);
  const beforeLists = state.lists;
  const createdResponse = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/__test_api/applications"));
  await state.held!.fulfill({ json: backgroundCreated });
  await createdResponse;
  await expect.poll(() => state.lists).toBeGreaterThan(beforeLists);
  await expect(page.locator(".jobList").getByRole("button", { name: /Background created role/ })).toBeVisible();
  await expect(page.locator(".selectedJob")).toContainText("Role 2");
  await expect(create.locator('[name="company"]')).toHaveValue("Current form input");
  await expect(page.getByText("Job saved. Diagnose the application, resolve any material questions, then generate the application pack.", { exact: true })).toBeVisible();
});

"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { createClient, Session } from "@supabase/supabase-js";

const api = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || "";
const supabase = supabaseUrl && supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;
type Experience = { id: string; role_title: string; organization: string; responsibility: string; context: string; result: string; no_result_data: boolean };
type CkbEvidence = { evidence_id: string; evidence_type: string; source_section: string; source_text: string };
type Resume = { id: number; title: string; source_text: string; experiences_json?: string; ckb_json?: string };
type SelectionPlanItem = { criteria_id: string; criteria_text: string; allocated_word_limit: number; matched_evidence: string[]; match_type: string; coverage: string; evidence_status: "strong" | "transferable" | "weak" };
type Application = { id: number; company: string; position_title: string; job_url?: string; job_description: string; selection_criteria?: string; selection_plan_json?: string; selection_confirmations_json?: string; status: string; submission_reference?: string; submitted_at?: string };
type GeneratedDocument = { id: number; document_type: string; content: string; used_experiences_json?: string; reviewer_json?: string; run_id?: string; trace_json?: string; created_at: string };
type ReviewerResult = { status: "pass" | "fail"; results: { criteria_id: string; status: "pass" | "fail"; issues: { type: string; severity?: "critical" | "major" | "advisory"; description: string; recommended_action?: string }[]; recommendation?: string }[] };
type QualityIssue = { severity: "error" | "warning"; code: string; message: string; document_type?: string };
type QualityResult = { ready: boolean; issues: QualityIssue[]; checked_documents: string[] };
type ResumeContentCheckItem = { field: string; label: string; value: string; status: "matched" | "review" | "missing"; message: string };
type ResumeContentCheckResult = { ready: boolean; matched_count: number; review_count: number; missing_count: number; items: ResumeContentCheckItem[] };
type Backup = { filename: string; size: number; created_at: string };
type Referee = { organisation: string; name: string; position_title: string; phone: string; relationship: string; email: string; postal_address?: string; suburb?: string; state: string; postcode?: string; country: string };
type Profile = { id: number; title?: string; first_name: string; last_name: string; preferred_name?: string; phone: string; email: string; postal_address?: string; suburb?: string; state: string; postcode?: string; country: string; work_rights: string; availability_notice: string; target_direction?: string; motivation?: string; writing_tone: string; preferences_notes?: string; referees: Referee[]; updated_at: string };
type JobFields = { company: string; position_title: string; job_url: string; job_description: string; selection_criteria: string };
type ContactGuess = { full_name: string; phone: string; email: string };
type SelectionCriteriaAccess = { unlimited: boolean; included_credits: number; referral_credits: number; used_credits: number; remaining_credits: number | null; referral_code: string | null; referral_claimed: boolean };

const packTypes = ["tailored_resume", "cover_letter", "selection_criteria"] as const;
const labels: Record<string, string> = {
  tailored_resume: "Tailored CV",
  cover_letter: "Cover Letter",
  selection_criteria: "Selection Criteria",
};
const applicationStatuses = ["draft", "ready_to_apply", "applied"] as const;
const statusLabels: Record<string, string> = { draft: "Draft", ready_to_apply: "Ready", applied: "Applied" };

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(!supabase);
  const [authNotice, setAuthNotice] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [notice, setNotice] = useState("Connecting to your local workspace…");
  const [packNotice, setPackNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedApplication, setSelectedApplication] = useState<number | null>(null);
  const [documents, setDocuments] = useState<GeneratedDocument[]>([]);
  const [activeType, setActiveType] = useState<string>("tailored_resume");
  const [draftText, setDraftText] = useState("");
  const [qualityResult, setQualityResult] = useState<QualityResult | null>(null);
  const [finalCheckState, setFinalCheckState] = useState<"idle" | "checking">("idle");
  const [resumeContentCheck, setResumeContentCheck] = useState<ResumeContentCheckResult | null>(null);
  const [resumeCheckState, setResumeCheckState] = useState<"idle" | "checking" | "done" | "error">("idle");
  const [statusFilter, setStatusFilter] = useState("all");
  const [backups, setBackups] = useState<Backup[]>([]);
  const [resumeUploadState, setResumeUploadState] = useState("idle");
  const [jobImportState, setJobImportState] = useState("idle");
  const [jobFields, setJobFields] = useState<JobFields>({ company: "", position_title: "", job_url: "", job_description: "", selection_criteria: "" });
  const [draftSaveState, setDraftSaveState] = useState<"saved" | "dirty" | "saving" | "error">("saved");
  const [rawJobAd, setRawJobAd] = useState("");
  const [adParseState, setAdParseState] = useState("idle");
  const [adWarnings, setAdWarnings] = useState<string[]>([]);
  const [confirmedApplication, setConfirmedApplication] = useState<number | null>(null);
  const [experiences, setExperiences] = useState<Experience[]>([]);
  const [resultPromptsShown, setResultPromptsShown] = useState<string[]>([]);
  const [contactGuess, setContactGuess] = useState<ContactGuess>({ full_name: "", phone: "", email: "" });
  const [selectionAccess, setSelectionAccess] = useState<SelectionCriteriaAccess | null>(null);
  const [referralCode, setReferralCode] = useState("");
  const [exportTemplate, setExportTemplate] = useState<"classic" | "modern" | "traditional">("classic");

  async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
    return window.fetch(input, { ...init, headers });
  }

  async function refresh() {
    const [profileResponse, resumeResponse, applicationResponse, backupResponse, selectionAccessResponse] = await Promise.all([
      authenticatedFetch(`${api}/profile`),
      authenticatedFetch(`${api}/resumes`),
      authenticatedFetch(`${api}/applications`),
      authenticatedFetch(`${api}/backups`),
      authenticatedFetch(`${api}/selection-criteria/access`),
    ]);
    if (profileResponse.ok) setProfile(await profileResponse.json());
    if (resumeResponse.ok) setResumes(await resumeResponse.json());
    if (applicationResponse.ok) setApplications(await applicationResponse.json());
    if (backupResponse.ok) setBackups(await backupResponse.json());
    if (selectionAccessResponse.ok) setSelectionAccess(await selectionAccessResponse.json());
  }

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setAuthReady(true);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!authReady || (supabase && !session)) return;
    authenticatedFetch(`${api}/health`)
      .then((response) => response.json())
      .then((health) => setNotice(health.ai_configured
        ? "Ready. Add a job and generate your application pack."
        : "Add an API key locally before generating application materials."))
      .catch(() => setNotice("The local service is not running. Start the app and refresh this page."));
    refresh();
  }, [authReady, session?.access_token]);

  useEffect(() => {
    if (!resumes[0]) return;
    try { setExperiences(JSON.parse(resumes[0].experiences_json || "[]")); } catch { setExperiences([]); }
  }, [resumes]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    const form = new FormData(event.currentTarget);
    setAuthNotice("Signing in…");
    const { error } = await supabase.auth.signInWithPassword({
      email: String(form.get("email") || "").trim(),
      password: String(form.get("password") || ""),
    });
    setAuthNotice(error ? error.message : "");
  }

  async function signOut() {
    if (supabase) await supabase.auth.signOut();
    setProfile(null);
    setResumes([]);
    setApplications([]);
    setDocuments([]);
  }

  const latestDocuments = useMemo(() => {
    const result: Record<string, GeneratedDocument> = {};
    for (const document of documents) {
      if (!result[document.document_type]) result[document.document_type] = document;
    }
    return result;
  }, [documents]);

  const activeDocument = latestDocuments[activeType];
  const activeReviewer = useMemo(() => {
    if (!activeDocument?.reviewer_json) return null;
    try { return JSON.parse(activeDocument.reviewer_json) as ReviewerResult; } catch { return null; }
  }, [activeDocument?.reviewer_json]);
  const activeEvidence = useMemo(() => {
    if (!activeDocument?.used_experiences_json) return [];
    try {
      const ids = JSON.parse(activeDocument.used_experiences_json) as string[];
      return ids.map((id) => {
        const experience = experiences.find((item) => item.id === id);
        return experience
          ? { id, label: [experience.role_title, experience.organization].filter(Boolean).join(" — ") || experience.responsibility }
          : { id, label: "Source excerpt from the uploaded Master CV" };
      });
    } catch {
      return [];
    }
  }, [activeDocument?.used_experiences_json, experiences]);
  useEffect(() => {
    setDraftText(activeDocument?.content || "");
    setDraftSaveState("saved");
  }, [activeDocument]);

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const value = (name: string) => String(form.get(name) || "").trim();
    const nameParts = value("full_name").split(/\s+/).filter(Boolean);
    const referees = [0, 1].flatMap((index) => {
      if (!value(`referee_${index}_name`)) return [];
      return [{
        organisation: value(`referee_${index}_organisation`), name: value(`referee_${index}_name`),
        position_title: value(`referee_${index}_position_title`), phone: value(`referee_${index}_phone`),
        relationship: value(`referee_${index}_relationship`), email: value(`referee_${index}_email`),
        postal_address: "", suburb: "", state: "WA", postcode: "", country: "Australia",
      }];
    });
    const incompleteReferee = referees.find((referee) => !referee.organisation || !referee.position_title || !referee.phone || !referee.relationship || !referee.email);
    if (incompleteReferee) return setNotice("Complete every field for each referee you add, or clear the referee name to leave it out.");
    const payload = {
      title: value("title"), first_name: nameParts[0] || "", last_name: nameParts.slice(1).join(" ") || nameParts[0] || "", preferred_name: value("preferred_name"),
      phone: value("phone"), email: value("email"), postal_address: profile?.postal_address || "", suburb: profile?.suburb || "", state: profile?.state || "WA",
      postcode: profile?.postcode || "", country: profile?.country || "Australia", work_rights: value("work_rights") || profile?.work_rights || "not_specified", availability_notice: value("availability_notice") || "not_specified",
      target_direction: value("target_direction"), motivation: value("motivation"), writing_tone: value("writing_tone") || "natural_professional", preferences_notes: value("preferences_notes"),
      referees,
    };
    const response = await authenticatedFetch(`${api}/profile`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not save the applicant profile.");
    setProfile(result);
    setNotice("Contact details confirmed. You will not need to enter them again.");
  }

  function detectContact(sourceText: string): ContactGuess {
    const lines = sourceText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const email = sourceText.match(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i)?.[0] || "";
    const phone = sourceText.match(/(?<!\d)(?:\+?61[\s().-]*4|04)(?:[\s().-]*\d){8}(?!\d)/)?.[0]?.replace(/\s+/g, " ") || "";
    const full_name = lines.slice(0, 12).find((line) => {
      const words = line.split(/\s+/);
      return words.length >= 2 && words.length <= 5 && !/[\d@|]/.test(line) && !/resume|curriculum|vitae|profile|summary/i.test(line);
    }) || "";
    return { full_name, phone, email };
  }

  async function saveDetectedContact(guess: ContactGuess) {
    if (!guess.full_name || !guess.phone || !guess.email) return false;
    const nameParts = guess.full_name.split(/\s+/).filter(Boolean);
    const payload = {
      title: profile?.title || "", first_name: nameParts[0], last_name: nameParts.slice(1).join(" ") || nameParts[0], preferred_name: profile?.preferred_name || "",
      phone: guess.phone, email: guess.email, postal_address: profile?.postal_address || "", suburb: profile?.suburb || "", state: profile?.state || "WA", postcode: profile?.postcode || "", country: profile?.country || "Australia",
      work_rights: profile?.work_rights || "not_specified", availability_notice: profile?.availability_notice || "not_specified", referees: profile?.referees || [],
      target_direction: profile?.target_direction || "", motivation: profile?.motivation || "", writing_tone: profile?.writing_tone || "natural_professional", preferences_notes: profile?.preferences_notes || "",
    };
    const response = await authenticatedFetch(`${api}/profile`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) return false;
    setProfile(await response.json());
    return true;
  }

  async function saveResume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = { title: String(form.get("title")), source_text: String(form.get("source_text")), experiences_json: JSON.stringify(experiences) };
    const current = resumes[0];
    const response = await authenticatedFetch(current ? `${api}/resumes/${current.id}` : `${api}/resumes`, {
      method: current ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice(response.ok ? "Master Resume saved. You only need to update it when your experience changes." : "Could not save the Master Resume.");
    if (response.ok) {
      setResumeContentCheck(null);
      setResumeCheckState("idle");
      refresh();
    }
  }

  async function runResumeContentCheck(resumeId = resumes[0]?.id) {
    if (!resumeId) return setNotice("Upload or save your Master Resume before running Content Check.");
    setResumeCheckState("checking");
    const response = await authenticatedFetch(`${api}/resumes/${resumeId}/content-check`);
    const result = await response.json();
    if (!response.ok) {
      setResumeCheckState("error");
      return setNotice(result.detail || "Could not compare the extracted details with your CV.");
    }
    setResumeContentCheck(result);
    setResumeCheckState("done");
    setNotice(result.ready
      ? "CV Content Check passed. Every extracted field was found in the uploaded CV."
      : "CV Content Check finished. Review the highlighted details before generating documents.");
  }

  function addExperience() {
    setExperiences((current) => [...current, { id: crypto.randomUUID(), role_title: "", organization: "", responsibility: "", context: "", result: "", no_result_data: false }]);
  }

  function updateExperience(id: string, field: keyof Experience, value: string | boolean) {
    setExperiences((current) => current.map((item) => item.id === id ? { ...item, [field]: value } : item));
  }

  function promptForResult(experience: Experience) {
    if (experience.result.trim() || experience.no_result_data || resultPromptsShown.includes(experience.id)) return;
    setResultPromptsShown((current) => [...current, experience.id]);
    setNotice("Does this experience have a measurable result? A rough range is fine. If not, tick ‘No result data available’.");
  }

  async function uploadResume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setResumeUploadState("uploading");
    const response = await authenticatedFetch(`${api}/resumes/upload`, { method: "POST", body: new FormData(event.currentTarget) });
    const result = await response.json();
    if (!response.ok) {
      setResumeUploadState("error");
      return setNotice(result.detail || "Could not read this resume file.");
    }
    setResumeUploadState("saved");
    let extractedExperienceCount = 0;
    try { extractedExperienceCount = JSON.parse(result.experiences_json || "[]").length; } catch { extractedExperienceCount = 0; }
    const guess = detectContact(result.source_text || "");
    setContactGuess(guess);
    const contactSaved = await saveDetectedContact(guess);
    await refresh();
    await runResumeContentCheck(result.id);
    const experienceMessage = extractedExperienceCount
      ? ` We also created ${extractedExperienceCount} work experience ${extractedExperienceCount === 1 ? "record" : "records"} for you to review.`
      : " We kept the full CV text; add structured experience only if you want to strengthen the generated evidence.";
    setNotice((contactSaved
      ? "CV uploaded. We found and saved your name, phone and email — please check them once."
      : "CV uploaded. Check the missing contact detail below; the rest has already been filled in.") + experienceMessage);
  }

  async function importJobLink() {
    if (!jobFields.job_url.trim()) return setNotice("Paste the job link first.");
    setJobImportState("importing");
    try {
      const response = await authenticatedFetch(`${api}/applications/import-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_url: jobFields.job_url.trim() }),
      });
      const result = await response.json();
      if (!response.ok) {
        setJobImportState("error");
        return setNotice(result.detail || "This job website could not be read automatically. You can still enter the details below.");
      }
      setJobFields((current) => ({
        ...current,
        company: result.company || current.company,
        position_title: result.position_title || current.position_title,
        job_description: result.job_description || current.job_description,
        job_url: result.job_url || current.job_url,
      }));
      setJobImportState("done");
      setNotice(result.source === "structured_job_posting" || result.source === "page_body"
        ? "Job details imported. Please review them, then save the job."
        : "The page supplied only a short summary. Paste the full advertisement below for the best result.");
    } catch {
      setJobImportState("error");
      setNotice("The automatic reader could not connect. Your link is still in the form; paste the job details manually below.");
    }
  }

  async function parseFullJobAd() {
    if (!rawJobAd.trim()) return setNotice("Paste the full job advertisement first.");
    setAdParseState("parsing");
    try {
      const response = await authenticatedFetch(`${api}/applications/parse-ad`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_text: rawJobAd }),
      });
      const result = await response.json();
      if (!response.ok) {
        setAdParseState("error");
        return setNotice(result.detail || "The job advertisement could not be separated.");
      }
      setJobFields((current) => ({
        ...current,
        company: result.company || current.company,
        position_title: result.position_title || current.position_title,
        job_description: result.job_description,
        selection_criteria: result.selection_criteria || current.selection_criteria,
      }));
      setAdWarnings(result.warnings || []);
      setAdParseState("done");
      setNotice(result.warnings?.length
        ? "Job details extracted, but warnings need your review before saving."
        : "Job details extracted. Confirm the organisation and position title, then save the job.");
    } catch {
      setAdParseState("error");
      setNotice("The job advertisement could not be processed. Your pasted text has not been removed.");
    }
  }

  async function createApplication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const payload = jobFields;
    const response = await authenticatedFetch(`${api}/applications`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) return setNotice("Could not save this job. Check the required fields and try again.");
    const application = await response.json();
    formElement.reset();
    setJobFields({ company: "", position_title: "", job_url: "", job_description: "", selection_criteria: "" });
    setJobImportState("idle");
    setRawJobAd("");
    setAdWarnings([]);
    setAdParseState("idle");
    await refresh();
    await openApplication(application.id);
    setNotice("Job saved. Click Generate Application Pack when you are ready.");
  }

  async function updateSavedJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedApplication) return;
    const form = new FormData(event.currentTarget);
    const payload = {
      company: String(form.get("company") || "").trim(),
      position_title: String(form.get("position_title") || "").trim(),
      job_url: String(form.get("job_url") || "").trim(),
      job_description: String(form.get("job_description") || "").trim(),
      selection_criteria: String(form.get("selection_criteria") || "").trim(),
      submission_reference: String(form.get("submission_reference") || "").trim(),
    };
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not update the saved job details.");
    setApplications((current) => current.map((application) => application.id === result.id ? result : application));
    setConfirmedApplication(null);
    setQualityResult(null);
    setNotice("Saved job details updated. Confirm them again before generating or applying.");
  }

  async function openApplication(id: number) {
    setSelectedApplication(id);
    setPackNotice("");
    setConfirmedApplication(null);
    setQualityResult(null);
    const response = await authenticatedFetch(`${api}/applications/${id}/documents`);
    const loaded = response.ok ? await response.json() : [];
    setDocuments(loaded);
    const firstAvailable = packTypes.find((type) => loaded.some((document: GeneratedDocument) => document.document_type === type));
    setActiveType(firstAvailable || "tailored_resume");
  }

  async function generatePack() {
    if (!selectedApplication || !resumes.length) return;
    const showPackNotice = (message: string) => {
      setNotice(message);
      setPackNotice(message);
    };
    const application = applications.find((item) => item.id === selectedApplication);
    const includesSelectionCriteria = Boolean(application?.selection_criteria?.trim());
    let documentTypes: readonly (typeof packTypes[number])[] = includesSelectionCriteria
      ? ["tailored_resume", "cover_letter", "selection_criteria"]
      : ["tailored_resume", "cover_letter"];
    let selectionCriteriaSkipped = false;
    if (includesSelectionCriteria) {
      const accessResponse = await authenticatedFetch(`${api}/selection-criteria/access`);
      if (!accessResponse.ok) return showPackNotice("Could not verify Selection Criteria access. Please try again.");
      const currentAccess = await accessResponse.json() as SelectionCriteriaAccess;
      setSelectionAccess(currentAccess);
      if (!currentAccess.unlimited && !currentAccess.remaining_credits) {
        documentTypes = ["tailored_resume", "cover_letter"];
        selectionCriteriaSkipped = true;
      }
    }
    const packId = crypto.randomUUID();
    setBusy(true);
    showPackNotice("Preparing your complete application pack…");
    setDocuments([]);
    setQualityResult(null);
    setActiveType("tailored_resume");
    const created: GeneratedDocument[] = [];
    try {
      for (let index = 0; index < documentTypes.length; index += 1) {
        const documentType = documentTypes[index];
        showPackNotice(`Creating application pack: ${index + 1} of ${documentTypes.length} — ${labels[documentType]}…`);
        const response = await authenticatedFetch(`${api}/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ application_id: selectedApplication, document_type: documentType, pack_id: packId }),
          signal: AbortSignal.timeout(180_000),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `${labels[documentType]} could not be generated.`);
        created.push(result);
      }
      setDocuments((current) => [...created.reverse(), ...current]);
      if (includesSelectionCriteria) {
        const accessResponse = await authenticatedFetch(`${api}/selection-criteria/access`);
        if (accessResponse.ok) setSelectionAccess(await accessResponse.json());
      }
      setActiveType("tailored_resume");
      if (selectionCriteriaSkipped) {
        setQualityResult(null);
        showPackNotice("CV and Cover Letter created. Selection Criteria was skipped because no credits remain. Add a credit before generating and checking the complete pack.");
        return;
      }
      const checkResponse = await authenticatedFetch(`${api}/applications/${selectedApplication}/quality-check`);
      if (checkResponse.ok) {
        const check = await checkResponse.json() as QualityResult;
        setQualityResult(check);
        showPackNotice(check.ready
          ? "Application pack created and automatically checked. Confirm your personal facts, then continue."
          : "Application pack created. The automatic check found items that still need attention.");
      } else {
        showPackNotice("Application pack created. Run Final Check before continuing.");
      }
    } catch (error) {
      if (created.length) setDocuments([...created].reverse());
      const detail = error instanceof Error ? error.message : "The application pack could not be completed.";
      showPackNotice(`${detail} This pack is incomplete, so Final Check is unavailable. The failed attempt has not used today's completed-pack allowance. Click Generate Application Pack to retry.`);
    } finally {
      setBusy(false);
    }
  }

  async function claimReferral(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const response = await authenticatedFetch(`${api}/selection-criteria/referral`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referral_code: referralCode.trim() }),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not apply this referral code.");
    setSelectionAccess(result);
    setReferralCode("");
    setNotice("Referral recorded. The person who invited you has received one Selection Criteria credit.");
  }

  async function saveDraft() {
    if (!activeDocument) return;
    setDraftSaveState("saving");
    const response = await authenticatedFetch(`${api}/documents/${activeDocument.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: draftText }),
    });
    if (!response.ok) {
      setDraftSaveState("error");
      return setNotice("Could not save your edits.");
    }
    const updated = await response.json();
    setDocuments((current) => current.map((document) => document.id === updated.id ? updated : document));
    setQualityResult(null);
    setDraftSaveState("saved");
    setNotice(`${labels[activeType]} edits saved.`);
  }

  async function runFinalCheck() {
    if (!selectedApplication || finalCheckState === "checking") return null;
    if (draftSaveState === "dirty" || draftSaveState === "saving") {
      setNotice("Save your document changes before running Final Check.");
      return null;
    }
    setFinalCheckState("checking");
    setQualityResult(null);
    setNotice("Running Final Check…");
    let response: Response;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    try {
      response = await authenticatedFetch(`${api}/applications/${selectedApplication}/quality-check`, {
        signal: controller.signal,
      });
    } catch (error) {
      setNotice(error instanceof DOMException && error.name === "AbortError"
        ? "Final Check timed out after 45 seconds. The online service may still be waking up; wait a moment and try again."
        : "Final Check could not reach the service. Wait for the online service to wake up, then try again.");
      return null;
    } finally {
      window.clearTimeout(timeout);
      setFinalCheckState("idle");
    }

    const contentType = response.headers.get("content-type") || "";
    const result = contentType.includes("application/json")
      ? await response.json().catch(() => null)
      : null;
    if (!response.ok || !result) {
      const detail = typeof result?.detail === "string" ? result.detail : "";
      const fallback = response.status === 401
        ? "Your session has expired. Sign in again, then run Final Check."
        : response.status >= 500
          ? "Final Check is temporarily unavailable. Wait a moment, then try again."
          : "Could not run Final Check. Refresh the page and try again.";
      setNotice(detail || fallback);
      return null;
    }
    setQualityResult(result);
    setNotice(result.ready ? "Content and grammar check passed. Review any warnings, then continue to the application page." : "Content and grammar check found errors. Fix them before applying.");
    return result as QualityResult;
  }

  async function reviewAndApply() {
    if (!selectedApplication) return;
    const application = applications.find((item) => item.id === selectedApplication);
    if (!application?.job_url?.trim()) {
      const message = "Add the application link under Edit saved job details before opening the employer page.";
      setNotice(message);
      window.alert(message);
      return;
    }
    if (!qualityResult) {
      const message = "Run Final Check first and review its result before opening the employer page.";
      setNotice(message);
      window.alert(message);
      return;
    }
    if (!qualityResult.ready) {
      const message = "Final Check still has errors. Fix them, save the changes, and run Final Check again.";
      setNotice(message);
      window.alert(message);
      return;
    }
    let requiredConfirmations: string[] = [];
    let confirmedCriteria: string[] = [];
    try {
      const plan = JSON.parse(application.selection_plan_json || "{}");
      requiredConfirmations = (plan.items || []).filter((item: SelectionPlanItem) => item.evidence_status === "transferable" || item.evidence_status === "weak").map((item: SelectionPlanItem) => item.criteria_id);
      confirmedCriteria = JSON.parse(application.selection_confirmations_json || "[]");
    } catch { requiredConfirmations = []; confirmedCriteria = []; }
    if (requiredConfirmations.some((criteriaId) => !confirmedCriteria.includes(criteriaId))) {
      const message = "Review and confirm every Transferable or Weak Selection Criterion before continuing.";
      setNotice(message);
      window.alert(message);
      return;
    }
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/prepare-submission`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Add the job application link before continuing.");
    const statusResponse = await authenticatedFetch(`${api}/applications/${selectedApplication}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "ready_to_apply" }),
    });
    if (statusResponse.ok) {
      const updated = await statusResponse.json();
      setApplications((current) => current.map((item) => item.id === updated.id ? updated : item));
    }
    setNotice("Application moved to Ready. Complete it in any browser, then return and click Mark as Applied.");
    window.open(result.job_url, "_blank", "noopener,noreferrer");
  }

  async function copyApplicationLink() {
    const application = applications.find((item) => item.id === selectedApplication);
    if (!application?.job_url?.trim()) {
      const message = "Add the application link under Edit saved job details first.";
      setNotice(message);
      window.alert(message);
      return;
    }
    await navigator.clipboard.writeText(application.job_url);
    setNotice("Application link copied. Open it in Chrome or any browser you prefer.");
  }

  async function markApplied() {
    if (!selectedApplication) return;
    const application = applications.find((item) => item.id === selectedApplication);
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/submission`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ submission_reference: application?.submission_reference || null }),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not record the submitted application.");
    setApplications((current) => current.map((application) => application.id === result.id ? result : application));
    setNotice(result.submission_reference
      ? `Application marked as Applied. Confirmation: ${result.submission_reference}`
      : "Application marked as Applied and the submission date was recorded.");
  }

  async function updateApplicationStatus(application: Application, status: string) {
    const response = await authenticatedFetch(`${api}/applications/${application.id}/status`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not update the application status.");
    setApplications((current) => current.map((item) => item.id === result.id ? result : item));
    setNotice(`${result.position_title} moved to ${statusLabels[result.status]}.`);
  }

  async function createLocalBackup() {
    const response = await authenticatedFetch(`${api}/backups`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not create the backup.");
    setBackups((current) => [result, ...current]);
    setNotice("Local backup created. API keys and passwords were not included.");
  }

  async function restoreLocalBackup(backup: Backup) {
    const confirmed = window.confirm(`Restore ${backup.filename}? This replaces the current profile, resumes, jobs and generated documents. A safety backup will be created first.`);
    if (!confirmed) return;
    const response = await authenticatedFetch(`${api}/backups/${encodeURIComponent(backup.filename)}/restore`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true }),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not restore the backup.");
    setSelectedApplication(null);
    setDocuments([]);
    await refresh();
    setNotice(`Backup restored: ${backup.filename}`);
  }

  async function downloadDocument(format: "docx" | "pdf") {
    if (!activeDocument) return;
    const response = await authenticatedFetch(`${api}/documents/${activeDocument.id}/export?format=${format}&template=${exportTemplate}`);
    if (!response.ok) return setNotice("Could not download this document.");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `${labels[activeType] || "Document"}.${format}`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function downloadPack(format: "docx" | "pdf") {
    if (!selectedApplication) return;
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/export-pack?format=${format}&template=${exportTemplate}`);
    if (!response.ok) return setNotice("Could not download the application pack.");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `Application_Pack_${format.toUpperCase()}.zip`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const selected = applications.find((application) => application.id === selectedApplication);
  const selectionPlan = useMemo(() => {
    try { return JSON.parse(selected?.selection_plan_json || "{}").items as SelectionPlanItem[] || []; } catch { return []; }
  }, [selected?.selection_plan_json]);
  const confirmedSelectionCriteria = useMemo(() => {
    try { return JSON.parse(selected?.selection_confirmations_json || "[]") as string[]; } catch { return []; }
  }, [selected?.selection_confirmations_json]);
  const ckbById = useMemo(() => {
    try {
      const items = JSON.parse(resumes[0]?.ckb_json || "[]") as CkbEvidence[];
      return Object.fromEntries(items.map((item) => [item.evidence_id, item]));
    } catch { return {} as Record<string, CkbEvidence>; }
  }, [resumes]);
  const reviewerByCriteria = useMemo(() => Object.fromEntries((activeReviewer?.results || []).map((item) => [item.criteria_id, item])), [activeReviewer]);

  async function setCriterionConfirmation(criteriaId: string, checked: boolean) {
    if (!selectedApplication) return;
    const next = checked
      ? Array.from(new Set([...confirmedSelectionCriteria, criteriaId]))
      : confirmedSelectionCriteria.filter((value) => value !== criteriaId);
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/selection-confirmations`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ criteria_ids: next }),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || "Could not save this confirmation.");
    setApplications((current) => current.map((item) => item.id === result.id ? result : item));
    setNotice(checked ? "Criterion reviewed and confirmed." : "Criterion confirmation removed.");
  }

  async function downloadTrace() {
    if (!activeDocument) return;
    const response = await authenticatedFetch(`${api}/documents/${activeDocument.id}/trace`);
    if (!response.ok) return setNotice("Could not download the audit trace.");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `${labels[activeType] || "Document"}_Audit_Trace.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
  const requiredPackTypes = selected?.selection_criteria?.trim() ? packTypes : packTypes.slice(0, 2);
  const packReady = requiredPackTypes.every((type) => latestDocuments[type]);
  const statusCounts = useMemo(() => Object.fromEntries(applicationStatuses.map((status) => [status, applications.filter((application) => application.status === status).length])), [applications]);
  const filteredApplications = statusFilter === "all" ? applications : applications.filter((application) => application.status === statusFilter);

  if (!authReady) return <main><section className="panel"><p>Preparing secure sign-in…</p></section></main>;

  if (supabase && !session) return <main>
    <header>
      <p className="eyebrow">JOB APPLICATION ASSISTANT · PRIVATE BETA</p>
      <h1>Sign in to your application workspace.</h1>
      <p>Access is limited to invited beta testers.</p>
    </header>
    <section className="panel">
      <form onSubmit={signIn} className="formBody compactForm">
        <label className="full">Email<input name="email" type="email" autoComplete="email" required /></label>
        <label className="full">Password<input name="password" type="password" autoComplete="current-password" required /></label>
        <button type="submit">Sign in</button>
        {authNotice && <p className="notice">{authNotice}</p>}
      </form>
    </section>
  </main>;

  return <main>
    <header>
      <p className="eyebrow">JOB APPLICATION ASSISTANT</p>
      <h1>From job description to a tailored CV and cover letter.</h1>
      <p>Keep one truthful Master CV, add a job, and prepare the two documents most applications need. Selection Criteria is added only when the employer asks for it.</p>
      {supabase && <button type="button" className="secondary" onClick={signOut}>Sign out</button>}
    </header>
    <p className="notice">{notice}</p>

    <section className="workspaceOverview" aria-label="Application overview">
      <div className="overviewStats"><div><strong>{applications.length}</strong><small>All applications</small></div><div><strong>{statusCounts.ready_to_apply || 0}</strong><small>Ready</small></div><div><strong>{statusCounts.applied || 0}</strong><small>Applied</small></div></div>
      <div className="overviewRecent"><strong>Recent applications</strong>{applications.length ? applications.slice(0, 3).map((application) => <button type="button" key={application.id} onClick={() => { openApplication(application.id); document.getElementById("application-workspace")?.scrollIntoView({ behavior: "smooth" }); }}><span>{application.position_title}</span><small>{application.company} · {statusLabels[application.status] || application.status}</small></button>) : <small>No saved jobs yet.</small>}</div>
      <div className="overviewActions"><button type="button" onClick={() => document.getElementById("add-job")?.scrollIntoView({ behavior: "smooth" })}>Add a job</button><button type="button" className="secondary" onClick={() => document.getElementById("application-tracker")?.scrollIntoView({ behavior: "smooth" })}>Open Tracker</button></div>
    </section>

    {selectionAccess && !selectionAccess.unlimited && <section className="selectionAccessCard">
      <div>
        <strong>Selection Criteria access</strong>
        <p><b>{selectionAccess.remaining_credits}</b> free use{selectionAccess.remaining_credits === 1 ? "" : "s"} remaining. New users receive 2; each successful referral adds 1.</p>
      </div>
      <div className="referralTools">
        {selectionAccess.referral_code && <button type="button" className="secondary" onClick={() => navigator.clipboard.writeText(selectionAccess.referral_code || "")}>Copy my referral code</button>}
        {!selectionAccess.referral_claimed && <form onSubmit={claimReferral}>
          <input aria-label="Referral code" value={referralCode} onChange={(event) => setReferralCode(event.target.value)} placeholder="Enter an inviter's code" required />
          <button type="submit">Apply code</button>
        </form>}
      </div>
    </section>}

    <section className="steps">
      <details className="panel" open={!profile}>
        <summary><span>0</span><div><strong>Contact check</strong><small>{profile ? "Detected and saved — check once" : "Upload your CV and skip manual entry"}</small></div></summary>
        {!resumes.length && <form onSubmit={uploadResume} className="formBody quickStartUpload">
          <div><strong>Start by uploading your existing CV</strong><p className="helper">We will read your name, phone and email from DOCX, PDF or TXT.</p></div>
          <input type="hidden" name="title" value="Master Resume" />
          <input name="file" type="file" accept=".docx,.pdf,.txt" required />
          <button disabled={resumeUploadState === "uploading"}>{resumeUploadState === "uploading" ? "Reading…" : "Upload & auto-fill"}</button>
        </form>}
        <div className="formBody">
          {profile && <div className="contactConfirm"><div><small>Name</small><strong>{[profile.first_name, profile.last_name].filter(Boolean).join(" ")}</strong></div><div><small>Phone</small><strong>{profile.phone}</strong></div><div><small>Email</small><strong>{profile.email}</strong></div></div>}
          <details className="quickProfileEdit" open={!profile}>
            <summary>{profile ? "Something is wrong? Edit it" : "Check missing details"}</summary>
            <form key={`${profile?.updated_at || "new"}-${contactGuess.email}`} onSubmit={saveProfile} className="compactForm">
              <label>Title <em>optional</em><select name="title" defaultValue={profile?.title || ""}><option value="">No title</option><option value="Mr">Mr</option><option value="Ms">Ms</option><option value="Mrs">Mrs</option><option value="Miss">Miss</option><option value="Dr">Dr</option></select></label>
              <label>Preferred name <em>optional</em><input name="preferred_name" defaultValue={profile?.preferred_name || ""} /></label>
              <label className="full">Full name<input name="full_name" defaultValue={profile ? [profile.first_name, profile.last_name].filter(Boolean).join(" ") : contactGuess.full_name} required /></label>
              <label>Phone<input name="phone" defaultValue={profile?.phone || contactGuess.phone} required /></label>
              <label>Email<input name="email" type="email" defaultValue={profile?.email || contactGuess.email} required /></label>
              <label>Work rights <em>optional</em><select name="work_rights" defaultValue={profile?.work_rights || "not_specified"}><option value="not_specified">Do not state in documents</option><option value="citizen">Australian citizen</option><option value="permanent_resident">Permanent resident</option><option value="visa">Visa holder</option></select></label>
              <label>Availability <em>optional</em><select name="availability_notice" defaultValue={profile?.availability_notice || "not_specified"}><option value="not_specified">Do not state in documents</option><option value="two_weeks">Available after two weeks</option><option value="one_month">Available after one month</option><option value="negotiable">Start date negotiable</option></select></label>
              <label className="full">Target direction <em>optional</em><input name="target_direction" defaultValue={profile?.target_direction || ""} placeholder="e.g. Government project and policy roles" /></label>
              <label className="full">Why this direction? <em>optional</em><textarea name="motivation" defaultValue={profile?.motivation || ""} placeholder="Your own motivation only - this is kept separate from CV evidence." /></label>
              <label>Writing tone <em>optional</em><select name="writing_tone" defaultValue={profile?.writing_tone || "natural_professional"}><option value="natural_professional">Natural and professional</option><option value="concise_direct">Concise and direct</option><option value="warm_formal">Warm and formal</option></select></label>
              <label className="full">Other preferences <em>optional</em><textarea name="preferences_notes" defaultValue={profile?.preferences_notes || ""} placeholder="Document preferences or constraints; do not add employment claims here." /></label>
              <details className="optionalProfile full">
                <summary>Optional referees (maximum two)</summary>
                {[0, 1].map((index) => {
                  const referee = profile?.referees?.[index];
                  return <fieldset className="referee" key={index}><legend>Referee {index + 1}</legend>
                    <label>Name<input name={`referee_${index}_name`} defaultValue={referee?.name || ""} /></label>
                    <label>Organisation<input name={`referee_${index}_organisation`} defaultValue={referee?.organisation || ""} /></label>
                    <label>Position title<input name={`referee_${index}_position_title`} defaultValue={referee?.position_title || ""} /></label>
                    <label>Relationship<input name={`referee_${index}_relationship`} defaultValue={referee?.relationship || ""} /></label>
                    <label>Phone<input name={`referee_${index}_phone`} defaultValue={referee?.phone || ""} /></label>
                    <label>Email<input name={`referee_${index}_email`} type="email" defaultValue={referee?.email || ""} /></label>
                  </fieldset>;
                })}
                <p className="helper">Only complete this if an employer asks for referees. These details are saved to your profile.</p>
              </details>
              <button className="full">Confirm details</button>
            </form>
          </details>
          <p className="helper">Title, preferred name and referees are optional. Add them only when useful for an application.</p>
        </div>
      </details>

      <details className="panel">
        <summary><span>↻</span><div><strong>Backup &amp; Restore</strong><small>Protect your local profile, resumes, jobs and generated documents</small></div></summary>
        <div className="formBody backupPanel">
          <div className="backupActions"><button type="button" onClick={createLocalBackup}>Create Backup</button><p className="helper">Backups stay on this computer and never include API keys, passwords or verification codes.</p></div>
          <div className="backupList">{backups.length ? backups.map((backup) => <div className="backupRow" key={backup.filename}><div><strong>{new Date(backup.created_at).toLocaleString()}</strong><small>{Math.max(1, Math.round(backup.size / 1024))} KB · {backup.filename}</small></div><div><button type="button" className="secondary" onClick={() => window.open(`${api}/backups/${encodeURIComponent(backup.filename)}/download`, "_blank", "noopener,noreferrer")}>Download</button><button type="button" className="secondary" onClick={() => restoreLocalBackup(backup)}>Restore</button></div></div>) : <p className="helper">No backups yet.</p>}</div>
        </div>
      </details>

      <details className="panel" open={!resumes.length}>
        <summary><span>1</span><div><strong>Master Resume</strong><small>{resumes.length ? "Saved — edit only when needed" : "Add your real experience once"}</small></div></summary>
        {resumes.length > 0 && <form onSubmit={uploadResume} className="formBody uploadBox">
          <div><strong>Upload your existing resume</strong><p className="helper">DOCX, PDF or TXT, up to 10 MB. Uploading replaces the current Master Resume.</p></div>
          <input type="hidden" name="title" value="Master Resume" />
          <input name="file" type="file" accept=".docx,.pdf,.txt" required />
          <button disabled={resumeUploadState === "uploading"}>{resumeUploadState === "uploading" ? "Reading file…" : resumeUploadState === "saved" ? "Uploaded ✓" : "Upload Resume"}</button>
        </form>}
        <div className="orDivider"><span>or paste and edit the text</span></div>
        <form key={resumes[0]?.id || "new"} onSubmit={saveResume} className="formBody resumeTextForm">
          <label>Resume title<input name="title" defaultValue={resumes[0]?.title || "Master Resume"} required /></label>
          <label>Resume text<textarea name="source_text" defaultValue={resumes[0]?.source_text || ""} rows={14} required /></label>
          <div className="experienceBuilder">
            <div className="experienceHeader"><div><strong>Structured work experiences</strong><p className="helper">These facts help Selection Criteria use STAR naturally and prevent invented results.</p></div><button type="button" className="secondary" onClick={addExperience}>Add experience</button></div>
            {experiences.map((experience, index) => <fieldset className="experienceCard" key={experience.id}><legend>Experience {index + 1}</legend>
              <div className="compactForm">
                <label>Role title<input value={experience.role_title} onChange={(event) => updateExperience(experience.id, "role_title", event.target.value)} required /></label>
                <label>Organisation<input value={experience.organization} onChange={(event) => updateExperience(experience.id, "organization", event.target.value)} required /></label>
                <label className="full">What did you do? <em>Action</em><textarea rows={3} value={experience.responsibility} onChange={(event) => updateExperience(experience.id, "responsibility", event.target.value)} required /></label>
                <label className="full">Background or problem <em>Situation · optional</em><textarea rows={2} value={experience.context} onChange={(event) => updateExperience(experience.id, "context", event.target.value)} /></label>
                <label className="full">Result or outcome <em>exact figure or rough range</em><textarea rows={2} value={experience.result} disabled={experience.no_result_data} onBlur={() => promptForResult(experience)} onChange={(event) => updateExperience(experience.id, "result", event.target.value)} placeholder="e.g. processed about 20–30 cases per week, shortened turnaround time, or improved accuracy" /></label>
                <label className="resultMissing full"><input type="checkbox" checked={experience.no_result_data} onChange={(event) => updateExperience(experience.id, "no_result_data", event.target.checked)} /> No result data available</label>
                <p className="metricPrompt full">Does this experience have specific numbers—volume, time saved, accuracy, financial value or satisfaction score? An approximate range is useful too, such as “about 20–30 per week”.</p>
                <button type="button" className="dangerLink" onClick={() => setExperiences((current) => current.filter((item) => item.id !== experience.id))}>Remove experience</button>
              </div>
            </fieldset>)}
            {!experiences.length && <p className="helper">Add each relevant role as a separate experience. You can still keep the full resume text above.</p>}
          </div>
          {resumes[0] && <div className="resumeContentCheck">
            <div className="contentCheckHeading"><div><strong>CV Content Check</strong><p className="helper">Compare personal details and extracted experience with the original uploaded CV.</p></div><button type="button" className="secondary" onClick={() => runResumeContentCheck()} disabled={resumeCheckState === "checking"}>{resumeCheckState === "checking" ? "Checking…" : "Check extracted details"}</button></div>
            {resumeContentCheck && <div className={resumeContentCheck.ready ? "contentCheckSummary pass" : "contentCheckSummary review"}>
              <strong>{resumeContentCheck.ready ? "All extracted details matched" : "Review the highlighted details"}</strong>
              <p>{resumeContentCheck.matched_count} matched · {resumeContentCheck.review_count} need confirmation · {resumeContentCheck.missing_count} missing</p>
              <div className="contentCheckItems">{resumeContentCheck.items.map((item) => <div className={`contentCheckItem ${item.status}`} key={item.field}><span>{item.status === "matched" ? "Matched" : item.status === "review" ? "Review" : "Missing"}</span><div><strong>{item.label}</strong>{item.value && <small>{item.value}</small>}<small>{item.message}</small></div></div>)}</div>
            </div>}
          </div>}
          <button>Save Master Resume</button>
        </form>
      </details>

      <section className="panel" id="add-job">
        <div className="stepHeading"><span>2</span><div><strong>Add a job</strong><small>Paste the JD and application link</small></div></div>
        <form onSubmit={createApplication} className="formBody compactForm">
          <div className="full fullAdBox">
            <label>Paste full job advertisement<textarea rows={9} value={rawJobAd} onChange={(event) => { setRawJobAd(event.target.value); setAdParseState("idle"); }} placeholder="Copy the complete advertisement from SEEK or another job site and paste it here once." /></label>
            <button type="button" onClick={parseFullJobAd} disabled={adParseState === "parsing"}>{adParseState === "parsing" ? "Separating details…" : adParseState === "done" ? "Details extracted ✓" : "Extract Job Details"}</button>
            <p className="helper">This fills the organisation, position title, job description and selection criteria below. Review them before saving.</p>
            {adWarnings.length > 0 && <div className="adWarnings"><strong>Check before saving</strong><ul>{adWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}
          </div>
          <label>Organisation<input name="company" value={jobFields.company} onChange={(event) => setJobFields({ ...jobFields, company: event.target.value })} required /></label>
          <label>Position title<input name="position_title" value={jobFields.position_title} onChange={(event) => setJobFields({ ...jobFields, position_title: event.target.value })} required /></label>
          <label className="full">Application link<div className="linkImportRow"><input name="job_url" type="url" placeholder="https://example.com/job" value={jobFields.job_url} onChange={(event) => setJobFields({ ...jobFields, job_url: event.target.value })} /><button type="button" onClick={importJobLink} disabled={jobImportState === "importing"}>{jobImportState === "importing" ? "Reading…" : jobImportState === "done" ? "Imported ✓" : "Import Details"}</button></div></label>
          <label className="full">Job description<textarea name="job_description" rows={8} value={jobFields.job_description} onChange={(event) => setJobFields({ ...jobFields, job_description: event.target.value })} required /></label>
          <label className="full">Selection criteria or short guidance <em>optional</em><textarea name="selection_criteria" rows={4} value={jobFields.selection_criteria} onChange={(event) => setJobFields({ ...jobFields, selection_criteria: event.target.value })} placeholder="Paste the full criteria, or add a short instruction such as: Focus on stakeholder engagement and government reporting." /><small>Short guidance will be expanded using explicit JD requirements and your saved CV evidence.</small></label>
          <button className="full">Save Job</button>
        </form>
      </section>

      <section className="panel" id="application-tracker">
        <div className="stepHeading"><span>3</span><div><strong>Application Tracker</strong><small>Review and update every application in one place</small></div></div>
        <div className="statusFilters">
          <button type="button" className={statusFilter === "all" ? "statusCard activeStatus" : "statusCard"} onClick={() => setStatusFilter("all")}><strong>{applications.length}</strong><small>All jobs</small></button>
          {applicationStatuses.map((status) => <button type="button" key={status} className={statusFilter === status ? "statusCard activeStatus" : "statusCard"} onClick={() => setStatusFilter(status)}><strong>{statusCounts[status] || 0}</strong><small>{statusLabels[status]}</small></button>)}
        </div>
        <div className="trackerList">
          {filteredApplications.length ? filteredApplications.map((application) => <div className="trackerRow" key={application.id}>
            <button type="button" className="trackerJob" onClick={() => openApplication(application.id)}><strong>{application.position_title}</strong><small>{application.company}{application.submitted_at ? ` · Applied ${new Date(application.submitted_at).toLocaleDateString()}` : ""}{application.submission_reference ? ` · ${application.submission_reference}` : ""}</small></button>
            <select aria-label={`Status for ${application.position_title}`} value={application.status} onChange={(event) => updateApplicationStatus(application, event.target.value)}>{applicationStatuses.map((status) => <option value={status} key={status}>{statusLabels[status]}</option>)}</select>
          </div>) : <p className="helper">No applications in this status.</p>}
        </div>
      </section>

      <section className="panel" id="application-workspace">
        <div className="stepHeading"><span>4</span><div><strong>Generate, review and apply</strong><small>Creates your tailored CV and Cover Letter; Selection Criteria is optional</small></div></div>
        <div className="applicationLayout">
          <aside className="jobList">
            {applications.length ? applications.map((application) => <button type="button" className={application.id === selectedApplication ? "job active" : "job"} key={application.id} onClick={() => openApplication(application.id)}>
              <strong>{application.position_title}</strong><small>{application.company} · {statusLabels[application.status] || application.status}</small>
            </button>) : <p className="helper">Save a job to get started.</p>}
          </aside>
          <div className="reviewArea">
            {selected ? <>
              <div className="selectedJob"><div><strong>{selected.position_title}</strong><small>{selected.company}{selected.submitted_at ? ` · Applied ${new Date(selected.submitted_at).toLocaleDateString()}` : ""}{selected.submission_reference ? ` · Confirmation ${selected.submission_reference}` : ""}</small></div><div className="selectedActions"><button className="secondary" type="button" onClick={copyApplicationLink}>Copy Application Link</button><button disabled={busy || !resumes.length || confirmedApplication !== selected.id} onClick={generatePack}>{busy ? "Creating pack…" : "Generate Application Pack"}</button></div></div>
              {packNotice && <p className="notice applicationNotice" role="status" aria-live="polite">{packNotice}</p>}
              <div className={confirmedApplication === selected.id ? "confirmCard confirmed" : "confirmCard"}>
                <div><strong>Confirm before generating</strong><p>Position: {selected.position_title}<br />Organisation: {selected.company}<br />Phone: {profile?.phone || "No saved profile"}<br />Email: {profile?.email || "No saved profile"}</p></div>
                <button type="button" disabled={!profile || !selected.company.trim() || !selected.position_title.trim() || confirmedApplication === selected.id} onClick={() => setConfirmedApplication(selected.id)}>{confirmedApplication === selected.id ? "Details confirmed ✓" : "Confirm these details"}</button>
              </div>
              <details className="jobEditPanel" key={`edit-${selected.id}`}>
                <summary>Edit saved job details</summary>
                <form onSubmit={updateSavedJob} className="compactForm">
                  <label>Organisation<input name="company" defaultValue={selected.company} required /></label>
                  <label>Position title<input name="position_title" defaultValue={selected.position_title} required /></label>
                  <label className="full">Application link<input name="job_url" type="url" defaultValue={selected.job_url || ""} placeholder="https://example.com/job" /></label>
                  <label className="full">Job description<textarea name="job_description" defaultValue={selected.job_description || ""} rows={10} required /></label>
                  <label className="full">Selection criteria or short guidance <em>optional</em><textarea name="selection_criteria" defaultValue={selected.selection_criteria || ""} rows={5} placeholder="Full criteria or a short instruction" /><small>Short guidance will be expanded using explicit JD requirements and your saved CV evidence.</small></label>
                  <label className="full">Employer confirmation number <em>optional — usually only provided by government or large recruitment systems</em><input name="submission_reference" defaultValue={selected.submission_reference || ""} /></label>
                  <button className="full">Save job changes</button>
                </form>
              </details>
              {documents.length ? <>
                {activeReviewer && <div className={activeReviewer.status === "pass" ? "reviewerResult pass" : "reviewerResult fail"}><strong>{activeReviewer.status === "pass" ? "Batch Reviewer passed" : "Batch Reviewer found issues"}</strong>{activeReviewer.status === "fail" && <ul>{activeReviewer.results.flatMap((result) => result.issues.map((issue) => <li key={`${result.criteria_id}-${issue.type}`}>{result.criteria_id}{issue.severity ? ` [${issue.severity.toUpperCase()}]` : ""}: {issue.description}</li>))}</ul>}</div>}
                {activeType === "selection_criteria" && selectionPlan.length > 0 && <div className="criteriaReviewPanel"><div><strong>Selection Criteria evidence check</strong><p className="helper">Strong is ready for normal review. Transferable and Weak responses require your explicit confirmation.</p></div>{selectionPlan.map((item) => { const review = reviewerByCriteria[item.criteria_id]; const needsConfirmation = item.evidence_status !== "strong"; return <article className={`criterionReviewCard ${item.evidence_status}`} key={item.criteria_id}><div className="criterionReviewHeading"><span className="criterionStatus">{item.evidence_status === "strong" ? "Strong" : item.evidence_status === "transferable" ? "Transferable" : "Weak"}</span><span className={review?.status === "pass" ? "reviewStatus pass" : "reviewStatus fail"}>{review?.status === "pass" ? "Reviewer passed" : "Needs review"}</span></div><strong>{item.criteria_text}</strong><small>Target: {item.allocated_word_limit} words · {item.match_type} match · {item.coverage} coverage</small><div className="criterionSources"><small>Evidence sources</small>{item.matched_evidence.length ? <ul>{item.matched_evidence.map((evidenceId) => <li key={evidenceId}><code>{evidenceId}</code> {ckbById[evidenceId]?.source_section || "Uploaded Master CV"}</li>)}</ul> : <p>No direct evidence matched. Check this response carefully.</p>}</div>{review?.issues?.length > 0 && <ul className="criterionIssues">{review.issues.map((issue: { type: string; description: string }) => <li key={issue.type}>{issue.description}</li>)}</ul>}{needsConfirmation && <label className="criterionConfirmation"><input type="checkbox" checked={confirmedSelectionCriteria.includes(item.criteria_id)} onChange={(event) => setCriterionConfirmation(item.criteria_id, event.target.checked)} /> I reviewed this {item.evidence_status} response and confirm it is truthful.</label>}</article>; })}</div>}
                <nav className="tabs">{requiredPackTypes.map((type) => <button key={type} className={activeType === type ? "activeTab" : "tab"} onClick={() => setActiveType(type)} disabled={!latestDocuments[type]}>{labels[type]}{latestDocuments[type] ? " ✓" : ""}</button>)}</nav>
                <div className="templatePicker"><div><strong>Export style</strong><small>All options are single-column and ATS-friendly.</small></div><select aria-label="Export style" value={exportTemplate} onChange={(event) => setExportTemplate(event.target.value as "classic" | "modern" | "traditional")}><option value="classic">Classic — Calibri</option><option value="modern">Modern — Arial</option><option value="traditional">Traditional — Georgia</option></select></div>
                {activeDocument && <div className="editor"><p className="helper"><strong>Latest generated:</strong> {new Date(activeDocument.created_at).toLocaleString()} · Document #{activeDocument.id}</p>{activeEvidence.length > 0 && <div className="evidenceTrace"><strong>Resume evidence used</strong><ul>{activeEvidence.map((item) => <li key={item.id}><code>{item.id}</code> {item.label}</li>)}</ul></div>}<textarea aria-label={labels[activeType]} value={draftText} onChange={(event) => { setDraftText(event.target.value); setDraftSaveState("dirty"); }} rows={24} /><div className="saveStatus" data-state={draftSaveState}>{draftSaveState === "dirty" ? "Unsaved changes" : draftSaveState === "saving" ? "Saving…" : draftSaveState === "error" ? "Save failed — try again" : "All changes saved ✓"}</div><div className="editorActions"><button className="secondary" onClick={() => navigator.clipboard.writeText(draftText)}>Copy</button><button className={`saveEdits ${draftSaveState}`} onClick={saveDraft} disabled={draftSaveState === "saving" || draftSaveState === "saved"}>{draftSaveState === "saving" ? "Saving…" : draftSaveState === "saved" ? "Saved ✓" : "Save edits"}</button><button className="secondary" onClick={() => downloadDocument("docx")}>This DOCX</button><button className="secondary" onClick={() => downloadDocument("pdf")}>This PDF</button><button className="secondary" onClick={downloadTrace}>Audit trace</button>{packReady && <><button className="secondary" onClick={() => downloadPack("docx")}>All DOCX</button><button className="secondary" onClick={() => downloadPack("pdf")}>All PDF</button><button className="secondary" onClick={runFinalCheck} disabled={finalCheckState === "checking"}>{finalCheckState === "checking" ? "Checking…" : "Run Final Check"}</button><button onClick={reviewAndApply}>Review &amp; Apply</button><button className="secondary" onClick={markApplied} disabled={selected.status === "applied"}>{selected.status === "applied" ? "Applied ✓" : "Mark as Applied"}</button></>}</div>{qualityResult && <div className={qualityResult.ready ? "qualityResult pass" : "qualityResult fail"}><strong>{qualityResult.ready ? "Final check passed" : "Fix these items before applying"}</strong>{qualityResult.issues.length ? <ul>{qualityResult.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><b>{issue.severity === "error" ? "Error" : "Warning"}:</b> {issue.message}{issue.document_type ? ` (${labels[issue.document_type] || issue.document_type})` : ""}</li>)}</ul> : <p>No issues found.</p>}</div>}</div>}
              </> : <div className="emptyState"><strong>Your CV and cover letter will appear here.</strong><p>If the saved job includes Selection Criteria, those responses will be added as an optional third document.</p></div>}
            </> : <div className="emptyState"><strong>Select a saved job.</strong><p>Then generate the complete application pack in one click.</p></div>}
          </div>
        </div>
      </section>
    </section>
    <p className="safety">AI prepares drafts from your Master Resume only. You review the facts and make the final submission. Login and CAPTCHA are never bypassed.</p>
  </main>;
}

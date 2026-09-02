"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { createClient, Session } from "@supabase/supabase-js";
import {
  ApplicationRequirements, ApplicationRequirementsCorrectionDraft, ApplicationRequirementsResponse,
  DocumentFormat, LimitConstraint, LimitScope, LimitUnit, RequirementValue, SubmissionLimit,
  createCorrectionDraft, documentChoiceLabel, formatDocumentFormat, formatRequirementLabel, formatSubmissionLimit,
  getRequirementsStatusLabel, requiredGeneratedDocumentTypes, requirementsHasUnknown, requirementsNeedConfirmation, unresolvedRequirementLabels,
} from "./applicationRequirements";
import { ApplicationDecision, decisionLabel } from "./applicationDecision";
import { AtsResult, PackReviewResult, ReleaseChecklist, canGenerate, releaseCanProceed } from "./releaseWorkflow";
import { ActivationState, activationIntent, activationTransition } from "./authActivation";
import { parsedSelectionCriteria, preservedOrganisation, releaseFailureState, resumeEditorVersion, shouldExpireSession, uploadFailureState, withBusyReset } from "./betaOperations";
import { activeApplications, archivedApplications } from "./applicationArchive";

const api = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || "";
const supabase = supabaseUrl && supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;
const betaSupportContact = process.env.NEXT_PUBLIC_BETA_SUPPORT_CONTACT || "the beta operator who invited you";
type Experience = { id: string; role_title: string; organization: string; time_period_text?: string; responsibility: string; context: string; result: string; no_result_data: boolean };
type CkbEvidence = { evidence_id: string; evidence_type: string; source_section: string; source_text: string };
type Resume = { id: number; title: string; source_text: string; experiences_json?: string; ckb_json?: string; updated_at: string };
type SelectionPlanItem = { criteria_id: string; criteria_text: string; allocated_word_limit: number; matched_evidence: string[]; match_type: string; coverage: string; evidence_status: "strong" | "transferable" | "weak" };
type Application = { id: number; company: string; position_title: string; job_url?: string; job_description: string; selection_criteria?: string; application_requirements_json?: string; selection_plan_json?: string; selection_confirmations_json?: string; status: string; submission_reference?: string; submitted_at?: string; archived_at?: string | null; updated_at?: string };
type GeneratedDocument = { id: number; document_type: string; content: string; used_experiences_json?: string; reviewer_json?: string; run_id?: string; trace_json?: string; created_at: string };
type ReviewerResult = { status: "pass" | "fail" | "pending" | "provider_failed"; state?: string; message?: string; results?: { criteria_id: string; status: "pass" | "fail"; issues: { type: string; severity?: "critical" | "major" | "advisory"; description: string; recommended_action?: string }[]; recommendation?: string }[] };
type QualityIssue = { severity: "error" | "warning"; code: string; message: string; document_type?: string };
type QualityResult = { ready: boolean; issues: QualityIssue[]; checked_documents: string[] };
type ResumeContentCheckItem = { field: string; label: string; value: string; status: "matched" | "review" | "missing"; message: string };
type ResumeContentCheckResult = { ready: boolean; matched_count: number; review_count: number; missing_count: number; items: ResumeContentCheckItem[] };
type Backup = { filename: string; size: number; created_at: string };
type Referee = { organisation: string; name: string; position_title: string; phone: string; relationship: string; email: string; postal_address?: string; suburb?: string; state: string; postcode?: string; country: string };
type Profile = { id: number; title?: string; first_name: string; last_name: string; preferred_name?: string; phone: string; email: string; postal_address?: string; suburb?: string; state: string; postcode?: string; country: string; work_rights: string; availability_notice: string; target_direction?: string; motivation?: string; writing_tone: string; preferences_notes?: string; referees: Referee[]; updated_at: string };
type JobFields = { company: string; position_title: string; job_url: string; job_description: string; selection_criteria: string; discovered_sources: Record<string, unknown>[] };
type ContactGuess = { full_name: string; phone: string; email: string };
type SelectionCriteriaAccess = { unlimited: boolean; included_credits: number; referral_credits: number; used_credits: number; remaining_credits: number | null; referral_code: string | null; referral_claimed: boolean };
type JobSource = { source_id: string; source_type: string; title: string; label: string; source_url?: string; acquisition_status: string; extraction_status: string; warnings_json: string };

const packTypes = ["tailored_resume", "cover_letter", "selection_criteria"] as const;
const labels: Record<string, string> = {
  tailored_resume: "Tailored CV",
  cover_letter: "Cover Letter",
  selection_criteria: "Selection Criteria",
};
const applicationStatuses = ["draft", "ready_to_apply", "applied"] as const;
const statusLabels: Record<string, string> = { draft: "Draft", ready_to_apply: "Ready", applied: "Applied" };
const requirementOptions: RequirementValue[] = ["required", "optional", "not_required", "unknown"];
const coverFormatOptions: DocumentFormat[] = ["standalone", "portal_fields", "not_applicable", "unknown"];
const selectionFormatOptions: DocumentFormat[] = ["standalone", "embedded_in_cover_letter", "embedded_in_resume", "portal_fields", "not_applicable", "unknown"];
const limitUnits: LimitUnit[] = ["words", "characters", "pages"];
const limitScopes: LimitScope[] = ["document", "per_criterion", "combined_documents"];
const limitConstraints: LimitConstraint[] = ["maximum", "minimum", "exact", "recommended"];
const sourceTypeLabels: Record<string, string> = { primary_advertisement: "Job advertisement", job_description_attachment: "JDF / Position Description", application_instruction_attachment: "Application Information Pack", mandatory_form: "Mandatory form", other_supporting_attachment: "Supporting attachment", unknown_attachment: "Other attachment" };

function sourceStateLabel(source: JobSource) {
  if (source.acquisition_status === "uploaded") return source.extraction_status === "partial" ? "Manually uploaded · partial extraction" : "Manually uploaded";
  if (source.acquisition_status === "requires_auth") return "Requires authentication";
  if (source.acquisition_status === "unavailable") return "Unavailable";
  if (source.acquisition_status === "failed" || source.extraction_status === "failed") return "Failed";
  if (source.extraction_status === "partial") return "Downloaded · partial extraction";
  if (source.acquisition_status === "fetched" && source.extraction_status === "extracted") return "Available and extracted";
  if (source.acquisition_status === "discovered") return source.source_url ? "Discovered · not acquired" : "Referenced but not acquired";
  return `${source.acquisition_status.replaceAll("_", " ")} · ${source.extraction_status.replaceAll("_", " ")}`;
}

function sourceWarnings(source: JobSource) {
  try { return JSON.parse(source.warnings_json || "[]") as string[]; } catch { return []; }
}

function RequirementLimitEditor({ limit, onToggle, onChange }: {
  limit: SubmissionLimit | null;
  onToggle: (enabled: boolean) => void;
  onChange: (changes: Partial<SubmissionLimit>) => void;
}) {
  return <div className="requirementLimitEditor">
    <label className="requirementCheckbox"><input type="checkbox" checked={Boolean(limit)} onChange={(event) => onToggle(event.target.checked)} /> Include a submission limit</label>
    {limit && <div className="requirementLimitFields">
      <label>Value<input type="number" min="1" value={limit.value} onChange={(event) => onChange({ value: Number(event.target.value) })} /></label>
      <label>Unit<select value={limit.unit} onChange={(event) => onChange({ unit: event.target.value as LimitUnit })}>{limitUnits.map((unit) => <option key={unit} value={unit}>{unit}</option>)}</select></label>
      <label>Scope<select value={limit.scope} onChange={(event) => onChange({ scope: event.target.value as LimitScope })}>{limitScopes.map((scope) => <option key={scope} value={scope}>{scope.replaceAll("_", " ")}</option>)}</select></label>
      <label>Constraint<select value={limit.constraint} onChange={(event) => onChange({ constraint: event.target.value as LimitConstraint })}>{limitConstraints.map((constraint) => <option key={constraint} value={constraint}>{constraint}</option>)}</select></label>
    </div>}
  </div>;
}

export function Workspace({ applicationsPage = false }: { applicationsPage?: boolean }) {
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(!supabase);
  const [authNotice, setAuthNotice] = useState("");
  const [activation, setActivation] = useState<ActivationState>({ mode: "idle" });
  const [showPrivacy, setShowPrivacy] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [notice, setNotice] = useState("Connecting to your local workspace…");
  const [packNotice, setPackNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedApplication, setSelectedApplication] = useState<number | null>(null);
  const [documents, setDocuments] = useState<GeneratedDocument[]>([]);
  const [generationFailure, setGenerationFailure] = useState<{ documentType: typeof packTypes[number]; message: string } | null>(null);
  const [activeType, setActiveType] = useState<string>("tailored_resume");
  const [draftText, setDraftText] = useState("");
  const [qualityResult, setQualityResult] = useState<QualityResult | null>(null);
  const [finalCheckState, setFinalCheckState] = useState<"idle" | "checking">("idle");
  const [documentReviewState, setDocumentReviewState] = useState<"idle" | "reviewing">("idle");
  const [resumeContentCheck, setResumeContentCheck] = useState<ResumeContentCheckResult | null>(null);
  const [resumeCheckState, setResumeCheckState] = useState<"idle" | "checking" | "done" | "error">("idle");
  const [statusFilter, setStatusFilter] = useState("all");
  const [backups, setBackups] = useState<Backup[]>([]);
  const [resumeUploadState, setResumeUploadState] = useState("idle");
  const [jobImportState, setJobImportState] = useState("idle");
  const [jobFields, setJobFields] = useState<JobFields>({ company: "", position_title: "", job_url: "", job_description: "", selection_criteria: "", discovered_sources: [] });
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
  const [submissionFormat, setSubmissionFormat] = useState<"docx" | "pdf">("docx");
  const [releaseChecklist, setReleaseChecklist] = useState<ReleaseChecklist | null>(null);
  const [packReviewResult, setPackReviewResult] = useState<PackReviewResult | null>(null);
  const [atsResult, setAtsResult] = useState<AtsResult | null>(null);
  const [releaseBusy, setReleaseBusy] = useState<"idle" | "pack" | "ats">("idle");
  const [applicationRequirements, setApplicationRequirements] = useState<ApplicationRequirements | null>(null);
  const [requirementsLoadState, setRequirementsLoadState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [requirementsSaveState, setRequirementsSaveState] = useState<"idle" | "saving" | "error">("idle");
  const [requirementsSavedMessage, setRequirementsSavedMessage] = useState("");
  const [requirementsEditDraft, setRequirementsEditDraft] = useState<ApplicationRequirementsCorrectionDraft | null>(null);
  const [requirementsError, setRequirementsError] = useState("");
  const [applicationDecision, setApplicationDecision] = useState<ApplicationDecision | null>(null);
  const [applicationDecisionCurrent, setApplicationDecisionCurrent] = useState(false);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [isEditingRequirements, setIsEditingRequirements] = useState(false);
  const requirementsRequestId = useRef(0);
  const [sources, setSources] = useState<JobSource[]>([]);
  const [sourcesLoadState, setSourcesLoadState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [sourceUploadId, setSourceUploadId] = useState<string | null>(null);
  const [sourcesError, setSourcesError] = useState("");
  const sourcesRequestId = useRef(0);

  async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
    const response = await window.fetch(input, { ...init, headers });
    if (shouldExpireSession(response.status) && supabase) {
      await supabase.auth.signOut({ scope: "local" });
      clearAuthenticatedState();
      setAuthNotice("Your session expired. Sign in again to reload your saved workspace.");
    }
    return response;
  }

  function clearAuthenticatedState() {
    setProfile(null); setResumes([]); setApplications([]); setDocuments([]);
    setApplicationRequirements(null); setApplicationDecision(null); setReleaseChecklist(null);
    setRequirementsEditDraft(null); setSources([]); setSourcesLoadState("idle");
    setSourcesError(""); setSourceUploadId(null); sourcesRequestId.current += 1;
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
    const intent = activationIntent(window.location.href);
    if (intent.mode === "error") {
      setActivation(activationTransition({ mode: "idle" }, "failure", intent.message));
      setAuthReady(true);
    } else if (intent.mode === "password_setup") {
      setActivation(activationTransition({ mode: "idle" }, "start"));
      const establish = intent.code ? supabase.auth.exchangeCodeForSession(intent.code) : supabase.auth.getSession();
      establish.then(({ data, error }) => {
        const callbackSession = "session" in data ? data.session : null;
        setSession(callbackSession);
        setActivation(error || !callbackSession
          ? activationTransition({ mode: "checking" }, "failure", error?.message)
          : activationTransition({ mode: "checking" }, "session_ready"));
        setAuthReady(true);
      });
    } else {
      supabase.auth.getSession().then(({ data }) => { setSession(data.session); setAuthReady(true); });
    }
    const { data } = supabase.auth.onAuthStateChange((event, nextSession) => {
      setSession(nextSession);
      setAuthReady(true);
      if (event === "PASSWORD_RECOVERY" && nextSession) setActivation({ mode: "password_setup" });
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

  useEffect(() => {
    if (!applicationsPage || selectedApplication || !applications.length) return;
    const requestedId = Number(new URLSearchParams(window.location.search).get("application"));
    const application = applications.find((item) => item.id === requestedId) || applications.find((item) => !item.archived_at);
    if (application) void openApplication(application.id);
  }, [applicationsPage, applications, selectedApplication]);

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
    clearAuthenticatedState();
  }

  async function setActivationPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    const password = String(new FormData(event.currentTarget).get("password") || "");
    if (password.length < 8) return setActivation({ mode: "error", message: "Use a password of at least eight characters, then reopen the invitation link if needed." });
    setActivation((state) => activationTransition(state, "save"));
    const { error } = await supabase.auth.updateUser({ password });
    if (error) return setActivation((state) => activationTransition(state, "failure", error.message));
    window.history.replaceState({}, "", window.location.pathname);
    setActivation((state) => activationTransition(state, "success"));
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
    try { const reviewer = JSON.parse(activeDocument.reviewer_json) as ReviewerResult; return reviewer.status ? reviewer : null; } catch { return null; }
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
      setApplicationDecision(null);
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
    setExperiences((current) => [...current, { id: crypto.randomUUID(), role_title: "", organization: "", time_period_text: "", responsibility: "", context: "", result: "", no_result_data: false }]);
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
    try {
      const response = await authenticatedFetch(`${api}/resumes/upload`, { method: "POST", body: new FormData(event.currentTarget) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Validation failed: the Resume file could not be read.");
      let extractedExperienceCount = 0;
      try { extractedExperienceCount = JSON.parse(result.experiences_json || "[]").length; } catch { extractedExperienceCount = 0; }
      const guess = detectContact(result.source_text || "");
      setContactGuess(guess);
      const contactSaved = await saveDetectedContact(guess);
      await refresh(); await runResumeContentCheck(result.id);
      const experienceMessage = extractedExperienceCount ? ` We also created ${extractedExperienceCount} work experience ${extractedExperienceCount === 1 ? "record" : "records"} for you to review.` : " We kept the full CV text; add structured experience only if you want to strengthen the generated evidence.";
      setNotice((contactSaved ? "CV uploaded. We found and saved your name, phone and email — please check them once." : "CV uploaded. Check the missing contact detail below; the rest has already been filled in.") + experienceMessage);
      setResumeUploadState("saved");
    } catch (error) {
      setResumeUploadState(uploadFailureState());
      setNotice(error instanceof Error ? error.message : "Network error: Resume upload failed. Check the connection and try again.");
    }
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
        company: preservedOrganisation(current.company, result.company),
        position_title: result.position_title || current.position_title,
        job_description: result.job_description || current.job_description,
        job_url: result.job_url || current.job_url,
        discovered_sources: result.discovered_sources || [],
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
        company: preservedOrganisation(current.company, result.company),
        position_title: result.position_title || current.position_title,
        job_description: result.job_description,
        selection_criteria: parsedSelectionCriteria(result.selection_criteria),
        discovered_sources: [],
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
    const { discovered_sources: _, ...updatePayload } = payload;
    const response = await authenticatedFetch(`${api}/applications${selectedApplication ? `/${selectedApplication}` : ""}`, {
      method: selectedApplication ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selectedApplication ? updatePayload : payload),
    });
    if (!response.ok) return setNotice("Could not save this job. Check the required fields and try again.");
    const application = await response.json();
    formElement.reset();
    setJobFields({ company: "", position_title: "", job_url: "", job_description: "", selection_criteria: "", discovered_sources: [] });
    setJobImportState("idle");
    setRawJobAd("");
    setAdWarnings([]);
    setAdParseState("idle");
    await refresh();
    await openApplication(application.id);
    setNotice("Job saved. Diagnose the application, resolve any material questions, then generate the application pack.");
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
    setReleaseChecklist(null); setPackReviewResult(null); setAtsResult(null);
    await openApplication(selectedApplication);
    setNotice("Saved job details updated. Earlier documents are now historical; diagnose and regenerate from the current job.");
  }

  async function loadApplicationRequirements(applicationId: number) {
    const requestId = ++requirementsRequestId.current;
    setApplicationRequirements(null);
    setRequirementsEditDraft(null);
    setIsEditingRequirements(false);
    setRequirementsError("");
    setRequirementsSaveState("idle");
    setRequirementsLoadState("loading");
    try {
      const response = await authenticatedFetch(`${api}/applications/${applicationId}/application-requirements`);
      const result = await response.json().catch(() => null) as ApplicationRequirementsResponse | { detail?: string } | null;
      if (requestId !== requirementsRequestId.current) return;
      if (!response.ok || !result || !("requirements" in result)) {
        setRequirementsLoadState("error");
        setRequirementsError(result && "detail" in result && result.detail ? result.detail : "Could not load the application requirements.");
        return;
      }
      setApplicationRequirements(result.requirements);
      setApplicationDecision(null);
      setRequirementsLoadState("success");
    } catch {
      if (requestId !== requirementsRequestId.current) return;
      setRequirementsLoadState("error");
      setRequirementsError("Could not load the application requirements. Check the connection and try again.");
    }
  }

  async function loadSources(applicationId: number) {
    const requestId = ++sourcesRequestId.current;
    setSources([]);
    setSourcesError("");
    setSourceUploadId(null);
    setSourcesLoadState("loading");
    try {
      const response = await authenticatedFetch(`${api}/applications/${applicationId}/sources`);
      const result = await response.json().catch(() => null) as JobSource[] | { detail?: string } | null;
      if (requestId !== sourcesRequestId.current) return;
      if (!response.ok || !Array.isArray(result)) {
        setSourcesLoadState("error");
        return setSourcesError(result && !Array.isArray(result) && result.detail ? result.detail : "Could not load application sources.");
      }
      setSources(result);
      setSourcesLoadState("success");
    } catch {
      if (requestId !== sourcesRequestId.current) return;
      setSourcesLoadState("error");
      setSourcesError("Could not load application sources. Check the connection and try again.");
    }
  }

  async function uploadMissingSource(source: JobSource, file: File | undefined) {
    if (!selectedApplication || !file || sourceUploadId) return;
    const applicationId = selectedApplication;
    const requestId = sourcesRequestId.current;
    setSourceUploadId(source.source_id);
    setSourcesError("");
    const body = new FormData();
    body.append("file", file);
    body.append("expected_source_type", source.source_type);
    body.append("target_source_id", source.source_id);
    try {
      const response = await authenticatedFetch(`${api}/applications/${applicationId}/sources/upload`, { method: "POST", body });
      const result = await response.json().catch(() => null) as JobSource[] | { detail?: string } | null;
      if (requestId !== sourcesRequestId.current) return;
      if (!response.ok || !Array.isArray(result)) return setSourcesError(result && !Array.isArray(result) && result.detail ? result.detail : "The document could not be uploaded.");
      setSources(result);
      setSourcesLoadState("success");
    } catch {
      if (requestId === sourcesRequestId.current) setSourcesError("The document could not be uploaded. Check the connection and try again.");
    } finally {
      if (requestId === sourcesRequestId.current) setSourceUploadId(null);
    }
  }

  async function confirmApplicationRequirements() {
    if (!selectedApplication || !applicationRequirements || requirementsSaveState === "saving") return;
    if (requirementsHasUnknown(applicationRequirements) && !window.confirm("Some requirements are still unknown. Edit them if the advertisement provides more detail. Confirm these requirements without changing the unknown values?")) return;
    setRequirementsSaveState("saving");
    setRequirementsError("");
    try {
      const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/application-requirements`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "confirm" }),
      });
      const result = await response.json().catch(() => null) as ApplicationRequirementsResponse | { detail?: string } | null;
      if (!response.ok || !result || !("requirements" in result)) {
        setRequirementsSaveState("error");
        return setRequirementsError(result && "detail" in result && result.detail ? result.detail : "Could not confirm the application requirements.");
      }
      setApplicationRequirements(result.requirements);
      setApplicationDecision(null);
      setRequirementsEditDraft(null);
      setIsEditingRequirements(false);
      setRequirementsSaveState("idle");
      setRequirementsSavedMessage("✓ Saved");
      setDocuments([]); setReleaseChecklist(null); setPackReviewResult(null); setAtsResult(null);
      await openApplication(selectedApplication);
    } catch {
      setRequirementsSaveState("error");
      setRequirementsError("Could not confirm the application requirements. Check the connection and try again.");
    }
  }

  function beginRequirementsEdit() {
    if (!applicationRequirements) return;
    setRequirementsEditDraft(createCorrectionDraft(applicationRequirements));
    setRequirementsError("");
    setRequirementsSaveState("idle");
    setRequirementsSavedMessage("");
    setIsEditingRequirements(true);
  }

  function cancelRequirementsEdit() {
    setRequirementsEditDraft(null);
    setRequirementsError("");
    setRequirementsSaveState("idle");
    setIsEditingRequirements(false);
  }

  function updateRequirementDocument(documentType: "resume" | "cover_letter" | "selection_criteria", changes: Partial<ApplicationRequirementsCorrectionDraft["documents"]["resume"]>) {
    setRequirementsEditDraft((current) => current ? {
      ...current,
      documents: { ...current.documents, [documentType]: { ...current.documents[documentType], ...changes } },
    } : current);
  }

  function toggleDraftDocumentGeneration(documentType: "cover_letter" | "selection_criteria", enabled: boolean) {
    updateRequirementDocument(documentType, enabled
      ? { requirement: "required", format: "standalone" }
      : { requirement: "not_required", format: "not_applicable" });
  }

  function toggleRequirementLimit(documentType: "cover_letter" | "selection_criteria", enabled: boolean) {
    const limit: SubmissionLimit | null = enabled
      ? { value: 1, unit: "pages", scope: "document", constraint: "maximum", source_text: "User-corrected limit" }
      : null;
    updateRequirementDocument(documentType, { limit });
  }

  function updateRequirementLimit(documentType: "cover_letter" | "selection_criteria", changes: Partial<SubmissionLimit>) {
    const currentLimit = requirementsEditDraft?.documents[documentType].limit;
    if (!currentLimit) return;
    updateRequirementDocument(documentType, { limit: { ...currentLimit, ...changes, source_text: "User-corrected limit" } });
  }

  async function saveApplicationRequirementsCorrections(draft = requirementsEditDraft) {
    if (!selectedApplication || !draft || requirementsSaveState === "saving") return;
    setRequirementsSaveState("saving");
    setRequirementsError("");
    try {
      const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/application-requirements`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "correct", ...draft }),
      });
      const result = await response.json().catch(() => null) as ApplicationRequirementsResponse | { detail?: string } | null;
      if (!response.ok || !result || !("requirements" in result)) {
        setRequirementsSaveState("error");
        return setRequirementsError(result && "detail" in result && result.detail ? result.detail : "Could not save the requirement corrections.");
      }
      setApplicationRequirements(result.requirements);
      setApplicationDecision(null);
      setRequirementsEditDraft(null);
      setIsEditingRequirements(false);
      setRequirementsSaveState("idle");
      setRequirementsSavedMessage(requirementsHasUnknown(result.requirements) ? "✓ Changes saved — some document choices still need your decision." : "✓ Saved");
      setDocuments([]); setReleaseChecklist(null); setPackReviewResult(null); setAtsResult(null);
      await openApplication(selectedApplication);
    } catch {
      setRequirementsSaveState("error");
      setRequirementsError("Could not save the requirement corrections. Check the connection and try again.");
    }
  }

  async function toggleDocumentGeneration(documentType: "cover_letter" | "selection_criteria", enabled: boolean) {
    if (!applicationRequirements) return;
    const draft = createCorrectionDraft(applicationRequirements);
    draft.documents[documentType] = {
      ...draft.documents[documentType],
      requirement: enabled ? "required" : "not_required",
      format: enabled ? "standalone" : "not_applicable",
      basis: "user_confirmed",
    };
    await saveApplicationRequirementsCorrections(draft);
  }

  async function openApplication(id: number) {
    setSelectedApplication(id);
    setPackNotice("");
    setConfirmedApplication(null);
    setQualityResult(null);
    setApplicationDecision(null);
    setApplicationDecisionCurrent(false);
    setReleaseChecklist(null);
    setPackReviewResult(null);
    setAtsResult(null);
    const [response, decisionResponse] = await Promise.all([
      authenticatedFetch(`${api}/applications/${id}/documents`),
      authenticatedFetch(`${api}/applications/${id}/decision`),
      loadApplicationRequirements(id),
      loadSources(id),
    ]);
    const loaded = response.ok ? await response.json() : [];
    setDocuments(loaded);
    if (decisionResponse.ok) {
      const result = await decisionResponse.json() as { decision: ApplicationDecision; current: boolean };
      if (result.decision?.status) {
        setApplicationDecision(result.decision);
        setApplicationDecisionCurrent(result.current);
      }
    }
    const firstAvailable = packTypes.find((type) => loaded.some((document: GeneratedDocument) => document.document_type === type));
    setActiveType(firstAvailable || "tailored_resume");
    await loadReleaseChecklist(id, submissionFormat, exportTemplate);
  }

  async function loadReleaseChecklist(id = selectedApplication, format = submissionFormat, template = exportTemplate) {
    if (!id) return null;
    try {
      const response = await authenticatedFetch(`${api}/applications/${id}/release-checklist?format=${format}&template=${template}`);
      if (!response.ok) throw new Error(response.status === 401 ? "Session expired." : "Verification unavailable.");
      const result = await response.json() as ReleaseChecklist;
      setReleaseChecklist(result);
      setPackReviewResult(result.checks.pack_review.current ? result.checks.pack_review.result : null);
      setAtsResult(result.checks.ats.ready ? result.checks.ats.result : null);
      setConfirmedApplication(result.checks.details_confirmation.ready ? id : null);
      return result;
    } catch (error) {
      const failed = releaseFailureState(); setReleaseChecklist(failed.checklist); setPackReviewResult(failed.packReview); setAtsResult(failed.ats);
      setNotice(error instanceof Error && error.message === "Session expired." ? error.message : "Verification unavailable: readiness could not be checked. Refresh and try again.");
      return null;
    }
  }

  async function confirmReleaseDetails() {
    if (!selectedApplication) return;
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/release-confirmation`, { method: "POST" });
    if (!response.ok) return setNotice("Could not confirm the applicant, job and contact details.");
    setConfirmedApplication(selectedApplication);
    await loadReleaseChecklist();
  }

  async function runPackReview() {
    if (!selectedApplication || releaseBusy !== "idle") return;
    setReleaseBusy("pack"); setNotice("Running Pack Review…");
    await withBusyReset(async () => { try {
      const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/pack-review`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : result.detail?.message || "Provider unavailable: Pack Review could not be completed.");
      setPackReviewResult(result); await loadReleaseChecklist();
      setNotice(result.skipped ? `Pack Review not required: ${result.skip_reason}` : result.blocks_release ? "Pack Review found blocking issues." : "Pack Review passed.");
    } catch (error) {
      setReleaseChecklist(null);
      setNotice(error instanceof Error ? error.message : "Network error: Pack Review failed. Try again.");
    } }, () => setReleaseBusy("idle"));
  }

  async function runAtsVerification() {
    const resume = latestDocuments.tailored_resume;
    if (!resume || releaseBusy !== "idle") return;
    setReleaseBusy("ats"); setNotice(`Verifying Resume #${resume.id} · ${submissionFormat.toUpperCase()} · ${exportTemplate}…`);
    await withBusyReset(async () => { try {
      const response = await authenticatedFetch(`${api}/documents/${resume.id}/ats-check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format: submissionFormat, template: exportTemplate }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Verification unavailable: ATS verification could not be completed.");
      setAtsResult(result); await loadReleaseChecklist();
      setNotice(result.ready ? "The selected Resume artifact passed ATS verification." : "The selected Resume artifact has blocking ATS issues.");
    } catch (error) {
      setReleaseChecklist(null); setAtsResult(null);
      setNotice(error instanceof Error ? error.message : "Network error: ATS verification failed. Try again.");
    } }, () => setReleaseBusy("idle"));
  }

  async function checkApplication() {
    const resume = latestDocuments.tailored_resume;
    if (!selectedApplication || !resume || releaseBusy !== "idle") return;
    if (draftSaveState === "dirty" || draftSaveState === "saving") return setNotice("Save your document changes before checking the application.");
    setReleaseBusy("pack"); setNotice("Checking your application…"); setQualityResult(null);
    try {
      const finalResponse = await authenticatedFetch(`${api}/applications/${selectedApplication}/quality-check`);
      const finalResult = await finalResponse.json().catch(() => null) as QualityResult | null;
      if (!finalResponse.ok || !finalResult) throw new Error("Application checks are temporarily unavailable. Try again.");
      setQualityResult(finalResult);
      if (!finalResult.ready) { await loadReleaseChecklist(); return setNotice("Your application needs attention. Review the affected document and try again."); }

      const packResponse = await authenticatedFetch(`${api}/applications/${selectedApplication}/pack-review`, { method: "POST" });
      const packResult = await packResponse.json();
      if (!packResponse.ok) throw new Error(typeof packResult.detail === "string" ? packResult.detail : packResult.detail?.message || "The consistency check could not be completed.");
      setPackReviewResult(packResult);
      if (packResult.blocks_release) { await loadReleaseChecklist(); return setNotice("The document consistency check found an issue. View check details, correct it and try again."); }

      const atsResponse = await authenticatedFetch(`${api}/documents/${resume.id}/ats-check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format: submissionFormat, template: exportTemplate }) });
      const ats = await atsResponse.json();
      if (!atsResponse.ok) throw new Error(typeof ats.detail === "string" ? ats.detail : ats.detail?.message || "The Resume compatibility check could not be completed.");
      setAtsResult(ats);
      const checklist = await loadReleaseChecklist();
      setNotice(ats.ready && checklist?.ready ? "Your application is ready to apply." : "Your Resume compatibility check needs attention. View check details before applying.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Application checks could not be completed. Try again.");
    } finally {
      setReleaseBusy("idle");
    }
  }

  async function generatePack() {
    if (!selectedApplication || !resumes.length) return;
    const showPackNotice = (message: string) => {
      setNotice(message);
      setPackNotice(message);
    };
    const documentTypes = requiredGeneratedDocumentTypes(applicationRequirements);
    const defaultTypes: readonly (typeof packTypes[number])[] = ["tailored_resume", "cover_letter"];
    const includesSelectionCriteria = documentTypes.includes("selection_criteria");
    let generationTypes: readonly (typeof packTypes[number])[] = documentTypes.length ? documentTypes : defaultTypes;
    let selectionCriteriaSkipped = false;
    if (includesSelectionCriteria) {
      const accessResponse = await authenticatedFetch(`${api}/selection-criteria/access`);
      if (!accessResponse.ok) return showPackNotice("Could not verify Selection Criteria access. Please try again.");
      const currentAccess = await accessResponse.json() as SelectionCriteriaAccess;
      setSelectionAccess(currentAccess);
      if (!currentAccess.unlimited && !currentAccess.remaining_credits) {
        generationTypes = documentTypes.filter((type) => type !== "selection_criteria");
        selectionCriteriaSkipped = true;
      }
    }
    const packId = crypto.randomUUID();
    setBusy(true);
    setGenerationFailure(null);
    showPackNotice("Preparing your complete application pack…");
    setDocuments([]);
    setQualityResult(null);
    setActiveType("tailored_resume");
    const created: GeneratedDocument[] = [];
    let generatingType: typeof packTypes[number] = "tailored_resume";
    try {
      for (let index = 0; index < generationTypes.length; index += 1) {
        const documentType = generationTypes[index];
        generatingType = documentType;
        showPackNotice(`Creating application pack: ${index + 1} of ${generationTypes.length} — ${labels[documentType]}…`);
        const response = await authenticatedFetch(`${api}/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ application_id: selectedApplication, document_type: documentType, pack_id: packId }),
          signal: AbortSignal.timeout(360_000),
        });
        const result = await response.json();
        if (!response.ok) {
          if (result.detail?.document_id) {
            const documentsResponse = await authenticatedFetch(`${api}/applications/${selectedApplication}/documents`);
            if (documentsResponse.ok) setDocuments(await documentsResponse.json());
          }
          throw new Error(typeof result.detail === "string" ? result.detail : result.detail?.message || `${labels[documentType]} could not be generated.`);
        }
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
      await loadReleaseChecklist();
    } catch (error) {
      const documentsResponse = await authenticatedFetch(`${api}/applications/${selectedApplication}/documents`);
      if (documentsResponse.ok) setDocuments(await documentsResponse.json());
      else if (created.length) setDocuments([...created].reverse());
      const detail = error instanceof Error ? error.message : "The application pack could not be completed.";
      setGenerationFailure({ documentType: generatingType, message: detail });
      showPackNotice(`${detail} This pack is incomplete, so application checks remain unavailable. The failed attempt has not used today's completed-pack allowance.`);
    } finally {
      setBusy(false);
    }
  }

  async function retryFailedDocument() {
    if (!selectedApplication || !generationFailure || busy) return;
    const { documentType } = generationFailure;
    setBusy(true);
    setPackNotice(`Retrying ${labels[documentType]}…`);
    try {
      const response = await authenticatedFetch(`${api}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ application_id: selectedApplication, document_type: documentType, pack_id: crypto.randomUUID() }),
        signal: AbortSignal.timeout(360_000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : result.detail?.message || `${labels[documentType]} could not be generated.`);
      setDocuments((current) => [result, ...current]);
      setGenerationFailure(null);
      setPackNotice(`${labels[documentType]} created. You can now continue with application checks.`);
      await loadReleaseChecklist();
    } catch (error) {
      const message = error instanceof Error ? error.message : `${labels[documentType]} could not be generated.`;
      setGenerationFailure({ documentType, message });
      setPackNotice(message);
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
    setPackReviewResult(null); setAtsResult(null);
    setDraftSaveState("saved");
    setNotice(`${labels[activeType]} edits saved.`);
    await loadReleaseChecklist();
  }

  async function reviewEditedDocument() {
    if (!activeDocument || documentReviewState === "reviewing") return;
    setDocumentReviewState("reviewing"); setNotice(`Re-reviewing edited ${labels[activeType]}…`);
    try {
      const response = await authenticatedFetch(`${api}/documents/${activeDocument.id}/review`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Provider unavailable: the edited document could not be reviewed.");
      setDocuments((current) => current.map((document) => document.id === result.id ? result : document));
      setNotice(JSON.parse(result.reviewer_json || "{}").status === "pass" ? "Edited document review passed. Run Final Check next." : "The edited document review found issues. Review them before Final Check.");
      await loadReleaseChecklist();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Provider unavailable: the edited document could not be reviewed.");
    } finally { setDocumentReviewState("idle"); }
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
    await loadReleaseChecklist();
    setNotice(result.ready ? "Content and grammar check passed. Review any warnings, then continue to the application page." : "Content and grammar check found errors. Fix them before applying.");
    return result as QualityResult;
  }

  async function diagnoseApplication() {
    if (!selectedApplication || decisionBusy) return;
    setDecisionBusy(true);
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/decision`, { method: "POST" });
    const result = await response.json();
    setDecisionBusy(false);
    if (!response.ok) return setPackNotice(result.detail || "Could not diagnose this application.");
    setApplicationDecision(result);
    setApplicationDecisionCurrent(true);
    setPackNotice(result.status === "ready" ? "Diagnosis ready. Review it before generating." : "Diagnosis needs your confirmation.");
  }

  async function answerDecisionQuestion(questionId: string, answer: boolean) {
    if (!selectedApplication || decisionBusy) return;
    setDecisionBusy(true);
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/decision/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question_id: questionId, answer }),
    });
    const result = await response.json();
    setDecisionBusy(false);
    if (!response.ok) return setPackNotice(result.detail || "Could not save this confirmation.");
    setApplicationDecision(result);
    setApplicationDecisionCurrent(true);
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
    const currentRelease = await loadReleaseChecklist();
    if (!releaseCanProceed(currentRelease)) {
      const message = "Complete every blocking item in the Release Checklist before opening the employer page.";
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
    const response = await authenticatedFetch(`${api}/applications/${selectedApplication}/prepare-submission?format=${submissionFormat}&template=${exportTemplate}`, { method: "POST" });
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

  async function updateApplicationArchive(application: Application, action: "archive" | "restore") {
    const response = await authenticatedFetch(`${api}/applications/${application.id}/archive`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }),
    });
    const result = await response.json();
    if (!response.ok) return setNotice(result.detail || `Could not ${action} the application.`);
    setApplications((current) => current.map((item) => item.id === result.id ? result : item));
    setStatusFilter(action === "archive" ? "archived" : "all");
    setNotice(`${result.position_title} ${action === "archive" ? "archived" : "restored"}.`);
  }

  async function deleteDraftApplication(application: Application) {
    if (!window.confirm(`Delete the draft for ${application.position_title}? You can then create a new job from the correct JD.`)) return;
    const response = await authenticatedFetch(`${api}/applications/${application.id}`, { method: "DELETE" });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      return setNotice(result.detail || "Could not delete the draft application.");
    }
    setApplications((current) => current.filter((item) => item.id !== application.id));
    if (selectedApplication === application.id) {
      setSelectedApplication(null); setDocuments([]); setApplicationRequirements(null); setApplicationDecision(null);
      setReleaseChecklist(null); setQualityResult(null); setPackNotice("");
    }
    setNotice(`${application.position_title} was deleted. Create a new application when you are ready.`);
  }

  async function permanentlyDeleteApplication(application: Application) {
    if (!window.confirm(`Permanently delete ${application.position_title}? This removes its documents and application history and cannot be undone.`)) return;
    const positionTitle = window.prompt(`Type the exact position title to confirm:\n${application.position_title}`);
    if (positionTitle === null) return;
    const response = await authenticatedFetch(`${api}/applications/${application.id}/permanent`, {
      method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ position_title: positionTitle }),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      return setNotice(result.detail || "Could not permanently delete the application.");
    }
    setApplications((current) => current.filter((item) => item.id !== application.id));
    if (selectedApplication === application.id) setSelectedApplication(null);
    setNotice(`${application.position_title} was permanently deleted.`);
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
  const documentsNeedRegeneration = Boolean(selected?.updated_at && documents.some((document) => new Date(document.created_at) < new Date(selected.updated_at as string)));
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
    await loadReleaseChecklist();
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

  async function exportAccountData() {
    try {
      const response = await authenticatedFetch(`${api}/account/export`);
      if (!response.ok) throw new Error("Could not export your account data.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "job-assistant-account-data.zip"; link.click(); URL.revokeObjectURL(url);
      setNotice("Your account-data archive was downloaded.");
    } catch { setNotice("Network error: account-data export failed. Try again."); }
  }

  async function deleteMyAccount() {
    if (!window.confirm("Delete your account and all saved application data permanently? This cannot be undone.")) return;
    const confirmation = window.prompt("Type DELETE MY ACCOUNT to confirm permanent deletion.");
    if (confirmation !== "DELETE MY ACCOUNT") return setNotice("Account deletion cancelled; the confirmation text did not match.");
    try {
      const response = await authenticatedFetch(`${api}/account`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Account deletion failed.");
      if (supabase) await supabase.auth.signOut({ scope: "local" });
      clearAuthenticatedState(); setNotice("Your account and saved application data were deleted.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Network error: account deletion failed. Contact the beta operator."); }
  }
  const requiredPackTypes = requiredGeneratedDocumentTypes(applicationRequirements);
  const generationLabel = requiredPackTypes.length
    ? `Generate ${requiredPackTypes.map((type) => labels[type]).join(requiredPackTypes.length > 1 ? ", " : "")}`.replace(/, ([^,]+)$/, " & $1")
    : "Generate Resume & Cover Letter";
  const packReady = requiredPackTypes.length > 0 && requiredPackTypes.every((type) => latestDocuments[type]);
  const active = activeApplications(applications);
  const archived = archivedApplications(applications);
  const statusCounts = useMemo(() => Object.fromEntries(applicationStatuses.map((status) => [status, activeApplications(applications).filter((application) => application.status === status).length])), [applications]);
  const filteredApplications = statusFilter === "archived" ? archived : statusFilter === "all" ? active : active.filter((application) => application.status === statusFilter);
  const privacyNotice = showPrivacy && <div className="modalBackdrop" role="presentation" onClick={() => setShowPrivacy(false)}><section className="privacyModal" role="dialog" aria-modal="true" aria-labelledby="privacy-title" onClick={(event) => event.stopPropagation()}><div className="requirementsHeading"><h2 id="privacy-title">Private Beta Privacy Notice</h2><button type="button" className="secondary" onClick={() => setShowPrivacy(false)}>Close</button></div><p>This service stores your profile, Resume, job descriptions, application records and generated documents. These materials may contain personal information.</p><p>Relevant Resume, job and application content is sent to the configured AI provider when the service generates or reviews documents. This is a beta service, so errors and interruptions may occur. Review every document yourself before submitting it.</p><p>Avoid uploading unnecessary highly sensitive information such as passwords, identity documents, health information or criminal-history details.</p><p>Use <strong>Export my data</strong> to download your account data. Use <strong>Delete my account</strong> to permanently remove your account and saved data.</p><p>For access, privacy or support questions, contact {betaSupportContact}.</p></section></div>;

  if (!authReady) return <main><section className="panel"><p>Preparing secure sign-in…</p></section></main>;

  if (supabase && ["checking", "password_setup", "saving", "error", "complete"].includes(activation.mode)) return <main>
    <header><p className="eyebrow">JOB APPLICATION ASSISTANT · PRIVATE BETA</p><h1>Activate your invited account.</h1></header>
    <section className="panel activationPanel">
      {activation.mode === "checking" && <p>Checking your invitation or recovery link…</p>}
      {(activation.mode === "password_setup" || activation.mode === "saving") && <form onSubmit={setActivationPassword} className="compactForm"><p className="full helper">Create a password of at least eight characters. Supabase Auth remains the authority for this invitation or recovery session.</p><label className="full">New password<input name="password" type="password" minLength={8} autoComplete="new-password" required /></label><button className="full" disabled={activation.mode === "saving"}>{activation.mode === "saving" ? "Saving…" : "Set password"}</button></form>}
      {activation.mode === "error" && <><div className="requirementsError"><strong>This link could not be used.</strong><p>{activation.message || "It may be expired, already used or malformed."}</p></div><p className="helper">Ask {betaSupportContact} for a new invitation or recovery link.</p><button type="button" onClick={() => { window.history.replaceState({}, "", window.location.pathname); setActivation({ mode: "idle" }); }}>Return to sign in</button></>}
      {activation.mode === "complete" && <><p>Password saved. Your invited account is active.</p><button type="button" onClick={() => setActivation({ mode: "idle" })}>Open workspace</button></>}
    </section>
  </main>;

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
    <footer className="safety"><button type="button" className="privacyLink" onClick={() => setShowPrivacy(true)}>Privacy / Beta Notice</button></footer>
    {privacyNotice}
  </main>;

  return <main>
    <header>
      <p className="eyebrow">JOB APPLICATION ASSISTANT</p>
      <h1>{applicationsPage ? "Your applications." : "From job description to a tailored CV and cover letter."}</h1>
      <p>{applicationsPage ? "Review, continue or organise each saved application." : "Keep one truthful Master CV, add a job, and prepare the two documents most applications need. Selection Criteria is added only when the employer asks for it."}</p>
      {applicationsPage && <p><a className="secondary pageLink" href="/">Back to dashboard</a> <a className="pageLink" href="/#add-job">Add a job</a></p>}
      {supabase && <div className="accountActions"><button type="button" className="secondary" onClick={exportAccountData}>Export my data</button><button type="button" className="secondary dangerButton" onClick={deleteMyAccount}>Delete my account</button><button type="button" className="secondary" onClick={signOut}>Sign out</button></div>}
    </header>
    <p className="notice">{notice}</p>

    <section className="steps">
    {!applicationsPage && (
    <>
    <section className="workspaceOverview" aria-label="Application overview">
      <div className="overviewStats"><div><strong>{active.length}</strong><small>All applications</small></div><div><strong>{statusCounts.ready_to_apply || 0}</strong><small>Ready</small></div><div><strong>{statusCounts.applied || 0}</strong><small>Applied</small></div></div>
      <div className="overviewRecent"><strong>Recent applications</strong>{active.length ? active.slice(0, 3).map((application) => <a href={`/applications?application=${application.id}`} key={application.id}><span>{application.position_title}</span><small>{application.company} · {statusLabels[application.status] || application.status}</small></a>) : <small>No saved jobs yet.</small>}</div>
      <div className="overviewActions"><button type="button" onClick={() => document.getElementById("add-job")?.scrollIntoView({ behavior: "smooth" })}>Add a job</button><a className="secondary pageLink" href="/applications">View all applications</a></div>
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
        <form key={resumeEditorVersion(resumes[0])} onSubmit={saveResume} className="formBody resumeTextForm">
          <label>Resume title<input name="title" defaultValue={resumes[0]?.title || "Master Resume"} required /></label>
          <label>Resume text<textarea name="source_text" defaultValue={resumes[0]?.source_text || ""} rows={14} required /></label>
          <div className="experienceBuilder">
            <div className="experienceHeader"><div><strong>Structured work experiences</strong><p className="helper">These facts help Selection Criteria use STAR naturally and prevent invented results.</p></div><button type="button" className="secondary" onClick={addExperience}>Add experience</button></div>
            {experiences.map((experience, index) => <fieldset className="experienceCard" key={experience.id}><legend>Experience {index + 1}</legend>
              <div className="compactForm">
                <label>Role title<input value={experience.role_title} onChange={(event) => updateExperience(experience.id, "role_title", event.target.value)} required /></label>
                <label>Organisation<input value={experience.organization} onChange={(event) => updateExperience(experience.id, "organization", event.target.value)} required /></label>
                <label>Employment period <em>optional</em><input value={experience.time_period_text || ""} onChange={(event) => updateExperience(experience.id, "time_period_text", event.target.value)} placeholder="e.g. Feb 2026 – Present" /></label>
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
          <label className="full">Application link<div className="linkImportRow"><input name="job_url" type="url" placeholder="https://example.com/job" value={jobFields.job_url} onChange={(event) => setJobFields({ ...jobFields, job_url: event.target.value, discovered_sources: [] })} /><button type="button" onClick={importJobLink} disabled={jobImportState === "importing"}>{jobImportState === "importing" ? "Reading…" : jobImportState === "done" ? "Imported ✓" : "Import Details"}</button></div></label>
          <label className="full">Job description<textarea name="job_description" rows={8} value={jobFields.job_description} onChange={(event) => setJobFields({ ...jobFields, job_description: event.target.value })} required /></label>
          <label className="full">Selection criteria or short guidance <em>optional</em><textarea name="selection_criteria" rows={4} value={jobFields.selection_criteria} onChange={(event) => setJobFields({ ...jobFields, selection_criteria: event.target.value })} placeholder="Paste the full criteria, or add a short instruction such as: Focus on stakeholder engagement and government reporting." /><small>Short guidance will be expanded using explicit JD requirements and your saved CV evidence.</small></label>
          <button className="full">Save Job</button>
        </form>
      </section>
    </>
    )}
      <section className="panel" id="application-tracker">
        <div className="stepHeading"><span>3</span><div><strong>Application Tracker</strong><small>Review and update every application in one place</small></div></div>
        <div className="statusFilters">
          <button type="button" className={statusFilter === "all" ? "statusCard activeStatus" : "statusCard"} onClick={() => setStatusFilter("all")}><strong>{active.length}</strong><small>All jobs</small></button>
          {applicationStatuses.map((status) => <button type="button" key={status} className={statusFilter === status ? "statusCard activeStatus" : "statusCard"} onClick={() => setStatusFilter(status)}><strong>{statusCounts[status] || 0}</strong><small>{statusLabels[status]}</small></button>)}
          <button type="button" className={statusFilter === "archived" ? "statusCard activeStatus" : "statusCard"} onClick={() => setStatusFilter("archived")}><strong>{archived.length}</strong><small>Archived</small></button>
        </div>
        <div className="trackerList">
          {filteredApplications.length ? filteredApplications.map((application) => <div className="trackerRow" key={application.id}>
            <button type="button" className="trackerJob" onClick={() => openApplication(application.id)}><strong>{application.position_title}</strong><small>{application.company}{application.submitted_at ? ` · Applied ${new Date(application.submitted_at).toLocaleDateString()}` : ""}{application.submission_reference ? ` · ${application.submission_reference}` : ""}</small></button>
            <div className="selectedActions"><select aria-label={`Status for ${application.position_title}`} value={application.status} onChange={(event) => updateApplicationStatus(application, event.target.value)}>{applicationStatuses.map((status) => <option value={status} key={status}>{statusLabels[status]}</option>)}</select><button type="button" className="secondary" onClick={() => updateApplicationArchive(application, application.archived_at ? "restore" : "archive")}>{application.archived_at ? "Restore" : "Archive"}</button>{application.archived_at && <button type="button" className="secondary dangerButton" onClick={() => permanentlyDeleteApplication(application)}>Delete permanently</button>}</div>
          </div>) : <p className="helper">No applications in this status.</p>}
        </div>
      </section>

      <section className="panel" id="application-workspace">
        <div className="stepHeading"><span>4</span><div><strong>Create and check your application</strong><small>Choose documents, generate drafts, review them, then check and download</small></div></div>
        <div className="applicationLayout">
          <aside className="jobList">
            {filteredApplications.length ? filteredApplications.map((application) => <button type="button" className={application.id === selectedApplication ? "job active" : "job"} key={application.id} onClick={() => openApplication(application.id)}>
              <strong>{application.position_title}</strong><small>{application.company} · {statusLabels[application.status] || application.status}</small>
            </button>) : <p className="helper">Save a job to get started.</p>}
          </aside>
          <div className="reviewArea">
            {selected ? <>
              <div className="selectedJob"><div><strong>{selected.position_title}</strong><small>{selected.company}{selected.submitted_at ? ` · Applied ${new Date(selected.submitted_at).toLocaleDateString()}` : ""}{selected.submission_reference ? ` · Confirmation ${selected.submission_reference}` : ""}</small></div><div className="selectedActions">{selected.status === "draft" ? <button className="secondary dangerButton" type="button" onClick={() => deleteDraftApplication(selected)}>Delete draft</button> : <button className="secondary" type="button" onClick={() => updateApplicationArchive(selected, selected.archived_at ? "restore" : "archive")}>{selected.archived_at ? "Restore" : "Archive"}</button>}{selected.archived_at && <button className="secondary dangerButton" type="button" onClick={() => permanentlyDeleteApplication(selected)}>Delete permanently</button>}<button className="secondary" type="button" onClick={copyApplicationLink}>Copy Application Link</button></div></div>
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
              <p className="helper"><strong>Steps:</strong> Add Job → Choose Documents → Generate → Review &amp; Edit → Check Application → Download &amp; Apply.</p>
              {packNotice && <p className="notice applicationNotice" role="status" aria-live="polite">{packNotice}</p>}
              <div className={confirmedApplication === selected.id ? "confirmCard confirmed" : "confirmCard"}>
                <div><strong>Check application details</strong><p>Position: {selected.position_title}<br />Organisation: {selected.company}<br />Phone: {profile?.phone || "No saved profile"}<br />Email: {profile?.email || "No saved profile"}</p></div>
                <button type="button" disabled={!profile || !selected.company.trim() || !selected.position_title.trim() || confirmedApplication === selected.id} onClick={confirmReleaseDetails}>{confirmedApplication === selected.id ? "Details confirmed ✓" : "Confirm these details"}</button>
              </div>
              <section className={`requirementsCard ${applicationRequirements && requirementsHasUnknown(applicationRequirements) ? "needs_confirmation" : applicationRequirements?.review_status || "loading"}`} aria-live="polite">
                <div className="requirementsHeading">
                  <div><strong>Documents for this application</strong><small>Choose the documents you want to prepare before checking your match.</small></div>
                  {applicationRequirements && <span className="requirementsStatus">{requirementsHasUnknown(applicationRequirements) ? "Needs resolution" : getRequirementsStatusLabel(applicationRequirements.review_status)}</span>}
                </div>
                {requirementsLoadState === "loading" && <p className="helper">Loading application requirements…</p>}
                {requirementsLoadState === "error" && <div className="requirementsError" role="alert"><strong>Requirements could not be loaded</strong><p>{requirementsError}</p><button type="button" className="secondary" onClick={() => loadApplicationRequirements(selected.id)}>Retry</button></div>}
                {requirementsLoadState === "success" && applicationRequirements && <>
                  {applicationRequirements.source === "legacy_inference" && <div className="legacyRequirementNotice"><strong>Legacy estimate</strong><p>These requirements were inferred from the previous application-pack behaviour. Check them against the job advertisement.</p></div>}
                  <p className="requirementsStatusHelp">Resume and Cover Letter are included by default. Selection Criteria is off unless you choose it.</p>
                  {requirementsSavedMessage && <p className="saveStatus" data-state="saved" role="status">{requirementsSavedMessage}</p>}
                  {requirementsHasUnknown(applicationRequirements) && <div className="unknownRequirementNotice"><strong>⚠ We’re not sure what documents this employer wants.</strong><p>Review requirements and decide:</p><ul>{unresolvedRequirementLabels(applicationRequirements).map((label) => <li key={label}>{label}</li>)}</ul></div>}
                  {!isEditingRequirements ? <>
                    <div className="documentChoices">{(["resume", "cover_letter", "selection_criteria"] as const).map((name) => { const document = applicationRequirements.documents[name]; const title = name === "resume" ? "Resume" : name === "cover_letter" ? "Cover Letter" : "Selection Criteria"; const selectedForGeneration = name === "resume" || document.requirement === "required"; return <article className="documentChoice" key={name}>{name === "resume" ? <span aria-hidden="true">✓</span> : <input aria-label={`Generate ${title}`} type="checkbox" checked={selectedForGeneration} disabled={requirementsSaveState === "saving"} onChange={(event) => void toggleDocumentGeneration(name, event.target.checked)} />}<div><strong>{title}</strong><small>{name === "resume" ? "Always included" : selectedForGeneration ? "Will be generated" : "Click to include"}</small>{name === "selection_criteria" && document.format === "embedded_in_cover_letter" && <small>Addressed inside the Cover Letter</small>}</div></article>; })}</div>
                    <details className="requirementsSource"><summary>View requirement details</summary><div className="requirementsGrid">{(["resume", "cover_letter", "selection_criteria"] as const).map((name) => { const document = applicationRequirements.documents[name]; return <article key={name}><strong>{name === "resume" ? "Resume" : name === "cover_letter" ? "Cover Letter" : "Selection Criteria"}</strong><dl><div><dt>Choice</dt><dd>{formatRequirementLabel(document.requirement)}</dd></div><div><dt>Format</dt><dd>{formatDocumentFormat(document.format)}</dd></div><div><dt>Limit</dt><dd>{formatSubmissionLimit(document.limit)}</dd></div></dl></article>; })}</div></details>
                    {applicationRequirements.additional_documents.length > 0 && <div className="additionalRequirements"><strong>Supporting / Additional documents</strong><ul>{applicationRequirements.additional_documents.map((document) => <li key={document}>{document}</li>)}</ul></div>}
                    {applicationRequirements.warnings.length > 0 && <details className="requirementsSource"><summary>View source notes</summary><ul>{applicationRequirements.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}
                    <details className="requirementsSource"><summary>Why this was detected</summary>{applicationRequirements.source_excerpt ? <blockquote>{applicationRequirements.source_excerpt}</blockquote> : applicationRequirements.source === "legacy_inference" ? <p>No source excerpt is available for this legacy application.</p> : <p>No source excerpt was identified.</p>}{applicationRequirements.source_text && <details><summary>Show full source</summary><pre>{applicationRequirements.source_text}</pre></details>}</details>
                    {requirementsError && <p className="requirementsError" role="alert">{requirementsError}</p>}
                    <div className="requirementsActions"><button type="button" className="secondary" onClick={beginRequirementsEdit} disabled={requirementsSaveState === "saving"}>Advanced format options</button></div>
                  </> : requirementsEditDraft && <div className="requirementsEditor">
                    <p className="editModeNotice"><strong>Editing document choices</strong> — save your changes below.</p>
                    <fieldset><legend>Resume</legend><p className="helper">Always included with a valid Master Resume.</p><label>Format<select value={requirementsEditDraft.documents.resume.format} onChange={(event) => updateRequirementDocument("resume", { format: event.target.value as DocumentFormat })}>{coverFormatOptions.filter((option) => option !== "not_applicable" && option !== "unknown").map((option) => <option value={option} key={option}>{formatDocumentFormat(option)}</option>)}</select></label></fieldset>
                    <fieldset><legend>Cover Letter</legend><label className="requirementCheckbox"><input type="checkbox" checked={requirementsEditDraft.documents.cover_letter.requirement === "required"} onChange={(event) => toggleDraftDocumentGeneration("cover_letter", event.target.checked)} /> Generate a Cover Letter</label>{requirementsEditDraft.documents.cover_letter.requirement === "required" && <><label>Format<select value={requirementsEditDraft.documents.cover_letter.format} onChange={(event) => updateRequirementDocument("cover_letter", { format: event.target.value as DocumentFormat })}>{coverFormatOptions.filter((option) => option !== "not_applicable" && option !== "unknown").map((option) => <option value={option} key={option}>{formatDocumentFormat(option)}</option>)}</select></label><RequirementLimitEditor limit={requirementsEditDraft.documents.cover_letter.limit} onToggle={(enabled) => toggleRequirementLimit("cover_letter", enabled)} onChange={(changes) => updateRequirementLimit("cover_letter", changes)} /></>}</fieldset>
                    <fieldset><legend>Selection Criteria</legend><label className="requirementCheckbox"><input type="checkbox" checked={requirementsEditDraft.documents.selection_criteria.requirement === "required"} onChange={(event) => toggleDraftDocumentGeneration("selection_criteria", event.target.checked)} /> Generate Selection Criteria</label>{requirementsEditDraft.documents.selection_criteria.requirement === "required" && <><label>Response format<select value={requirementsEditDraft.documents.selection_criteria.format} onChange={(event) => updateRequirementDocument("selection_criteria", { format: event.target.value as DocumentFormat })}>{selectionFormatOptions.filter((option) => option !== "not_applicable" && option !== "unknown").map((option) => <option value={option} key={option}>{formatDocumentFormat(option)}</option>)}</select></label><label>Criteria count <em>leave blank if unknown</em><input type="number" min="0" value={requirementsEditDraft.documents.selection_criteria.criteria_count ?? ""} onChange={(event) => updateRequirementDocument("selection_criteria", { criteria_count: event.target.value === "" ? null : Number(event.target.value) })} /></label><RequirementLimitEditor limit={requirementsEditDraft.documents.selection_criteria.limit} onToggle={(enabled) => toggleRequirementLimit("selection_criteria", enabled)} onChange={(changes) => updateRequirementLimit("selection_criteria", changes)} /></>}</fieldset>
                    {applicationRequirements.additional_documents.length > 0 && <div className="additionalRequirements"><strong>Supporting / Additional documents</strong><p className="helper">Shown for reference in this first editor version.</p><ul>{applicationRequirements.additional_documents.map((document) => <li key={document}>{document}</li>)}</ul></div>}
                    {requirementsError && <p className="requirementsError" role="alert">{requirementsError}</p>}
                    <div className="requirementsActions"><button type="button" onClick={() => void saveApplicationRequirementsCorrections()} disabled={requirementsSaveState === "saving"}>{requirementsSaveState === "saving" ? "Saving…" : "Save corrections"}</button><button type="button" className="secondary" onClick={cancelRequirementsEdit} disabled={requirementsSaveState === "saving"}>Cancel</button></div>
                  </div>}
                  <div className="requirementsActions"><button type="button" disabled={busy || !canGenerate(Boolean(selected.job_description.trim()), resumes.length > 0)} title={!selected.job_description.trim() ? "Add a job description before generating." : !resumes.length ? "Upload a Resume before generating." : ""} onClick={generatePack}>{busy ? "Generating documents…" : generationLabel}</button></div>
                </>}
              </section>
              {generationFailure && <section className="requirementsError generationFailure" role="alert"><strong>{labels[generationFailure.documentType]} was not created</strong><p>{generationFailure.message}</p><button type="button" onClick={retryFailedDocument} disabled={busy}>{busy ? "Retrying…" : `Retry ${labels[generationFailure.documentType]}`}</button></section>}
              <section className="sourcesCard" aria-live="polite">
                <div className="requirementsHeading"><div><strong>Application Sources</strong><small>Documents found or referenced for this application.</small></div></div>
                {sourcesLoadState === "loading" && <p className="helper">Loading application sources…</p>}
                {sourcesLoadState === "error" && <div className="requirementsError" role="alert"><strong>Sources could not be loaded</strong><p>{sourcesError}</p><button type="button" className="secondary" onClick={() => loadSources(selected.id)}>Retry</button></div>}
                {sourcesLoadState === "success" && <div className="sourceList">{sources.map((source) => {
                  const warnings = sourceWarnings(source);
                  const canUpload = ["job_description_attachment", "application_instruction_attachment"].includes(source.source_type) && (["discovered", "unavailable", "requires_auth", "failed"].includes(source.acquisition_status) || source.extraction_status === "failed");
                  return <article className={`sourceRow ${source.acquisition_status} ${source.extraction_status}`} key={source.source_id}>
                    <div><strong>{source.title || sourceTypeLabels[source.source_type] || source.label}</strong><small>{sourceTypeLabels[source.source_type] || source.source_type.replaceAll("_", " ")} · {sourceStateLabel(source)}</small>{warnings.map((warning) => <p className="sourceWarning" key={warning}>{warning}</p>)}{source.source_url && <a href={source.source_url} target="_blank" rel="noreferrer">View source link</a>}</div>
                    {canUpload && <label className="sourceUploadButton">{sourceUploadId === source.source_id ? "Uploading…" : "Upload missing document"}<input type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" disabled={Boolean(sourceUploadId)} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; uploadMissingSource(source, file); }} /></label>}
                  </article>;
                })}</div>}
                {sourcesLoadState === "success" && sourcesError && <p className="requirementsError" role="alert">{sourcesError}</p>}
              </section>
              <section className={`requirementsCard ${applicationDecision?.status || "loading"}`} aria-live="polite">
                <div className="requirementsHeading"><div><strong>Application diagnosis</strong><small>Optional guidance about evidence coverage and application risks.</small></div>{applicationDecision && <span className="requirementsStatus">{decisionLabel(applicationDecision.application_recommendation)}</span>}</div>
                {!applicationDecision ? <><p className="helper">Generate when you are ready, or run a diagnosis first for tailored suggestions.</p><button type="button" onClick={diagnoseApplication} disabled={decisionBusy || !resumes.length}>{decisionBusy ? "Checking…" : "Check application"}</button></> : <>
                  {!applicationDecisionCurrent && <div className="requirementsWarnings"><strong>Based on changed job details</strong><p>Run the check again before relying on this diagnosis.</p></div>}
                  {applicationDecision.diagnosed_at && <p className="helper">Last checked: {new Date(applicationDecision.diagnosed_at).toLocaleString()}</p>}
                  {applicationDecision.blocking_issues.length > 0 && <div className="requirementsWarnings"><strong>Things to review</strong><ul>{applicationDecision.blocking_issues.map((issue) => <li key={issue.criteria_id}>{issue.message}</li>)}</ul></div>}
                  <div className="requirementsGrid">{applicationDecision.requirements.map((item) => <article key={item.criteria_id}><strong>{item.requirement_text}</strong><dl><div><dt>Importance</dt><dd>{decisionLabel(item.importance)}</dd></div><div><dt>Evidence</dt><dd>{item.evidence_classification ? decisionLabel(item.evidence_classification) : "Unsupported — no candidate conclusion"}</dd></div><div><dt>Risk</dt><dd>{decisionLabel(item.risk)}</dd></div><div><dt>Action</dt><dd>{decisionLabel(item.recommended_action)}</dd></div></dl></article>)}</div>
                  {applicationDecision.questions.filter((question) => question.material).map((question) => <div className="confirmCard" key={question.question_id}><div><strong>Material confirmation</strong><p>{question.prompt}</p>{question.answer !== null && <small>Recorded as {question.answer ? "Yes" : "No"} · user confirmed</small>}</div><div className="selectedActions"><button type="button" onClick={() => answerDecisionQuestion(question.question_id, true)} disabled={decisionBusy}>Yes</button><button type="button" className="secondary" onClick={() => answerDecisionQuestion(question.question_id, false)} disabled={decisionBusy}>No</button></div></div>)}
                  <button type="button" className="secondary" onClick={diagnoseApplication} disabled={decisionBusy}>{decisionBusy ? "Checking…" : "Run diagnosis again"}</button>
                </>}
              </section>
              {documents.length ? <>
                {documentsNeedRegeneration && <div className="requirementsWarnings"><strong>Earlier documents need regeneration</strong><p>They were created before the latest job details or document choices were saved.</p></div>}
                {releaseChecklist && <section className={`releaseChecklist ${releaseChecklist.ready ? "pass" : "pending"}`}>
                  <div className="requirementsHeading"><div><strong>Check application</strong><small>Checks accuracy, consistency and Resume compatibility before you apply.</small></div><span className="requirementsStatus">{releaseChecklist.ready ? "Ready to apply" : documents.some((document) => { try { return ["pending", "provider_failed"].includes(JSON.parse(document.reviewer_json || "{}").status); } catch { return false; } }) ? "Needs attention" : releaseChecklist.checks.final_check.ready ? "Documents reviewed" : "Draft"}</span></div>
                  <ul className="releaseChecks">
                    <li data-ready={releaseChecklist.checks.documents.ready}>Required documents {releaseChecklist.checks.documents.ready ? "present" : "missing"}</li>
                    <li data-ready={releaseChecklist.checks.details_confirmation.ready}>Applicant, job and contact details {releaseChecklist.checks.details_confirmation.ready ? "confirmed" : "need confirmation"}</li>
                    <li data-ready={releaseChecklist.checks.selection_confirmations.ready}>Selection Criteria confirmations {releaseChecklist.checks.selection_confirmations.ready ? "complete" : "incomplete"}</li>
                    <li data-ready={releaseChecklist.checks.final_check.ready}>Content check {releaseChecklist.checks.final_check.ready ? "passed" : "not started"}</li>
                    <li data-ready={releaseChecklist.checks.pack_review.ready}>Consistency check {releaseChecklist.checks.pack_review.ready ? (packReviewResult?.skipped ? `not required — ${packReviewResult.skip_reason}` : "passed") : "not started"}</li>
                    <li data-ready={releaseChecklist.checks.ats.ready}>Resume compatibility · {submissionFormat.toUpperCase()} · {exportTemplate}: {releaseChecklist.checks.ats.ready ? "passed" : "not started"}</li>
                  </ul>
                  <div className="selectedActions"><button type="button" onClick={checkApplication} disabled={!packReady || releaseBusy !== "idle"} title={!packReady ? "Generate and review every selected document first." : ""}>{releaseBusy !== "idle" ? "Checking application…" : "Start application checks"}</button></div>
                  <details className="requirementsSource"><summary>View check details</summary><p className="helper">Checks run in order: document content, pack consistency, then Resume compatibility.</p><div className="selectedActions"><button type="button" className="secondary" onClick={runFinalCheck} disabled={finalCheckState === "checking"}>{finalCheckState === "checking" ? "Checking…" : "Run content check"}</button><button type="button" className="secondary" onClick={runPackReview} disabled={!releaseChecklist.checks.final_check.ready || releaseBusy !== "idle"}>{releaseBusy === "pack" ? "Reviewing…" : "Run consistency check"}</button><button type="button" className="secondary" onClick={runAtsVerification} disabled={!releaseChecklist.checks.pack_review.ready || releaseBusy !== "idle"}>{releaseBusy === "ats" ? "Verifying…" : "Check Resume compatibility"}</button></div></details>
                  {packReviewResult?.results?.some((result) => result.issues?.length) && <div className={packReviewResult.blocks_release ? "requirementsError" : "requirementsWarnings"}><strong>Pack Review findings</strong><ul>{packReviewResult.results.flatMap((result) => result.issues.map((issue, index) => <li key={`${result.document_type}-${index}`}>{labels[result.document_type] || result.document_type}: {issue.description}{issue.blocks_release ? " (blocking)" : " (advisory)"}</li>))}</ul></div>}
                  {atsResult && !atsResult.ready && <div className="requirementsError"><strong>ATS blockers</strong><ul>{atsResult.checks.filter((item) => item.blocking && item.state !== "pass").map((item) => <li key={item.code}>{item.message}</li>)}</ul></div>}
                  {releaseChecklist.warnings.length > 0 && <details className="requirementsWarnings"><summary>Advisory warnings ({releaseChecklist.warnings.length})</summary><ul>{releaseChecklist.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul></details>}
                </section>}
                {activeReviewer && <div className={activeReviewer.status === "pass" ? "reviewerResult pass" : "reviewerResult fail"}><strong>{activeReviewer.status === "pass" ? `${labels[activeType]} review passed` : activeReviewer.status === "provider_failed" || activeReviewer.status === "pending" ? `${labels[activeType]} needs review` : `${labels[activeType]} review found issues`}</strong>{activeReviewer.status === "provider_failed" && <p>The draft is saved. Retry its automatic review before checking the application.</p>}{activeReviewer.status === "fail" && <details><summary>View check details</summary><ul>{(activeReviewer.results || []).flatMap((result) => result.issues.map((issue) => <li key={`${result.criteria_id}-${issue.type}`}>{result.criteria_id}{issue.severity ? ` [${issue.severity.toUpperCase()}]` : ""}: {issue.description}</li>))}</ul></details>}</div>}
                {activeType === "selection_criteria" && selectionPlan.length > 0 && <div className="criteriaReviewPanel"><div><strong>Selection Criteria evidence check</strong><p className="helper">Strong is ready for normal review. Transferable and Weak responses require your explicit confirmation.</p></div>{selectionPlan.map((item) => { const review = reviewerByCriteria[item.criteria_id]; const needsConfirmation = item.evidence_status !== "strong"; return <article className={`criterionReviewCard ${item.evidence_status}`} key={item.criteria_id}><div className="criterionReviewHeading"><span className="criterionStatus">{item.evidence_status === "strong" ? "Strong" : item.evidence_status === "transferable" ? "Transferable" : "Weak"}</span><span className={review?.status === "pass" ? "reviewStatus pass" : "reviewStatus fail"}>{review?.status === "pass" ? "Reviewer passed" : "Needs review"}</span></div><strong>{item.criteria_text}</strong><small>Target: {item.allocated_word_limit} words · {item.match_type} match · {item.coverage} coverage</small><div className="criterionSources"><small>Evidence sources</small>{item.matched_evidence.length ? <ul>{item.matched_evidence.map((evidenceId) => <li key={evidenceId}><code>{evidenceId}</code> {ckbById[evidenceId]?.source_section || "Uploaded Master CV"}</li>)}</ul> : <p>No direct evidence matched. Check this response carefully.</p>}</div>{review?.issues?.length > 0 && <ul className="criterionIssues">{review.issues.map((issue: { type: string; description: string }) => <li key={issue.type}>{issue.description}</li>)}</ul>}{needsConfirmation && <label className="criterionConfirmation"><input type="checkbox" checked={confirmedSelectionCriteria.includes(item.criteria_id)} onChange={(event) => setCriterionConfirmation(item.criteria_id, event.target.checked)} /> I reviewed this {item.evidence_status} response and confirm it is truthful.</label>}</article>; })}</div>}
                <nav className="tabs">{requiredPackTypes.map((type) => <button key={type} className={activeType === type ? "activeTab" : "tab"} onClick={() => setActiveType(type)} disabled={!latestDocuments[type]}>{labels[type]}{latestDocuments[type] ? " ✓" : ""}</button>)}</nav>
                <div className="templatePicker"><div><strong>Selected Resume submission artifact</strong><small>Changing format or style requires ATS verification for the new artifact.</small></div><select aria-label="Submission format" value={submissionFormat} onChange={(event) => { const value = event.target.value as "docx" | "pdf"; setSubmissionFormat(value); setAtsResult(null); void loadReleaseChecklist(selectedApplication, value, exportTemplate); }}><option value="docx">DOCX</option><option value="pdf">PDF</option></select><select aria-label="Export style" value={exportTemplate} onChange={(event) => { const value = event.target.value as "classic" | "modern" | "traditional"; setExportTemplate(value); setAtsResult(null); void loadReleaseChecklist(selectedApplication, submissionFormat, value); }}><option value="classic">Classic — Calibri</option><option value="modern">Modern — Arial</option><option value="traditional">Traditional — Georgia</option></select></div>
                {activeDocument && <div className="editor"><p className="helper"><strong>Latest generated:</strong> {new Date(activeDocument.created_at).toLocaleString()} · Document #{activeDocument.id}</p>{activeEvidence.length > 0 && <details className="evidenceTrace"><summary>View evidence and check details</summary><ul>{activeEvidence.map((item) => <li key={item.id}><code>{item.id}</code> {item.label}</li>)}</ul></details>}<textarea aria-label={labels[activeType]} value={draftText} onChange={(event) => { setDraftText(event.target.value); setDraftSaveState("dirty"); }} rows={24} /><div className="saveStatus" data-state={draftSaveState}>{draftSaveState === "dirty" ? "Unsaved changes" : draftSaveState === "saving" ? "Saving…" : draftSaveState === "error" ? "Save failed — try again" : "All changes saved ✓"}</div><div className="editorActions"><button className="secondary" onClick={() => navigator.clipboard.writeText(draftText)}>Copy</button><button className={`saveEdits ${draftSaveState}`} onClick={saveDraft} disabled={draftSaveState === "saving" || draftSaveState === "saved"}>{draftSaveState === "saving" ? "Saving…" : draftSaveState === "saved" ? "Saved ✓" : "Save edits"}</button>{(!activeReviewer || activeReviewer.status === "provider_failed" || activeReviewer.status === "pending") && draftSaveState === "saved" && <button type="button" onClick={reviewEditedDocument} disabled={documentReviewState === "reviewing"}>{documentReviewState === "reviewing" ? "Reviewing…" : activeReviewer?.status === "provider_failed" ? `Retry ${labels[activeType]} review` : "Review edited document"}</button>}<button className="secondary" onClick={() => downloadDocument("docx")}>Draft DOCX</button><button className="secondary" onClick={() => downloadDocument("pdf")}>Draft PDF</button><button className="secondary" onClick={downloadTrace}>Audit trace</button>{packReady && <><button className="secondary" onClick={() => downloadPack("docx")}>Draft pack DOCX</button><button className="secondary" onClick={() => downloadPack("pdf")}>Draft pack PDF</button><button onClick={reviewAndApply} disabled={!releaseChecklist?.ready} title={!releaseChecklist?.ready ? "Check your application before applying." : ""}>Download &amp; Apply</button><button className="secondary" onClick={markApplied} disabled={selected.status !== "ready_to_apply"}>{selected.status === "applied" ? "Applied ✓" : "Mark as Applied"}</button></>}</div>{qualityResult && <div className={qualityResult.ready ? "qualityResult pass" : "qualityResult fail"}><strong>{qualityResult.ready ? "Application content checks passed" : "Fix these items before applying"}</strong>{qualityResult.issues.length ? <ul>{qualityResult.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><b>{issue.severity === "error" ? "Error" : "Warning"}:</b> {issue.message}{issue.document_type ? ` (${labels[issue.document_type] || issue.document_type})` : ""}</li>)}</ul> : <p>No issues found.</p>}</div>}</div>}
              </> : <div className="emptyState"><strong>Your required application documents will appear here.</strong><p>A standalone Selection Criteria document is created only when the confirmed employer requirements request one.</p></div>}
            </> : <div className="emptyState"><strong>Select a saved job.</strong><p>Then generate the complete application pack in one click.</p></div>}
          </div>
        </div>
      </section>
    </section>
    <footer className="safety">AI prepares drafts from your Master Resume only. You review the facts and make the final submission. Login and CAPTCHA are never bypassed. <button type="button" className="privacyLink" onClick={() => setShowPrivacy(true)}>Privacy / Beta Notice</button></footer>
    {privacyNotice}
  </main>;
}

export default function Home() {
  return <Workspace />;
}

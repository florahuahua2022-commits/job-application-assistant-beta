import { groupReviewFindings, type ReviewFinding } from "./releaseWorkflow";

export function ReviewFindings({ findings }: { findings: ReviewFinding[] }) {
  const groups = groupReviewFindings(findings);
  return <>{(["error", "warning", "advisory"] as const).map(level => {
    const items = groups[level];
    if (!items.length) return null;
    const label = level === "error" ? "Error" : level === "warning" ? "Warning" : "Advisory";
    const list = <ul>{items.map((issue, index) => <li key={index}>
      {(issue.type || issue.code) === "resume_too_brief" && <p><strong>Source detail needs assessment</strong></p>}
      <b>{label}:</b> {issue.text}{issue.document_type && <small> ({issue.document_type.replaceAll("_", " ")})</small>}
      {level === "warning" && issue.blocks_release && <p>Verify before continuing</p>}
      {issue.location && <blockquote>{issue.location}</blockquote>}
      {issue.recommended_action && <p>{issue.recommended_action}</p>}
      {issue.evidence && <details><summary>Supporting source</summary><p>{issue.evidence}</p></details>}
    </li>)}</ul>;
    return level === "advisory"
      ? <details className="reviewAdvisory" key={level}><summary>Advisory suggestions ({items.length})</summary>{list}</details>
      : <section className={level === "error" ? "requirementsError" : "requirementsWarnings"} key={level} aria-label={`${label} findings`}>{list}</section>;
  })}</>;
}

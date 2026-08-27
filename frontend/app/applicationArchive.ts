export type ArchivableApplication = { archived_at?: string | null };

export const activeApplications = <T extends ArchivableApplication>(applications: T[]) => applications.filter((application) => !application.archived_at);
export const archivedApplications = <T extends ArchivableApplication>(applications: T[]) => applications.filter((application) => Boolean(application.archived_at));

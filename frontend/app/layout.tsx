import "./styles.css";
import "./profile.css";
import "./workflow.css";
import "./applications.css";
export const metadata = { title: "Job Application Assistant", description: "Create job application documents grounded in your real experience." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}</body></html>; }

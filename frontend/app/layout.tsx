import "./styles.css";
import "./profile.css";
import "./workflow.css";
import "./applications.css";
export const metadata = { title: "求职助手", description: "真实经历驱动的求职材料助手" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="zh-CN"><body>{children}</body></html>; }

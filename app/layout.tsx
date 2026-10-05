import type { Metadata } from "next";
import { SessionProvider } from "@/app/components/SessionProvider";
import { ToastProvider } from "@/app/components/ToastProvider";
import "./globals.css";
import "./theme.css";
import "./reference-video-sections.css";
import "./public-detail.css";
import "./unified-ui.css";
import "./community.css";
import "./api-platform/platform.css";

export const metadata: Metadata = {
  title: "UESTC AI 社 | 赛事与作品档案",
  description: "电子科技大学 AI 社的信息发布与通用比赛平台。",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN"><body><ToastProvider><SessionProvider>{children}</SessionProvider></ToastProvider></body></html>
  );
}

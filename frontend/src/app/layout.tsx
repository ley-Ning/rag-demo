import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { JetBrains_Mono, Noto_Sans_SC, Noto_Serif_SC } from "next/font/google";

import AppProviders from "@/components/app-providers";

import "./globals.css";
import "@/styles/tokens.css";

// Apple-style font configuration
const notoSansSC = Noto_Sans_SC({
  variable: "--font-noto-sans-sc",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const notoSerifSC = Noto_Serif_SC({
  variable: "--font-noto-serif-sc",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "玄武智库 - 企业级 RAG 知识问答平台",
  description: "基于向量检索的企业知识问答系统，精准溯源，智能决策",
  keywords: ["RAG", "知识问答", "向量检索", "AI", "企业知识库"],
  authors: [{ name: "玄武智库" }],
  viewport: "width=device-width, initial-scale=1, maximum-scale=1",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <head>
        <meta name="theme-color" content="#ffffff" />
      </head>
      <body
        className={`${notoSansSC.variable} ${notoSerifSC.variable} ${jetBrainsMono.variable}`}
        style={{
          fontFamily: "var(--font-noto-sans-sc), -apple-system, BlinkMacSystemFont, sans-serif",
        }}
      >
        <AntdRegistry>
          <AppProviders>{children}</AppProviders>
        </AntdRegistry>
      </body>
    </html>
  );
}

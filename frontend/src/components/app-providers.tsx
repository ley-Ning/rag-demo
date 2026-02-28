"use client";

import { App, ConfigProvider, theme as antTheme } from "antd";

export default function AppProviders({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ConfigProvider
      theme={{
        algorithm: antTheme.defaultAlgorithm,
        token: {
          // Apple-inspired color palette
          colorPrimary: "#0071e3",
          colorInfo: "#0071e3",
          colorSuccess: "#30d158",
          colorWarning: "#ff9f0a",
          colorError: "#ff453a",

          // Border radius - Apple style
          borderRadius: 12,

          // Typography
          fontSize: 14,
          fontSizeHeading1: 32,
          fontSizeHeading2: 24,
          fontSizeHeading3: 20,
          fontSizeHeading4: 17,
          fontSizeHeading5: 15,

          // Font families
          fontFamily:
            "var(--font-noto-sans-sc), -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', sans-serif",
          fontFamilyCode: "var(--font-jetbrains-mono), 'SF Mono', monospace",

          // Spacing
          marginXS: 4,
          marginSM: 8,
          marginMD: 16,
          marginLG: 24,
          marginXL: 32,

          // Shadows
          boxShadow: "0 1px 3px rgba(0, 0, 0, 0.06), 0 4px 16px rgba(0, 0, 0, 0.08)",
          boxShadowSecondary: "0 2px 12px rgba(0, 0, 0, 0.08)",

          // Motion
          motionDurationFast: "0.15s",
          motionDurationMid: "0.2s",
          motionDurationSlow: "0.3s",
          motionEaseInOut: "cubic-bezier(0.4, 0, 0.2, 1)",
          motionEaseOut: "cubic-bezier(0, 0, 0.2, 1)",
        },
        components: {
          Layout: {
            bodyBg: "transparent",
            siderBg: "transparent",
            headerBg: "transparent",
          },
          Card: {
            colorBorderSecondary: "rgba(0, 0, 0, 0.06)",
            paddingLG: 24,
          },
          Menu: {
            itemBg: "transparent",
            itemColor: "rgba(245, 245, 247, 0.65)",
            itemSelectedBg: "rgba(0, 113, 227, 0.24)",
            itemSelectedColor: "#f5f5f7",
            itemHoverColor: "#f5f5f7",
            itemHoverBg: "rgba(255, 255, 255, 0.08)",
            itemBorderRadius: 12,
          },
          Button: {
            colorPrimaryHover: "#0077ed",
            primaryShadow: "0 1px 3px rgba(0, 113, 227, 0.3)",
          },
          Input: {
            paddingBlock: 10,
            paddingInline: 14,
          },
          Select: {
            optionSelectedBg: "rgba(0, 113, 227, 0.1)",
          },
          Table: {
            headerBg: "rgba(0, 0, 0, 0.02)",
            rowHoverBg: "rgba(0, 113, 227, 0.04)",
          },
          Tag: {
            defaultBg: "rgba(0, 0, 0, 0.04)",
          },
          Message: {
            contentBg: "#ffffff",
          },
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}

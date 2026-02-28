"use client";

import {
  AppstoreOutlined,
  CompassOutlined,
  FileSearchOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { Button, Drawer, Layout, Menu, Space, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState, useEffect } from "react";

const { Header, Sider, Content } = Layout;

const menuItems: MenuProps["items"] = [
  {
    key: "/chat",
    icon: <MessageOutlined />,
    label: "智能问答",
  },
  {
    key: "/documents",
    icon: <FileSearchOutlined />,
    label: "文档中心",
  },
  {
    key: "/models",
    icon: <AppstoreOutlined />,
    label: "模型管理",
  },
  {
    key: "/settings",
    icon: <SettingOutlined />,
    label: "系统设置",
  },
];

const pathTitleMap: Record<string, { title: string; subtitle: string }> = {
  "/chat": {
    title: "智能问答",
    subtitle: "AI 驱动的知识检索与问答",
  },
  "/documents": {
    title: "文档中心",
    subtitle: "上传、解析与管理知识库",
  },
  "/models": {
    title: "模型管理",
    subtitle: "模型池配置与状态监控",
  },
  "/settings": {
    title: "系统设置",
    subtitle: "环境参数与安全策略配置",
  },
};

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 992);
      if (window.innerWidth >= 992) {
        setMobileMenuOpen(false);
      }
    };

    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  const headerMeta = useMemo(() => {
    return (
      pathTitleMap[pathname] ?? {
        title: "玄武智库",
        subtitle: "企业级 RAG 知识问答平台",
      }
    );
  }, [pathname]);

  const handleMenuClick = (key: string) => {
    router.push(key);
    if (isMobile) {
      setMobileMenuOpen(false);
    }
  };

  const siderContent = (
    <>
      {/* Logo */}
      <div className="workspace-logo">
        <span className="workspace-logo-dot" />
        {(!collapsed || isMobile) && (
          <div className="workspace-logo-copy">
            <Typography.Text className="workspace-logo-title">
              玄武智库
            </Typography.Text>
            <Typography.Text className="workspace-logo-subtitle">
              RAG 知识中枢
            </Typography.Text>
          </div>
        )}
      </div>

      {/* Navigation Menu */}
      <Menu
        mode="inline"
        selectedKeys={[pathname]}
        items={menuItems}
        onClick={({ key }) => handleMenuClick(key)}
        className="workspace-menu"
      />

      {/* Status Footer */}
      {(!collapsed || isMobile) && (
        <div className="workspace-sider-footnote">
          <CompassOutlined />
          <span>系统运行中</span>
        </div>
      )}
    </>
  );

  return (
    <Layout className="workspace-layout">
      {/* Desktop Sidebar - Fixed */}
      {!isMobile && (
        <Sider
          width={240}
          collapsedWidth={72}
          collapsed={collapsed}
          className="workspace-sider"
          trigger={null}
        >
          <div className="workspace-sider-inner">{siderContent}</div>
        </Sider>
      )}

      {/* Mobile Drawer */}
      {isMobile && (
        <Drawer
          placement="left"
          open={mobileMenuOpen}
          onClose={() => setMobileMenuOpen(false)}
          width={280}
          closable={false}
          styles={{
            body: { padding: 0 },
            header: { display: "none" },
          }}
          className="workspace-drawer"
        >
          <div
            className="workspace-sider-inner"
            style={{
              background: "linear-gradient(180deg, rgba(29, 29, 31, 0.96) 0%, rgba(29, 29, 31, 0.98) 100%)",
              height: "100%",
            }}
          >
            {siderContent}
          </div>
        </Drawer>
      )}

      <Layout className="workspace-main">
        {/* Header - Fixed */}
        <Header className="workspace-header">
          <div className="workspace-header-left">
            <Button
              type="text"
              className="workspace-collapse-btn"
              icon={isMobile ? (mobileMenuOpen ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />) : (collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />)}
              onClick={() => {
                if (isMobile) {
                  setMobileMenuOpen(!mobileMenuOpen);
                } else {
                  setCollapsed(!collapsed);
                }
              }}
            />
            <div className="workspace-header-copy">
              <Typography.Title level={4} className="workspace-header-title">
                {headerMeta.title}
              </Typography.Title>
              <Typography.Text className="workspace-header-subtitle">
                {headerMeta.subtitle}
              </Typography.Text>
            </div>
          </div>

          <Space size={8}>
            <Tag className="workspace-status-tag">
              v1.0.0
            </Tag>
            <Tag className="workspace-status-tag workspace-status-tag--alt">
              开发环境
            </Tag>
          </Space>
        </Header>

        {/* Content - Scrollable */}
        <Content className="workspace-content">
          <div className="workspace-content-inner">{children}</div>
        </Content>
      </Layout>
    </Layout>
  );
}

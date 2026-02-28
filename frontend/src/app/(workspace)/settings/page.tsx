"use client";

import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Typography,
  message,
  Divider,
} from "antd";
import { useState } from "react";

type RuntimeValues = {
  envName: string;
  traceSampling: number;
  enableDebugLog: boolean;
  maxConcurrentTasks: number;
};

type SafetyValues = {
  uploadWhitelist: string;
  blockPromptInjection: boolean;
  answerStyle: "balanced" | "strict" | "concise";
  citationRequired: boolean;
};

const runtimeDefaults: RuntimeValues = {
  envName: "development",
  traceSampling: 100,
  enableDebugLog: true,
  maxConcurrentTasks: 8,
};

const safetyDefaults: SafetyValues = {
  uploadWhitelist: "pdf,doc,docx,txt,md",
  blockPromptInjection: true,
  answerStyle: "balanced",
  citationRequired: true,
};

export default function SettingsPage() {
  const [runtimeForm] = Form.useForm<RuntimeValues>();
  const [safetyForm] = Form.useForm<SafetyValues>();
  const [saving, setSaving] = useState(false);
  const [apiMessage, contextHolder] = message.useMessage();

  const saveSettings = async () => {
    setSaving(true);
    try {
      const runtimeValues = await runtimeForm.validateFields();
      const safetyValues = await safetyForm.validateFields();
      console.info("Runtime settings:", runtimeValues);
      console.info("Safety settings:", safetyValues);
      apiMessage.success("设置已保存");
    } catch {
      apiMessage.warning("请检查配置项是否正确");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-view page-stack">
      {contextHolder}

      {/* Hero Section */}
      <Card className="hero-card hero-card--settings">
        <div className="hero-card__grid">
          <div>
            <Typography.Text className="hero-card__eyebrow">
              系统设置
            </Typography.Text>
            <Typography.Title level={3} className="hero-card__title">
              精细配置，安全可控
            </Typography.Title>
            <Typography.Paragraph className="hero-card__desc">
              集中管理系统运行参数与安全策略，确保平台稳定、合规运行。
            </Typography.Paragraph>
          </div>
          <div className="hero-card__stats">
            <div className="hero-stat">
              <span className="hero-stat__label">配置模块</span>
              <span className="hero-stat__value">2</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat__label">配置项</span>
              <span className="hero-stat__value">8</span>
            </div>
          </div>
        </div>
      </Card>

      {/* Settings Forms Grid */}
      <div className="settings-grid">
        {/* Runtime Settings */}
        <Card className="panel-card">
          <Typography.Title level={5} className="panel-title">
            运行环境
          </Typography.Title>
          <Typography.Text className="panel-subtitle">
            链路追踪、并发控制与日志配置
          </Typography.Text>

          <Divider style={{ margin: "16px 0" }} />

          <Form
            form={runtimeForm}
            layout="vertical"
            initialValues={runtimeDefaults}
            className="settings-form"
          >
            <Form.Item
              label="环境标识"
              name="envName"
              rules={[{ required: true, message: "请输入环境标识" }]}
            >
              <Input placeholder="development / staging / production" />
            </Form.Item>

            <Form.Item
              label="链路追踪采样率 (%)"
              name="traceSampling"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={100} style={{ width: "100%" }} />
            </Form.Item>

            <Form.Item
              label="最大并发任务数"
              name="maxConcurrentTasks"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={64} style={{ width: "100%" }} />
            </Form.Item>

            <Form.Item
              label="启用调试日志"
              name="enableDebugLog"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </Form>
        </Card>

        {/* Safety Settings */}
        <Card className="panel-card">
          <Typography.Title level={5} className="panel-title">
            安全策略
          </Typography.Title>
          <Typography.Text className="panel-subtitle">
            上传限制、回答策略与引用控制
          </Typography.Text>

          <Divider style={{ margin: "16px 0" }} />

          <Form
            form={safetyForm}
            layout="vertical"
            initialValues={safetyDefaults}
            className="settings-form"
          >
            <Form.Item
              label="上传文件白名单"
              name="uploadWhitelist"
              rules={[{ required: true, message: "请输入文件白名单" }]}
            >
              <Input placeholder="pdf,doc,docx,txt,md" />
            </Form.Item>

            <Form.Item
              label="回答策略"
              name="answerStyle"
              rules={[{ required: true }]}
            >
              <Select
                options={[
                  { value: "balanced", label: "均衡模式 - 平衡详细度与简洁度" },
                  { value: "strict", label: "严谨模式 - 基于证据严格回答" },
                  { value: "concise", label: "简洁模式 - 精简高效输出" },
                ]}
              />
            </Form.Item>

            <Form.Item
              label="强制引用来源"
              name="citationRequired"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>

            <Form.Item
              label="拦截提示词注入"
              name="blockPromptInjection"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </Form>
        </Card>
      </div>

      {/* Action Panel */}
      <Card className="panel-card">
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="配置说明"
            description="当前配置仅在前端保存，后续可对接后端 API 实现持久化存储。"
          />

          <div className="settings-actions">
            <Button
              type="primary"
              loading={saving}
              onClick={() => void saveSettings()}
              size="large"
            >
              保存配置
            </Button>
            <Button
              size="large"
              onClick={() => {
                runtimeForm.setFieldsValue(runtimeDefaults);
                safetyForm.setFieldsValue(safetyDefaults);
              }}
            >
              恢复默认
            </Button>
          </div>
        </Space>
      </Card>
    </div>
  );
}

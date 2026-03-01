"use client";

import {
  AppstoreOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createModel,
  deleteModel,
  fetchModelById,
  fetchModels,
  testModel,
  TestModelResult,
  updateModel,
  updateModelStatus,
} from "@/lib/rag-api";
import { ModelCapability, ModelItem, ModelStatus } from "@/types/rag";

interface ModelFormValues {
  id: string;
  name: string;
  provider: string;
  capabilities: ModelCapability[];
  status: ModelStatus;
  maxTokens: number;
  baseUrl: string;
  apiKey: string;
}

const capabilityStyleMap: Record<ModelCapability, { bg: string; color: string }> = {
  chat: { bg: "rgba(0, 113, 227, 0.1)", color: "#0071e3" },
  embedding: { bg: "rgba(191, 90, 242, 0.1)", color: "#bf5af2" },
  rerank: { bg: "rgba(255, 159, 10, 0.1)", color: "#ff9f0a" },
};

const capabilityOptions: Array<{ label: string; value: ModelCapability }> = [
  { label: "chat", value: "chat" },
  { label: "embedding", value: "embedding" },
  { label: "rerank", value: "rerank" },
];

const statusOptions: Array<{ label: string; value: ModelStatus }> = [
  { label: "在线", value: "online" },
  { label: "离线", value: "offline" },
];

const createDefaultFormValues = (): ModelFormValues => ({
  id: "",
  name: "",
  provider: "",
  capabilities: ["chat"],
  status: "online",
  maxTokens: 8192,
  baseUrl: "",
  apiKey: "",
});

export default function ModelsPage() {
  const [form] = Form.useForm<ModelFormValues>();
  const [models, setModels] = useState<ModelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "online" | "offline">(
    "all",
  );
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingModel, setEditingModel] = useState<ModelItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [loadingModelDetail, setLoadingModelDetail] = useState(false);
  const [switchingModelId, setSwitchingModelId] = useState<string>();
  const [deletingModelId, setDeletingModelId] = useState<string>();
  const [testingModelId, setTestingModelId] = useState<string>();
  const [apiMessage, contextHolder] = message.useMessage();

  const loadModels = useCallback(
    async (showLoading = false) => {
      if (showLoading) {
        setLoading(true);
      }
      try {
        const data = await fetchModels();
        setModels(data);
      } catch (error) {
        apiMessage.error((error as Error).message || "模型列表加载失败");
      } finally {
        if (showLoading) {
          setLoading(false);
        }
      }
    },
    [apiMessage],
  );

  useEffect(() => {
    void loadModels(true);
  }, [loadModels]);

  const openCreateModal = () => {
    setEditingModel(null);
    setEditorOpen(true);
  };

  const openEditModal = async (model: ModelItem) => {
    setLoadingModelDetail(true);
    setEditingModel(model);
    setEditorOpen(true);
    try {
      const detail = await fetchModelById(model.id);
      setEditingModel(detail);
    } catch (error) {
      apiMessage.warning((error as Error).message || "模型详情加载失败，已使用列表数据");
    } finally {
      setLoadingModelDetail(false);
    }
  };

  useEffect(() => {
    if (!editorOpen) {
      return;
    }

    if (!editingModel) {
      form.setFieldsValue(createDefaultFormValues());
      return;
    }

    form.setFieldsValue({
      id: editingModel.id,
      name: editingModel.name,
      provider: editingModel.provider,
      capabilities: editingModel.capabilities,
      status: editingModel.status,
      maxTokens: editingModel.maxTokens,
      baseUrl: editingModel.baseUrl,
      apiKey: editingModel.apiKey,
    });
  }, [editorOpen, editingModel, form]);

  const closeEditorModal = () => {
    setEditorOpen(false);
    setEditingModel(null);
    form.resetFields();
  };

  const submitEditorModal = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      if (editingModel) {
        const updated = await updateModel(editingModel.id, {
          name: values.name,
          provider: values.provider,
          capabilities: values.capabilities,
          status: values.status,
          maxTokens: values.maxTokens,
          baseUrl: values.baseUrl,
          apiKey: values.apiKey,
        });
        setModels((prev) =>
          prev.map((item) => (item.id === editingModel.id ? updated : item)),
        );
        apiMessage.success("模型配置已更新");
      } else {
        const created = await createModel(values);
        setModels((prev) =>
          [...prev, created].sort((a, b) =>
            `${a.provider}-${a.name}`.localeCompare(`${b.provider}-${b.name}`),
          ),
        );
        apiMessage.success("模型已创建");
      }
      closeEditorModal();
    } catch (error) {
      if (error instanceof Error && error.message) {
        apiMessage.error(error.message);
      }
    } finally {
      setSaving(false);
    }
  };

  const toggleModelStatus = async (model: ModelItem, checked: boolean) => {
    const status: ModelStatus = checked ? "online" : "offline";
    setSwitchingModelId(model.id);
    try {
      const updated = await updateModelStatus(model.id, status);
      setModels((prev) => prev.map((item) => (item.id === model.id ? updated : item)));
      apiMessage.success(`${model.name} 已切换为${checked ? "在线" : "离线"}`);
    } catch (error) {
      apiMessage.error((error as Error).message || "状态更新失败");
    } finally {
      setSwitchingModelId(undefined);
    }
  };

  const removeModel = async (model: ModelItem) => {
    setDeletingModelId(model.id);
    try {
      await deleteModel(model.id);
      setModels((prev) => prev.filter((item) => item.id !== model.id));
      apiMessage.success("模型已删除");
    } catch (error) {
      apiMessage.error((error as Error).message || "模型删除失败");
    } finally {
      setDeletingModelId(undefined);
    }
  };

  const handleTestModel = async (model: ModelItem) => {
    setTestingModelId(model.id);
    try {
      const result: TestModelResult = await testModel(model.id);
      if (result.success) {
        apiMessage.success(
          `${model.name} 测试成功 (${result.latency_ms}ms)`,
        );
      } else {
        apiMessage.error({
          content: `${model.name} 测试失败: ${result.message}`,
          duration: 5,
        });
      }
    } catch (error) {
      apiMessage.error((error as Error).message || "模型测试失败");
    } finally {
      setTestingModelId(undefined);
    }
  };

  const filteredModels = useMemo(() => {
    return models.filter((item) => {
      if (statusFilter !== "all" && item.status !== statusFilter) {
        return false;
      }
      if (!keyword.trim()) {
        return true;
      }
      const normalized = keyword.trim().toLowerCase();
      return (
        item.name.toLowerCase().includes(normalized) ||
        item.id.toLowerCase().includes(normalized) ||
        item.provider.toLowerCase().includes(normalized)
      );
    });
  }, [keyword, models, statusFilter]);

  const onlineChatModels = useMemo(
    () =>
      models.filter(
        (item) =>
          item.status === "online" && item.capabilities.includes("chat"),
      ),
    [models],
  );

  const modelColumns = useMemo<ColumnsType<ModelItem>>(
    () => [
      {
        title: "模型名称",
        key: "name",
        width: 260,
        render: (_, record) => (
          <div className="model-name-cell">
            <Typography.Text strong style={{ fontSize: 15 }}>
              {record.name}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {record.id}
            </Typography.Text>
          </div>
        ),
      },
      {
        title: "提供商",
        dataIndex: "provider",
        key: "provider",
        width: 120,
        render: (provider: string) => (
          <Tag style={{ borderRadius: 6, background: "rgba(0,0,0,0.04)", border: "none" }}>
            {provider}
          </Tag>
        ),
      },
      {
        title: "Base URL",
        dataIndex: "baseUrl",
        key: "baseUrl",
        width: 240,
        render: (baseUrl: string) =>
          baseUrl ? (
            <Typography.Text style={{ fontSize: 12 }}>{baseUrl}</Typography.Text>
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              未配置
            </Typography.Text>
          ),
      },
      {
        title: "密钥",
        dataIndex: "apiKey",
        key: "apiKey",
        width: 90,
        render: (apiKey: string) => (
          <Tag
            style={{
              borderRadius: 6,
              background: apiKey ? "rgba(48, 209, 88, 0.1)" : "rgba(134, 134, 139, 0.1)",
              color: apiKey ? "#30d158" : "#86868b",
              border: "none",
            }}
          >
            {apiKey ? "已配置" : "未配置"}
          </Tag>
        ),
      },
      {
        title: "能力标签",
        dataIndex: "capabilities",
        key: "capabilities",
        width: 200,
        render: (caps: ModelCapability[]) => (
          <Space size={4} wrap>
            {caps.map((cap) => {
              const style = capabilityStyleMap[cap];
              return (
                <Tag
                  key={cap}
                  style={{
                    borderRadius: 6,
                    background: style.bg,
                    color: style.color,
                    border: "none",
                    fontWeight: 500,
                  }}
                >
                  {cap}
                </Tag>
              );
            })}
          </Space>
        ),
      },
      {
        title: "最大 Token",
        dataIndex: "maxTokens",
        key: "maxTokens",
        width: 130,
        render: (tokens: number) => (
          <Typography.Text style={{ fontFamily: "var(--font-jetbrains-mono), monospace" }}>
            {tokens.toLocaleString()}
          </Typography.Text>
        ),
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        width: 110,
        render: (status: ModelStatus) =>
          status === "online" ? (
            <Tag
              icon={<CheckCircleOutlined />}
              style={{
                borderRadius: 6,
                background: "rgba(48, 209, 88, 0.1)",
                color: "#30d158",
                border: "none",
                fontWeight: 500,
              }}
            >
              在线
            </Tag>
          ) : (
            <Tag
              icon={<ClockCircleOutlined />}
              style={{
                borderRadius: 6,
                background: "rgba(134, 134, 139, 0.1)",
                color: "#86868b",
                border: "none",
              }}
            >
              离线
            </Tag>
          ),
      },
      {
        title: "操作",
        key: "actions",
        width: 280,
        render: (_, record) => (
          <Space size={8} wrap className="model-actions">
            <Switch
              checked={record.status === "online"}
              checkedChildren="在线"
              unCheckedChildren="离线"
              size="small"
              loading={switchingModelId === record.id}
              onChange={(checked) => void toggleModelStatus(record, checked)}
            />
            <Button
              type="text"
              size="small"
              icon={<ThunderboltOutlined />}
              loading={testingModelId === record.id}
              onClick={() => void handleTestModel(record)}
            >
              测试
            </Button>
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEditModal(record)}
            >
              编辑
            </Button>
            <Popconfirm
              title="确认删除此模型？"
              description="删除后聊天页和编排任务都无法继续使用该模型。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{
                danger: true,
                loading: deletingModelId === record.id,
              }}
              onConfirm={() => void removeModel(record)}
            >
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deletingModelId === record.id}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    [deletingModelId, switchingModelId, testingModelId],
  );

  return (
    <div className="models-view page-stack">
      {contextHolder}

      <div className="metric-row">
        <Card className="metric-card">
          <Statistic
            title="在线模型"
            value={models.filter((item) => item.status === "online").length}
            prefix={<CheckCircleOutlined style={{ color: "#30d158" }} />}
          />
        </Card>
        <Card className="metric-card">
          <Statistic
            title="离线模型"
            value={models.filter((item) => item.status === "offline").length}
            prefix={<ClockCircleOutlined style={{ color: "#86868b" }} />}
          />
        </Card>
        <Card className="metric-card">
          <Statistic
            title="对话能力"
            value={models.filter((item) => item.capabilities.includes("chat")).length}
            prefix={<AppstoreOutlined style={{ color: "#0071e3" }} />}
          />
        </Card>
      </div>

      <Card className="panel-card">
        <div className="model-toolbar">
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索模型名称、ID 或提供商..."
            prefix={<SearchOutlined style={{ color: "var(--rag-text-muted)" }} />}
            allowClear
            size="large"
          />
          <Space className="model-toolbar__actions" size={12} wrap>
            <Segmented
              value={statusFilter}
              onChange={(value) =>
                setStatusFilter(value as "all" | "online" | "offline")
              }
              options={[
                { label: "全部", value: "all" },
                { label: "在线", value: "online" },
                { label: "离线", value: "offline" },
              ]}
              size="large"
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
              新增模型
            </Button>
          </Space>
        </div>

        <Table<ModelItem>
          rowKey="id"
          columns={modelColumns}
          dataSource={filteredModels}
          loading={loading}
          pagination={{
            pageSize: 10,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 个模型`,
          }}
          scroll={{ x: 1430 }}
          className="models-table"
          rowClassName={(record) =>
            record.status === "online" ? "models-table__row--online" : ""
          }
        />
      </Card>

      <Modal
        open={editorOpen}
        title={editingModel ? "编辑模型" : "新增模型"}
        okText={editingModel ? "保存修改" : "创建模型"}
        cancelText="取消"
        confirmLoading={saving}
        onOk={() => void submitEditorModal()}
        onCancel={closeEditorModal}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          disabled={loadingModelDetail}
          preserve={false}
        >
          <Form.Item
            label="模型 ID"
            name="id"
            rules={[
              { required: true, message: "请输入模型 ID" },
              {
                pattern: /^[a-zA-Z0-9._:-]{2,64}$/,
                message: "仅支持字母、数字、.-_:，长度 2-64",
              },
            ]}
          >
            <Input
              placeholder="例如：qwen2.5-72b-instruct"
              disabled={Boolean(editingModel)}
            />
          </Form.Item>

          <Form.Item
            label="模型名称"
            name="name"
            rules={[{ required: true, message: "请输入模型名称" }]}
          >
            <Input placeholder="例如：Qwen 2.5 72B Instruct" />
          </Form.Item>

          <Form.Item
            label="提供商"
            name="provider"
            rules={[{ required: true, message: "请输入模型提供商" }]}
          >
            <Input placeholder="例如：openai / qwen / volcengine" />
          </Form.Item>

          <Form.Item
            label="能力标签"
            name="capabilities"
            rules={[{ required: true, message: "请至少选择一个能力标签" }]}
          >
            <Select
              mode="multiple"
              options={capabilityOptions}
              placeholder="请选择能力标签"
              maxTagCount="responsive"
            />
          </Form.Item>

          <Form.Item
            label="Base URL"
            name="baseUrl"
            rules={[
              { max: 260, message: "Base URL 不能超过 260 个字符" },
              {
                validator: (_, value) => {
                  if (!value) {
                    return Promise.resolve();
                  }
                  try {
                    const parsed = new URL(value);
                    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
                      return Promise.reject(new Error("Base URL 仅支持 http/https"));
                    }
                    return Promise.resolve();
                  } catch {
                    return Promise.reject(new Error("Base URL 格式不正确"));
                  }
                },
              },
            ]}
          >
            <Input placeholder="例如：https://api.openai.com/v1" />
          </Form.Item>

          <Form.Item
            label="API Key"
            name="apiKey"
            rules={[{ max: 260, message: "API Key 不能超过 260 个字符" }]}
          >
            <Input.Password placeholder="例如：sk-xxxx" visibilityToggle />
          </Form.Item>

          <Form.Item
            label="最大 Token"
            name="maxTokens"
            rules={[{ required: true, message: "请输入最大 Token" }]}
          >
            <InputNumber min={256} max={10000000} style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item
            label="状态"
            name="status"
            rules={[{ required: true, message: "请选择状态" }]}
          >
            <Select options={statusOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

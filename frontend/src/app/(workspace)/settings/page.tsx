"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Form,
  Input,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";

import {
  createMcpServer,
  fetchMcpServers,
  fetchMcpTools,
  syncMcpServerTools,
  updateMcpServer,
  updateMcpToolStatus,
} from "@/lib/rag-api";
import { McpServerItem, McpToolItem } from "@/types/rag";

const { Title, Paragraph, Text } = Typography;

export default function SettingsPage() {
  const [servers, setServers] = useState<McpServerItem[]>([]);
  const [tools, setTools] = useState<McpToolItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [syncingServerKey, setSyncingServerKey] = useState<string | null>(null);
  const [apiMessage, contextHolder] = message.useMessage();
  const [form] = Form.useForm();

  const loadAll = async () => {
    setLoading(true);
    try {
      const [serverItems, toolItems] = await Promise.all([fetchMcpServers(), fetchMcpTools()]);
      setServers(serverItems);
      setTools(toolItems);
    } catch (error) {
      apiMessage.error((error as Error).message || "加载 MCP 配置失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  const handleCreateServer = async (values: {
    serverKey: string;
    name: string;
    endpoint: string;
    timeoutMs?: number;
  }) => {
    setCreating(true);
    try {
      await createMcpServer({
        serverKey: values.serverKey.trim(),
        name: values.name.trim(),
        endpoint: values.endpoint.trim(),
        timeoutMs: values.timeoutMs || 12000,
      });
      apiMessage.success("MCP Server 已创建");
      form.resetFields();
      await loadAll();
    } catch (error) {
      apiMessage.error((error as Error).message || "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const toggleServer = async (record: McpServerItem, enabled: boolean) => {
    try {
      await updateMcpServer(record.serverKey, { enabled });
      setServers((prev) =>
        prev.map((item) => (item.serverKey === record.serverKey ? { ...item, enabled } : item)),
      );
      apiMessage.success(`Server 已${enabled ? "启用" : "停用"}`);
    } catch (error) {
      apiMessage.error((error as Error).message || "更新失败");
    }
  };

  const toggleTool = async (record: McpToolItem, enabled: boolean) => {
    try {
      await updateMcpToolStatus(record.toolName, enabled);
      setTools((prev) =>
        prev.map((item) => (item.toolName === record.toolName ? { ...item, enabled } : item)),
      );
      apiMessage.success(`Tool 已${enabled ? "启用" : "停用"}`);
    } catch (error) {
      apiMessage.error((error as Error).message || "更新失败");
    }
  };

  const handleSyncTools = async (record: McpServerItem) => {
    setSyncingServerKey(record.serverKey);
    try {
      const result = await syncMcpServerTools(record.serverKey);
      await loadAll();
      apiMessage.success(`同步完成：${result.syncedCount} 个工具`);
    } catch (error) {
      apiMessage.error((error as Error).message || "同步失败");
    } finally {
      setSyncingServerKey(null);
    }
  };

  const serverColumns: ColumnsType<McpServerItem> = [
    {
      title: "Server Key",
      dataIndex: "serverKey",
      key: "serverKey",
      width: 180,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      width: 160,
    },
    {
      title: "Endpoint",
      dataIndex: "endpoint",
      key: "endpoint",
      render: (value: string) => (
        <Typography.Text ellipsis={{ tooltip: value }} style={{ maxWidth: 320 }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "超时",
      dataIndex: "timeoutMs",
      key: "timeoutMs",
      width: 100,
      render: (value: number) => `${value}ms`,
    },
    {
      title: "状态",
      dataIndex: "enabled",
      key: "enabled",
      width: 110,
      render: (_: boolean, record) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) => {
            void toggleServer(record, checked);
          }}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 140,
      render: (_: unknown, record) => (
        <Button
          size="small"
          disabled={!record.enabled}
          loading={syncingServerKey === record.serverKey}
          onClick={() => {
            void handleSyncTools(record);
          }}
        >
          同步工具
        </Button>
      ),
    },
  ];

  const toolColumns: ColumnsType<McpToolItem> = [
    {
      title: "Tool",
      dataIndex: "toolName",
      key: "toolName",
      width: 220,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: "显示名",
      dataIndex: "displayName",
      key: "displayName",
      width: 140,
    },
    {
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (value: string) => (
        <Tag color={value === "builtin" ? "blue" : "purple"}>{value}</Tag>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      render: (value: string) => (
        <Typography.Text ellipsis={{ tooltip: value }} style={{ maxWidth: 300 }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (_: boolean, record) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) => {
            void toggleTool(record, checked);
          }}
        />
      ),
    },
  ];

  return (
    <div className="page-stack settings-view">
      {contextHolder}

      <Card className="panel-card">
        <Title level={5} className="panel-title">环境信息</Title>
        <Divider style={{ margin: "12px 0 16px" }} />
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="运行环境">
            <Tag color="blue">开发环境</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="系统版本">
            <Tag>v1.1.0</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="后端服务">Python FastAPI</Descriptions.Item>
          <Descriptions.Item label="向量数据库">PostgreSQL + pgvector</Descriptions.Item>
          <Descriptions.Item label="插件架构">MCP 双轨（builtin + external）</Descriptions.Item>
          <Descriptions.Item label="调用策略">模型自动调用</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card className="panel-card">
        <Title level={5} className="panel-title">MCP Server 管理</Title>
        <Divider style={{ margin: "12px 0 16px" }} />
        <Alert
          type="info"
          showIcon
          message="说明"
          description="内置工具默认可用。这里主要管理外部 MCP Server，启用后可被聊天自动调用。"
          style={{ marginBottom: 16 }}
        />
        <Form
          form={form}
          layout="inline"
          onFinish={(values) => {
            void handleCreateServer(values);
          }}
          style={{ marginBottom: 16, rowGap: 8 }}
        >
          <Form.Item
            name="serverKey"
            rules={[{ required: true, message: "请输入 serverKey" }]}
          >
            <Input placeholder="server-key" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item
            name="name"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input placeholder="名称" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item
            name="endpoint"
            rules={[{ required: true, message: "请输入 endpoint" }]}
          >
            <Input placeholder="https://mcp.example.com/invoke" style={{ width: 320 }} />
          </Form.Item>
          <Form.Item name="timeoutMs" initialValue={12000}>
            <Input type="number" placeholder="超时ms" style={{ width: 120 }} />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" type="primary" loading={creating}>
              新增 Server
            </Button>
          </Form.Item>
        </Form>
        <Table
          rowKey="serverKey"
          columns={serverColumns}
          dataSource={servers}
          loading={loading}
          pagination={false}
          size="small"
        />
      </Card>

      <Card className="panel-card">
        <Title level={5} className="panel-title">MCP Tool 开关</Title>
        <Divider style={{ margin: "12px 0 16px" }} />
        <Table
          rowKey="toolName"
          columns={toolColumns}
          dataSource={tools}
          loading={loading}
          pagination={false}
          size="small"
        />
      </Card>

      <Card className="panel-card">
        <Title level={5} className="panel-title">配置说明</Title>
        <Divider style={{ margin: "12px 0 16px" }} />
        <Paragraph type="secondary">
          MCP 默认自动调用。若需调整策略，可在后端环境变量中设置
          <Text code> MCP_AUTO_CALL </Text>、
          <Text code> DEEP_THINK_ENABLED </Text>。
        </Paragraph>
      </Card>
    </div>
  );
}

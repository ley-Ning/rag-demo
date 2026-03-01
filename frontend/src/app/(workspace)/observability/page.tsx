"use client";

import { useEffect, useState } from "react";
import {
  Card,
  Table,
  Tag,
  Typography,
  Space,
  Input,
  Select,
  Button,
  Descriptions,
  Modal,
  Divider,
  Empty,
  Spin,
  message,
} from "antd";
import {
  SearchOutlined,
  ReloadOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ApiOutlined,
  RobotOutlined,
  CloudOutlined,
  DatabaseOutlined,
} from "@ant-design/icons";
import { Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";

import { fetchConsumptionLogsByQuery } from "@/lib/rag-api";
import { ConsumptionLogItem, ConsumptionLogsQuery } from "@/types/rag";

const { Title, Text, Paragraph } = Typography;

// 从 skillCalls 中提取 Embedding token 消耗
function extractEmbeddingTokens(skillCalls: { skillName: string; totalTokens: number }[]): number {
  const embeddingCall = skillCalls.find((s) => s.skillName.includes("embedding"));
  return embeddingCall?.totalTokens || 0;
}

// 从 skillCalls 中提取 LLM token 消耗
function extractLlmTokens(skillCalls: { skillName: string; totalTokens: number }[]): number {
  const llmCall = skillCalls.find((s) => s.skillName.includes("llm"));
  return llmCall?.totalTokens || 0;
}

export default function ObservabilityPage() {
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<ConsumptionLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState<ConsumptionLogsQuery>({
    limit: 50,
    status: "all",
    modelId: "",
    keyword: "",
  });
  const [selectedLog, setSelectedLog] = useState<ConsumptionLogItem | null>(null);
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const [apiMessage, contextHolder] = message.useMessage();

  const loadLogs = async () => {
    setLoading(true);
    try {
      const result = await fetchConsumptionLogsByQuery(query);
      setLogs(result.items);
      setTotal(result.total);
    } catch (error) {
      apiMessage.error((error as Error).message || "加载日志失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadLogs();
  }, [query.status]);

  const handleSearch = () => {
    void loadLogs();
  };

  const handleReset = () => {
    setQuery({
      limit: 50,
      status: "all",
      modelId: "",
      keyword: "",
    });
  };

  const showDetail = (log: ConsumptionLogItem) => {
    setSelectedLog(log);
    setDetailModalOpen(true);
  };

  const columns: ColumnsType<ConsumptionLogItem> = [
    {
      title: "时间",
      dataIndex: "createdAt",
      key: "createdAt",
      width: 160,
      render: (val: string) => new Date(val).toLocaleString("zh-CN"),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 80,
      render: (status: string) =>
        status === "success" ? (
          <Tag icon={<CheckCircleOutlined />} color="success">成功</Tag>
        ) : (
          <Tag icon={<CloseCircleOutlined />} color="error">失败</Tag>
        ),
    },
    {
      title: "问题",
      dataIndex: "question",
      key: "question",
      ellipsis: true,
      render: (text: string) => (
        <Text ellipsis={{ tooltip: text }} style={{ maxWidth: 300 }}>
          {text}
        </Text>
      ),
    },
    {
      title: "模型",
      dataIndex: "modelId",
      key: "modelId",
      width: 140,
      render: (text: string) => <Tag>{text || "-"}</Tag>,
    },
    {
      title: "延迟",
      dataIndex: "latencyMs",
      key: "latencyMs",
      width: 100,
      render: (ms: number) => (
        <Space>
          <ClockCircleOutlined />
          <span>{ms}ms</span>
        </Space>
      ),
    },
    {
      title: "Token 消耗",
      key: "tokens",
      width: 180,
      render: (_: unknown, record: ConsumptionLogItem) => {
        const embeddingTokens = extractEmbeddingTokens(record.skillCalls || []);
        const llmTokens = extractLlmTokens(record.skillCalls || []);
        const hasDetail = embeddingTokens > 0 || llmTokens > 0;

        return (
          <Tooltip
            title={
              hasDetail ? (
                <div style={{ fontSize: 12 }}>
                  {embeddingTokens > 0 && (
                    <div>Embedding: {embeddingTokens.toLocaleString()}</div>
                  )}
                  {llmTokens > 0 && (
                    <div>LLM: {llmTokens.toLocaleString()}</div>
                  )}
                  <div style={{ marginTop: 4, fontWeight: "bold" }}>
                    总计: {record.totalTokens.toLocaleString()}
                  </div>
                </div>
              ) : (
                "总计: " + record.totalTokens.toLocaleString()
              )
            }
          >
            <Space size={4}>
              <Text strong>{record.totalTokens.toLocaleString()}</Text>
              {hasDetail && (
                <Tag
                  color="blue"
                  style={{ fontSize: 10, padding: "0 4px", margin: 0 }}
                >
                  E:{embeddingTokens} / L:{llmTokens}
                </Tag>
              )}
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: "操作",
      key: "action",
      width: 80,
      render: (_: unknown, record: ConsumptionLogItem) => (
        <Button type="link" size="small" onClick={() => showDetail(record)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div className="page-stack">
      {contextHolder}

      {/* Filters */}
      <Card className="panel-card">
        <Space wrap size="middle">
          <Input
            placeholder="搜索问题关键词"
            prefix={<SearchOutlined />}
            value={query.keyword}
            onChange={(e) => setQuery({ ...query, keyword: e.target.value })}
            onPressEnter={handleSearch}
            style={{ width: 240 }}
            allowClear
          />
          <Select
            value={query.status}
            onChange={(val) => setQuery({ ...query, status: val })}
            style={{ width: 120 }}
            options={[
              { value: "all", label: "全部状态" },
              { value: "success", label: "成功" },
              { value: "failed", label: "失败" },
            ]}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
            搜索
          </Button>
          <Button icon={<ReloadOutlined />} onClick={handleReset}>
            重置
          </Button>
        </Space>
      </Card>

      {/* Logs Table */}
      <Card className="panel-card">
        <Spin spinning={loading}>
          {logs.length === 0 && !loading ? (
            <Empty description="暂无日志记录" />
          ) : (
            <Table
              columns={columns}
              dataSource={logs}
              rowKey="id"
              pagination={{
                pageSize: 50,
                total,
                showTotal: (t) => `共 ${t} 条`,
              }}
              size="small"
              className="logs-table"
            />
          )}
        </Spin>
      </Card>

      {/* Detail Modal */}
      <Modal
        title="日志详情"
        open={detailModalOpen}
        onCancel={() => setDetailModalOpen(false)}
        footer={null}
        width={720}
      >
        {selectedLog && (
          <div>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="Trace ID">
                <Text code copyable>{selectedLog.traceId}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Session ID">
                <Text code>{selectedLog.sessionId || "-"}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="模型">
                <Tag>{selectedLog.modelId || "-"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {selectedLog.status === "success" ? (
                  <Tag color="success">成功</Tag>
                ) : (
                  <Tag color="error">失败</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="延迟">
                {selectedLog.latencyMs}ms
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {new Date(selectedLog.createdAt).toLocaleString("zh-CN")}
              </Descriptions.Item>
              <Descriptions.Item label="Prompt Tokens">
                {selectedLog.promptTokens.toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="Completion Tokens">
                {selectedLog.completionTokens.toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="Total Tokens" span={2}>
                <Text strong>{selectedLog.totalTokens.toLocaleString()}</Text>
              </Descriptions.Item>
            </Descriptions>

            {/* Token 明细 */}
            {selectedLog.skillCalls && selectedLog.skillCalls.length > 0 && (
              <>
                <Divider titlePlacement="left">Token 消耗明细</Divider>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  {selectedLog.skillCalls.map((skill, idx) => (
                    <Card
                      key={idx}
                      size="small"
                      style={{ minWidth: 180 }}
                      className={
                        skill.skillName.includes("embedding")
                          ? "token-card token-card--embedding"
                          : skill.skillName.includes("llm")
                            ? "token-card token-card--llm"
                            : "token-card"
                      }
                    >
                      <Space orientation="vertical" size={4}>
                        <Space>
                          {skill.skillName.includes("embedding") ? (
                            <CloudOutlined style={{ color: "#bf5af2" }} />
                          ) : skill.skillName.includes("llm") ? (
                            <RobotOutlined style={{ color: "#0071e3" }} />
                          ) : (
                            <DatabaseOutlined style={{ color: "#ff9f0a" }} />
                          )}
                          <Text strong style={{ fontSize: 13 }}>
                            {skill.skillName.includes("embedding")
                              ? "Embedding"
                              : skill.skillName.includes("llm")
                                ? "LLM 生成"
                                : skill.skillName.includes("vector")
                                  ? "向量检索"
                                  : skill.skillName}
                          </Text>
                        </Space>
                        <Text style={{ fontSize: 20, fontWeight: 600 }}>
                          {skill.totalTokens.toLocaleString()}
                          <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                            tokens
                          </Text>
                        </Text>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {skill.latencyMs}ms
                        </Text>
                      </Space>
                    </Card>
                  ))}
                </div>
              </>
            )}

            <Divider titlePlacement="left">问题内容</Divider>
            <Paragraph>{selectedLog.question}</Paragraph>

            {selectedLog.errorMessage && (
              <>
                <Divider titlePlacement="left">错误信息</Divider>
                <Paragraph type="danger">{selectedLog.errorMessage}</Paragraph>
              </>
            )}

            {selectedLog.references && selectedLog.references.length > 0 && (
              <>
                <Divider titlePlacement="left">引用来源</Divider>
                <Space orientation="vertical" style={{ width: "100%" }}>
                  {selectedLog.references.map((ref, idx) => (
                    <Card key={idx} size="small">
                      <Text strong>{ref.documentName}</Text>
                      <br />
                      <Text type="secondary">Score: {(ref.score * 100).toFixed(1)}%</Text>
                    </Card>
                  ))}
                </Space>
              </>
            )}

            {selectedLog.skillCalls && selectedLog.skillCalls.length > 0 && (
              <>
                <Divider titlePlacement="left">
                  <Space>
                    <ApiOutlined />
                    MCP Skill 调用 ({selectedLog.skillCalls.length})
                  </Space>
                </Divider>
                <Space orientation="vertical" style={{ width: "100%" }}>
                  {selectedLog.skillCalls.map((skill, idx) => (
                    <Card key={idx} size="small">
                      <Descriptions column={2} size="small">
                        <Descriptions.Item label="Skill">{skill.skillName}</Descriptions.Item>
                        <Descriptions.Item label="状态">
                          <Tag color={skill.status === "success" ? "success" : "error"}>
                            {skill.status}
                          </Tag>
                        </Descriptions.Item>
                        <Descriptions.Item label="延迟">{skill.latencyMs}ms</Descriptions.Item>
                        <Descriptions.Item label="Tokens">{skill.totalTokens}</Descriptions.Item>
                      </Descriptions>
                      {skill.inputSummary && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          输入: {skill.inputSummary}
                        </Text>
                      )}
                    </Card>
                  ))}
                </Space>
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

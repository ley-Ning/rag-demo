"use client";

import { SendOutlined, RobotOutlined, UserOutlined, FileTextOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { askQuestion, fetchModels } from "@/lib/rag-api";
import { ModelItem } from "@/types/rag";

interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  references?: Array<{
    documentName: string;
    chunkId: string;
    score: number;
  }>;
}

const quickQuestions = [
  "请总结这个项目的技术架构",
  "文档解析流程中有哪些关键步骤？",
  "如何优化向量检索的准确性？",
];

function formatTurnTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ChatPage() {
  const [form] = Form.useForm<{ question: string }>();
  const [models, setModels] = useState<ModelItem[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>();
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [loadingModels, setLoadingModels] = useState(true);
  const [asking, setAsking] = useState(false);
  const [lastSessionId, setLastSessionId] = useState<string>();
  const [apiMessage, contextHolder] = message.useMessage();
  const threadRef = useRef<HTMLDivElement>(null);

  const chatModels = useMemo(
    () =>
      models.filter(
        (item) =>
          item.capabilities.includes("chat") && item.status === "online",
      ),
    [models],
  );

  useEffect(() => {
    const run = async () => {
      try {
        const data = await fetchModels();
        setModels(data);
        const firstChatModel = data.find(
          (item) =>
            item.capabilities.includes("chat") && item.status === "online",
        );
        setSelectedModel(firstChatModel?.id);
      } catch (error) {
        apiMessage.error((error as Error).message || "模型加载失败");
      } finally {
        setLoadingModels(false);
      }
    };

    void run();
  }, [apiMessage]);

  useEffect(() => {
    const panel = threadRef.current;
    if (!panel) {
      return;
    }
    panel.scrollTop = panel.scrollHeight;
  }, [chatTurns, asking]);

  const onFinish = async (values: { question: string }) => {
    if (!selectedModel) {
      apiMessage.warning("请先选择一个聊天模型");
      return;
    }

    const question = values.question.trim();
    if (!question) {
      return;
    }

    setAsking(true);
    form.resetFields();

    try {
      const userTurn: ChatTurn = {
        role: "user",
        content: question,
        createdAt: Date.now(),
      };
      setChatTurns((prev) => [...prev, userTurn]);

      const result = await askQuestion({
        question,
        modelId: selectedModel,
        sessionId: lastSessionId,
      });
      setLastSessionId(result.sessionId);

      const assistantTurn: ChatTurn = {
        role: "assistant",
        content: result.answer,
        createdAt: Date.now(),
        references: result.references.map((item) => ({
          documentName: item.documentName,
          chunkId: item.chunkId,
          score: item.score,
        })),
      };
      setChatTurns((prev) => [...prev, assistantTurn]);
    } catch (error) {
      apiMessage.error((error as Error).message || "提问失败");
    } finally {
      setAsking(false);
    }
  };

  const chooseQuickQuestion = (question: string) => {
    form.setFieldValue("question", question);
  };

  const activeModel = chatModels.find((model) => model.id === selectedModel);

  return (
    <div className="chat-view page-stack">
      {contextHolder}

      {/* Hero Section */}
      <Card className="hero-card">
        <div className="hero-card__grid">
          <div>
            <Typography.Text className="hero-card__eyebrow">
              智能问答
            </Typography.Text>
            <Typography.Title level={3} className="hero-card__title">
              精准检索，溯源可信
            </Typography.Title>
            <Typography.Paragraph className="hero-card__desc">
              基于企业知识库的智能问答系统，每条回答都有据可查，让 AI 决策更加透明可靠。
            </Typography.Paragraph>
          </div>

          <div className="hero-card__stats">
            <div className="hero-stat">
              <span className="hero-stat__label">可用模型</span>
              <span className="hero-stat__value">{chatModels.length}</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat__label">对话轮次</span>
              <span className="hero-stat__value">{chatTurns.length}</span>
            </div>
          </div>
        </div>
      </Card>

      {/* Main Content Grid */}
      <div className="chat-grid">
        {/* Left Panel - Controls */}
        <Card className="panel-card">
          <Space orientation="vertical" size={16} style={{ width: "100%" }}>
            <div>
              <Typography.Title level={5} className="panel-title">
                模型配置
              </Typography.Title>
              <Typography.Text className="panel-subtitle">
                选择 AI 模型开始对话
              </Typography.Text>
            </div>

            <Select
              value={selectedModel}
              placeholder="请选择聊天模型"
              loading={loadingModels}
              onChange={setSelectedModel}
              size="large"
              style={{ width: "100%" }}
              options={chatModels.map((model) => ({
                value: model.id,
                label: `${model.name} · ${model.provider}`,
              }))}
            />

            {!loadingModels && chatModels.length === 0 ? (
              <Alert
                type="warning"
                showIcon
                message="暂无可用模型"
                description="请先在模型管理页面检查模型状态。"
              />
            ) : null}

            {activeModel ? (
              <div className="model-chip">
                <Tag className="model-chip__tag">{activeModel.provider}</Tag>
                <span>最大 Token: {activeModel.maxTokens.toLocaleString()}</span>
              </div>
            ) : null}

            {/* Quick Questions */}
            <div className="quick-questions">
              <Typography.Text className="quick-questions__label">
                快速提问
              </Typography.Text>
              <Space orientation="vertical" style={{ width: "100%" }}>
                {quickQuestions.map((question) => (
                  <Button
                    key={question}
                    type="text"
                    block
                    className="quick-questions__item"
                    onClick={() => chooseQuickQuestion(question)}
                    style={{ textAlign: "left", justifyContent: "flex-start" }}
                  >
                    {question}
                  </Button>
                ))}
              </Space>
            </div>

            {/* Question Form */}
            <Form form={form} layout="vertical" onFinish={onFinish}>
              <Form.Item
                label="输入问题"
                name="question"
                rules={[{ required: true, message: "请输入问题" }]}
              >
                <Input.TextArea
                  placeholder="请输入您想了解的问题..."
                  rows={5}
                  maxLength={2000}
                  showCount
                />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                icon={<SendOutlined />}
                loading={asking}
                disabled={!selectedModel}
                className="action-btn"
                block
              >
                发送问题
              </Button>
            </Form>
          </Space>
        </Card>

        {/* Right Panel - Chat Thread */}
        <Card className="panel-card panel-card--thread">
          <div className="thread-header">
            <Typography.Title level={5} className="panel-title">
              对话记录
            </Typography.Title>
            <Tag className="thread-tag">
              {asking ? "思考中..." : `${chatTurns.length} 条消息`}
            </Tag>
          </div>

          <div className="thread-list" ref={threadRef}>
            {chatTurns.length === 0 ? (
              <Empty
                description="开始您的第一段对话"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              chatTurns.map((turn, index) => (
                <div
                  key={`${turn.role}-${index}-${turn.createdAt}`}
                  className={`chat-turn chat-turn--${turn.role}`}
                >
                  <div className="chat-turn__meta">
                    <span>
                      {turn.role === "user" ? (
                        <>
                          <UserOutlined style={{ marginRight: 6 }} />
                          我
                        </>
                      ) : (
                        <>
                          <RobotOutlined style={{ marginRight: 6 }} />
                          AI 助手
                        </>
                      )}
                    </span>
                    <span>{formatTurnTime(turn.createdAt)}</span>
                  </div>
                  <Typography.Paragraph className="chat-turn__content">
                    {turn.content}
                  </Typography.Paragraph>
                  {turn.references?.length ? (
                    <div className="chat-turn__refs">
                      <FileTextOutlined style={{ color: "var(--rag-text-muted)" }} />
                      {turn.references.map((ref) => (
                        <Tag
                          key={`${ref.documentName}-${ref.chunkId}`}
                          className="chat-ref-tag"
                        >
                          {ref.documentName} · {ref.chunkId} ·{" "}
                          {(ref.score * 100).toFixed(0)}%
                        </Tag>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

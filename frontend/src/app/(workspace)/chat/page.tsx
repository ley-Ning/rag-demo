"use client";

import {
  SendOutlined,
  RobotOutlined,
  UserOutlined,
  FileTextOutlined,
  FolderOutlined,
  PlusOutlined,
  DeleteOutlined,
  HistoryOutlined,
} from "@ant-design/icons";
import {
  Button,
  Empty,
  Input,
  List,
  Select,
  Tag,
  Typography,
  message,
  Spin,
  Tooltip,
  Popconfirm,
  Switch,
  Space,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  askQuestionStream,
  deleteChatSession,
  fetchChatSessions,
  fetchDocuments,
  fetchModels,
  fetchSessionMessages,
} from "@/lib/rag-api";
import { ChatSession, DocumentItem, ModelItem, ToolRunItem } from "@/types/rag";

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  references?: Array<{
    documentName: string;
    chunkId: string;
    score: number;
  }>;
  toolRuns?: ToolRunItem[];
  deepThinkSummary?: string | null;
}

function formatTurnTime(timestamp: number | string) {
  const date = typeof timestamp === "string" ? new Date(timestamp) : new Date(timestamp);
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatSessionTime(timestamp: string) {
  const date = new Date(timestamp);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } else if (days === 1) {
    return "昨天";
  } else if (days < 7) {
    return `${days} 天前`;
  } else {
    return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  }
}

const LAST_SESSION_KEY = "rag_last_session_id";

export default function ChatPage() {
  const [inputValue, setInputValue] = useState("");
  const [models, setModels] = useState<ModelItem[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>();
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [loadingModels, setLoadingModels] = useState(true);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [asking, setAsking] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [enableTools, setEnableTools] = useState(true);
  const [enableDeepThink, setEnableDeepThink] = useState(false);
  const [apiMessage, contextHolder] = message.useMessage();
  const threadRef = useRef<HTMLDivElement>(null);
  const initialRestoreDone = useRef(false);

  const chatModels = useMemo(
    () =>
      models.filter(
        (item) =>
          item.capabilities.includes("chat") && item.status === "online",
      ),
    [models],
  );

  const activeModel = chatModels.find((model) => model.id === selectedModel);

  // 加载模型
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

  // 加载文档
  useEffect(() => {
    const run = async () => {
      try {
        const data = await fetchDocuments("completed", 100);
        setDocuments(data.items);
      } catch (error) {
        console.error("加载文档列表失败:", error);
      } finally {
        setLoadingDocs(false);
      }
    };

    void run();
  }, []);

  // 加载会话列表
  const loadSessions = async () => {
    setLoadingSessions(true);
    try {
      const data = await fetchChatSessions(20, 0);
      setSessions(data.items);
    } catch (error) {
      console.error("加载会话列表失败:", error);
    } finally {
      setLoadingSessions(false);
    }
  };

  useEffect(() => {
    void loadSessions();
  }, []);

  // 从 localStorage 恢复上次会话
  useEffect(() => {
    if (initialRestoreDone.current) return;
    initialRestoreDone.current = true;

    const lastSessionId = localStorage.getItem(LAST_SESSION_KEY);
    if (lastSessionId) {
      void loadSessionHistory(lastSessionId);
    }
  }, []);

  // 当 currentSessionId 变化时，保存到 localStorage
  useEffect(() => {
    if (currentSessionId) {
      localStorage.setItem(LAST_SESSION_KEY, currentSessionId);
    } else {
      localStorage.removeItem(LAST_SESSION_KEY);
    }
  }, [currentSessionId]);

  // 自动滚动到底部
  useEffect(() => {
    const panel = threadRef.current;
    if (!panel) {
      return;
    }
    panel.scrollTop = panel.scrollHeight;
  }, [chatTurns, asking]);

  // 加载会话历史
  const loadSessionHistory = async (sessionId: string) => {
    try {
      const data = await fetchSessionMessages(sessionId);
      const turns: ChatTurn[] = data.messages.map((msg) => ({
        id: `msg-${msg.id}`,
        role: msg.role,
        content: msg.content,
        createdAt: new Date(msg.createdAt).getTime(),
        references: msg.references?.map((ref) => ({
          documentName: ref.documentName,
          chunkId: ref.chunkId,
          score: ref.score,
        })),
      }));
      setChatTurns(turns);
      setCurrentSessionId(sessionId);
    } catch (error) {
      apiMessage.error((error as Error).message || "加载历史失败");
    }
  };

  // 新建会话
  const handleNewChat = () => {
    setChatTurns([]);
    setCurrentSessionId(null);
    setInputValue("");
  };

  // 删除会话
  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.sessionId !== sessionId));
      if (currentSessionId === sessionId) {
        handleNewChat();
      }
      apiMessage.success("会话已删除");
    } catch (error) {
      apiMessage.error((error as Error).message || "删除失败");
    }
  };

  const handleSend = async () => {
    if (!selectedModel) {
      apiMessage.warning("请先选择一个聊天模型");
      return;
    }

    const question = inputValue.trim();
    if (!question) {
      return;
    }

    setAsking(true);
    setInputValue("");

    const userTurnId = `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const assistantTurnId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const useRag = selectedDocIds.length > 0;

    try {
      const userTurn: ChatTurn = {
        id: userTurnId,
        role: "user",
        content: question,
        createdAt: Date.now(),
      };
      const assistantTurn: ChatTurn = {
        id: assistantTurnId,
        role: "assistant",
        content: "",
        createdAt: Date.now(),
        references: [],
      };
      setChatTurns((prev) => [...prev, userTurn, assistantTurn]);

      await askQuestionStream(
        {
          question,
          modelId: selectedModel,
          sessionId: currentSessionId || undefined,
          useRag,
          documentIds: useRag ? selectedDocIds : undefined,
          enableTools,
          enableDeepThink,
        },
        {
          onChunk: (text) => {
            setChatTurns((prev) =>
              prev.map((turn) =>
                turn.id === assistantTurnId
                  ? { ...turn, content: `${turn.content}${text}` }
                  : turn,
              ),
            );
          },
          onDone: (result) => {
            // 如果是新会话，更新当前会话ID
            if (!currentSessionId) {
              setCurrentSessionId(result.sessionId);
            }
            // 只有 RAG 模式才设置引用来源
            if (useRag && result.references?.length) {
              setChatTurns((prev) =>
                prev.map((turn) =>
                  turn.id === assistantTurnId
                    ? {
                        ...turn,
                        references: result.references.map((item) => ({
                          documentName: item.documentName,
                          chunkId: item.chunkId,
                          score: item.score,
                        })),
                      }
                    : turn,
                ),
              );
            }
            setChatTurns((prev) =>
              prev.map((turn) =>
                turn.id === assistantTurnId
                  ? {
                      ...turn,
                      toolRuns: result.toolRuns || [],
                      deepThinkSummary: result.deepThinkSummary || null,
                    }
                  : turn,
              ),
            );
            // 刷新会话列表
            void loadSessions();
          },
        },
      );
    } catch (error) {
      apiMessage.error((error as Error).message || "提问失败");
      setChatTurns((prev) =>
        prev.map((turn) =>
          turn.id === assistantTurnId
            ? {
                ...turn,
                content: "抱歉，当前回答失败，请稍后重试。",
              }
            : turn,
        ),
      );
    } finally {
      setAsking(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const removeSelectedDoc = (docId: string) => {
    setSelectedDocIds((prev) => prev.filter((id) => id !== docId));
  };

  const selectedDocNames = selectedDocIds.map((id) => {
    const doc = documents.find((d) => d.documentId === id);
    return { id, name: doc?.fileName || id };
  });

  return (
    <div className="chat-page">
      {contextHolder}

      {/* Main Chat Area */}
      <div className="chat-main chat-main--full">
        {/* Chat Header */}
        <div className="chat-header">
          <div className="chat-header__left">
            <Select
              value={selectedModel}
              placeholder={loadingModels ? "加载中..." : "选择模型"}
              loading={loadingModels}
              onChange={setSelectedModel}
              style={{ minWidth: 180 }}
              options={chatModels.map((model) => ({
                value: model.id,
                label: model.name,
              }))}
            />
            {selectedDocIds.length > 0 && (
              <Tag color="blue" className="chat-header__rag-tag">
                RAG · {selectedDocIds.length} 文档
              </Tag>
            )}
          </div>
          <div className="chat-header__actions">
            <Space size={8}>
              <Tooltip title="自动调用插件">
                <Space size={4}>
                  <span style={{ fontSize: 12, color: "#6e6e73" }}>插件</span>
                  <Switch size="small" checked={enableTools} onChange={setEnableTools} />
                </Space>
              </Tooltip>
              <Tooltip title="深度思考模式">
                <Space size={4}>
                  <span style={{ fontSize: 12, color: "#6e6e73" }}>深思</span>
                  <Switch size="small" checked={enableDeepThink} onChange={setEnableDeepThink} />
                </Space>
              </Tooltip>
            </Space>
            <Tooltip title="选择文档范围">
              <Button
                type="text"
                icon={<FolderOutlined />}
                onClick={() => setShowSettings(!showSettings)}
                className={showSettings ? "active" : ""}
              />
            </Tooltip>
            <Tooltip title="历史对话">
              <Button
                type="text"
                icon={<HistoryOutlined />}
                onClick={() => setShowHistory(!showHistory)}
                className={showHistory ? "active" : ""}
              />
            </Tooltip>
            <Tooltip title="新对话">
              <Button
                type="text"
                icon={<PlusOutlined />}
                onClick={handleNewChat}
              />
            </Tooltip>
          </div>
        </div>

        {/* Chat Messages */}
        <div className="chat-messages" ref={threadRef}>
          {chatTurns.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty__icon">
                <RobotOutlined />
              </div>
              <Typography.Title level={4} className="chat-empty__title">
                您好，我是 AI 助手
              </Typography.Title>
              <Typography.Text className="chat-empty__desc">
                基于企业知识库的智能问答，每条回答都有据可查
              </Typography.Text>
              <div className="chat-empty__tips">
                <Tag color="blue">点击文件夹图标选择文档范围</Tag>
              </div>
            </div>
          ) : (
            <div className="chat-thread">
              {chatTurns.map((turn) => (
                <div
                  key={turn.id}
                  className={`chat-bubble chat-bubble--${turn.role}`}
                >
                  <div className="chat-bubble__avatar">
                    {turn.role === "user" ? (
                      <UserOutlined />
                    ) : (
                      <RobotOutlined />
                    )}
                  </div>
                  <div className="chat-bubble__body">
                    <div className="chat-bubble__header">
                      <span className="chat-bubble__name">
                        {turn.role === "user" ? "我" : "AI 助手"}
                      </span>
                      <span className="chat-bubble__time">
                        {formatTurnTime(turn.createdAt)}
                      </span>
                    </div>
                    <div className="chat-bubble__content">
                      {turn.content || (turn.role === "assistant" && asking ? "思考中..." : "")}
                    </div>
                    {turn.deepThinkSummary ? (
                      <div className="chat-bubble__refs">
                        <RobotOutlined />
                        <span className="chat-bubble__refs-label">深度思考：</span>
                        <Typography.Text type="secondary">
                          {turn.deepThinkSummary}
                        </Typography.Text>
                      </div>
                    ) : null}
                    {turn.toolRuns?.length ? (
                      <div className="chat-bubble__refs">
                        <FileTextOutlined />
                        <span className="chat-bubble__refs-label">插件调用：</span>
                        {turn.toolRuns.map((run, idx) => (
                          <Tag key={`${turn.id}-tool-${idx}`} className="chat-ref-tag">
                            {run.toolName} · {run.status} · {run.latencyMs}ms
                          </Tag>
                        ))}
                      </div>
                    ) : null}
                    {turn.references?.length ? (
                      <div className="chat-bubble__refs">
                        <FileTextOutlined />
                        <span className="chat-bubble__refs-label">引用来源：</span>
                        {turn.references.map((ref, refIndex) => (
                          <Tag key={refIndex} className="chat-ref-tag">
                            {ref.documentName} · {(ref.score * 100).toFixed(0)}%
                          </Tag>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Chat Input */}
        <div className="chat-input-area">
          <div className="chat-input-container">
            <Input.TextArea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入您的问题，按 Enter 发送..."
              autoSize={{ minRows: 1, maxRows: 4 }}
              className="chat-input"
              disabled={asking}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              loading={asking}
              disabled={!inputValue.trim() || !selectedModel}
              className="chat-send-btn"
            />
          </div>
          <div className="chat-input-hint">
            {activeModel ? (
              <span>
                {activeModel.name} · {selectedDocIds.length > 0 ? "知识库增强" : "普通对话"}
              </span>
            ) : (
              <span className="text-warning">请先选择模型</span>
            )}
          </div>
        </div>
      </div>

      {/* History Drawer */}
      <div className={`chat-drawer ${showHistory ? "chat-drawer--open" : ""}`}>
        <div className="chat-drawer__header">
          <span>历史对话</span>
          <Button
            type="text"
            size="small"
            onClick={() => setShowHistory(false)}
          >
            ×
          </Button>
        </div>
        <div className="chat-drawer__content">
          {loadingSessions ? (
            <div className="chat-drawer__loading">
              <Spin size="small" />
            </div>
          ) : sessions.length === 0 ? (
            <Empty description="暂无历史对话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <List
              dataSource={sessions}
              renderItem={(session) => (
                <List.Item
                  className={`chat-drawer__item ${currentSessionId === session.sessionId ? "active" : ""}`}
                  onClick={() => {
                    loadSessionHistory(session.sessionId);
                    setShowHistory(false);
                  }}
                >
                  <div className="chat-drawer__item-content">
                    <div className="chat-drawer__item-title">{session.title}</div>
                    <div className="chat-drawer__item-meta">
                      <span>{formatSessionTime(session.updatedAt)}</span>
                      {session.useRag && <Tag color="blue">RAG</Tag>}
                    </div>
                  </div>
                  <Popconfirm
                    title="确定删除？"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      handleDeleteSession(session.sessionId, e as unknown as React.MouseEvent);
                    }}
                  >
                    <Button
                      type="text"
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Popconfirm>
                </List.Item>
              )}
            />
          )}
        </div>
      </div>

      {/* Settings Drawer */}
      <div className={`chat-drawer ${showSettings ? "chat-drawer--open" : ""}`}>
        <div className="chat-drawer__header">
          <span>文档范围</span>
          <Button
            type="text"
            size="small"
            onClick={() => setShowSettings(false)}
          >
            ×
          </Button>
        </div>
        <div className="chat-drawer__content">
          <div className="chat-drawer__section">
            <div className="chat-drawer__section-title">选择文档</div>
            {loadingDocs ? (
              <div className="chat-drawer__loading">
                <Spin size="small" />
              </div>
            ) : documents.length === 0 ? (
              <Empty description="暂无已处理的文档" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <>
                <Select
                  mode="multiple"
                  placeholder="选择文档（默认全部）"
                  value={selectedDocIds}
                  onChange={setSelectedDocIds}
                  style={{ width: "100%" }}
                  maxTagCount={3}
                  options={documents.map((doc) => ({
                    value: doc.documentId,
                    label: doc.fileName,
                  }))}
                />
                {selectedDocIds.length > 0 && (
                  <div className="chat-drawer__selected">
                    <div className="chat-drawer__selected-header">
                      <span>已选 {selectedDocIds.length} 个</span>
                      <Button
                        type="link"
                        size="small"
                        onClick={() => setSelectedDocIds([])}
                      >
                        清空
                      </Button>
                    </div>
                    <div className="chat-drawer__selected-list">
                      {selectedDocNames.map((doc) => (
                        <Tag
                          key={doc.id}
                          closable
                          onClose={() => removeSelectedDoc(doc.id)}
                        >
                          {doc.name}
                        </Tag>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
          <div className="chat-drawer__section">
            <div className="chat-drawer__section-title">统计</div>
            <div className="chat-drawer__stats">
              <div className="chat-drawer__stat">
                <span className="chat-drawer__stat-label">可用文档</span>
                <span className="chat-drawer__stat-value">{documents.length}</span>
              </div>
              <div className="chat-drawer__stat">
                <span className="chat-drawer__stat-label">对话轮次</span>
                <span className="chat-drawer__stat-value">{Math.ceil(chatTurns.length / 2)}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Overlay */}
      {(showHistory || showSettings) && (
        <div
          className="chat-drawer__overlay"
          onClick={() => {
            setShowHistory(false);
            setShowSettings(false);
          }}
        />
      )}
    </div>
  );
}

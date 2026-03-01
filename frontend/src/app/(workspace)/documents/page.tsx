"use client";

import {
  CopyOutlined,
  ReloadOutlined,
  SearchOutlined,
  InboxOutlined,
  PartitionOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  FileSearchOutlined,
  DeleteOutlined,
  EyeOutlined,
} from "@ant-design/icons";
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Segmented,
  Space,
  Statistic,
  Switch,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadProps } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  deleteDocument,
  fetchDocumentChunks,
  fetchDocuments,
  splitPreview,
  uploadDocument,
} from "@/lib/rag-api";
import type { DocumentChunksResult } from "@/lib/rag-api";
import { ChunkPreview, DocumentItem, SplitStrategy } from "@/types/rag";

const splitStrategyOptions: Array<{
  label: string;
  value: SplitStrategy;
  description: string;
}> = [
  { label: "固定长度", value: "fixed", description: "按固定字符数切分，适合格式统一的文档" },
  { label: "按句切分", value: "sentence", description: "按句子边界切分，保持语义完整性" },
  { label: "按段切分", value: "paragraph", description: "按段落边界切分，适合结构化文档" },
  {
    label: "父子分块",
    value: "parent_child",
    description: "先父块后子块，利于父召回子精排",
  },
  {
    label: "PageIndex",
    value: "pageindex",
    description: "按标题结构建索引并切分，便于章节级定位",
  },
];

function estimateChunkCount(
  textLength: number,
  chunkSize: number,
  overlap: number,
) {
  if (textLength <= 0 || chunkSize <= 0) {
    return 0;
  }

  const step = Math.max(chunkSize - overlap, 1);
  return Math.ceil(textLength / step);
}

function formatFileSize(sizeInBytes: number) {
  if (!Number.isFinite(sizeInBytes) || sizeInBytes <= 0) {
    return "0 B";
  }
  if (sizeInBytes < 1024) {
    return `${sizeInBytes} B`;
  }
  if (sizeInBytes < 1024 * 1024) {
    return `${(sizeInBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeInBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatStrategyLabel(strategy: string | null | undefined) {
  if (!strategy) {
    return "fixed";
  }
  if (strategy === "sentence") {
    return "sentence（按句）";
  }
  if (strategy === "paragraph") {
    return "paragraph（按段）";
  }
  if (strategy === "parent_child") {
    return "parent_child（父子）";
  }
  if (strategy === "pageindex") {
    return "pageindex（结构索引）";
  }
  return strategy;
}

function renderStatusTag(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "completed") {
    return (
      <Tag className="document-status-tag document-status-tag--completed">
        <CheckCircleOutlined />
        已完成
      </Tag>
    );
  }
  if (normalized === "processing") {
    return (
      <Tag className="document-status-tag document-status-tag--processing">
        <SyncOutlined spin />
        处理中
      </Tag>
    );
  }
  if (normalized === "failed") {
    return (
      <Tag className="document-status-tag document-status-tag--failed">
        <ExclamationCircleOutlined />
        失败
      </Tag>
    );
  }
  return (
    <Tag className="document-status-tag document-status-tag--queued">
      <ClockCircleOutlined />
      排队中
    </Tag>
  );
}

export default function DocumentsPage() {
  const [content, setContent] = useState("");
  const [chunkSize, setChunkSize] = useState(400);
  const [overlap, setOverlap] = useState(50);
  const [splitStrategy, setSplitStrategy] = useState<SplitStrategy>("fixed");
  const [chunks, setChunks] = useState<ChunkPreview[]>([]);
  const [uploading, setUploading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [autoPreview, setAutoPreview] = useState(true);
  const [lastTaskId, setLastTaskId] = useState<string>();
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [refreshingDocuments, setRefreshingDocuments] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [deletingDocId, setDeletingDocId] = useState<string>();
  const [chunksModalOpen, setChunksModalOpen] = useState(false);
  const [selectedDocument, setSelectedDocument] = useState<DocumentItem | null>(null);
  const [originalModalOpen, setOriginalModalOpen] = useState(false);
  const [originalPreviewDoc, setOriginalPreviewDoc] = useState<DocumentItem | null>(null);
  const [documentChunks, setDocumentChunks] = useState<DocumentChunksResult | null>(null);
  const [loadingChunks, setLoadingChunks] = useState(false);
  const [chunksPage, setChunksPage] = useState(1);
  const [chunksPageSize, setChunksPageSize] = useState(20);
  const [chunksKeyword, setChunksKeyword] = useState("");
  const [jumpChunkIndex, setJumpChunkIndex] = useState<number>();
  const [activeChunkIndex, setActiveChunkIndex] = useState<number>();
  const [apiMessage, contextHolder] = message.useMessage();

  const canPreview = useMemo(() => content.trim().length > 0, [content]);
  const estimated = useMemo(
    () => estimateChunkCount(content.length, chunkSize, overlap),
    [chunkSize, content.length, overlap],
  );
  const filteredChunks = useMemo(() => {
    if (!documentChunks) {
      return [];
    }
    const keyword = chunksKeyword.trim().toLowerCase();
    if (!keyword) {
      return documentChunks.chunks;
    }
    return documentChunks.chunks.filter((item) => {
      const inText = item.content.toLowerCase().includes(keyword);
      const inIndex = String(item.chunkIndex).includes(keyword);
      return inText || inIndex;
    });
  }, [chunksKeyword, documentChunks]);
  const chunksRangeText = useMemo(() => {
    if (!documentChunks || documentChunks.total <= 0) {
      return "暂无分块";
    }
    const start = (chunksPage - 1) * chunksPageSize + 1;
    const end = Math.min(chunksPage * chunksPageSize, documentChunks.total);
    return `${start}-${end} / ${documentChunks.total}`;
  }, [chunksPage, chunksPageSize, documentChunks]);
  const originalPreviewUrl = useMemo(() => {
    if (!originalPreviewDoc) {
      return "";
    }
    return `/api/v1/documents/${encodeURIComponent(originalPreviewDoc.documentId)}/file`;
  }, [originalPreviewDoc]);

  const runPreview = useCallback(async () => {
    if (!canPreview) {
      setChunks([]);
      return;
    }

    setPreviewing(true);
    try {
      const result = await splitPreview({
        content,
        chunkSize,
        overlap,
        strategy: splitStrategy,
      });
      setChunks(result.items);
    } catch (error) {
      apiMessage.error((error as Error).message || "切割预览失败");
    } finally {
      setPreviewing(false);
    }
  }, [apiMessage, canPreview, chunkSize, content, overlap, splitStrategy]);

  const loadDocuments = useCallback(
    async (mode: "init" | "refresh" = "refresh") => {
      if (mode === "init") {
        setLoadingDocuments(true);
      } else {
        setRefreshingDocuments(true);
      }
      try {
        const filterStatus = statusFilter === "all" ? undefined : statusFilter;
        const result = await fetchDocuments(filterStatus, 50);
        setDocuments(result.items);
      } catch (error) {
        apiMessage.error((error as Error).message || "文档列表加载失败");
      } finally {
        if (mode === "init") {
          setLoadingDocuments(false);
        } else {
          setRefreshingDocuments(false);
        }
      }
    },
    [apiMessage, statusFilter],
  );

  const handleDeleteDocument = async (doc: DocumentItem) => {
    setDeletingDocId(doc.documentId);
    try {
      await deleteDocument(doc.documentId);
      setDocuments((prev) => prev.filter((item) => item.documentId !== doc.documentId));
      apiMessage.success(`文档 "${doc.fileName}" 已删除`);
    } catch (error) {
      apiMessage.error((error as Error).message || "删除失败");
    } finally {
      setDeletingDocId(undefined);
    }
  };

  const handlePreviewOriginal = (doc: DocumentItem) => {
    setOriginalPreviewDoc(doc);
    setOriginalModalOpen(true);
  };

  const loadDocumentChunks = useCallback(
    async (documentId: string, page = 1, pageSize = 20) => {
      setLoadingChunks(true);
      try {
        const result = await fetchDocumentChunks(
          documentId,
          pageSize,
          Math.max((page - 1) * pageSize, 0),
        );
        setDocumentChunks(result);
        setChunksPage(page);
        setChunksPageSize(pageSize);
      } catch (error) {
        apiMessage.error((error as Error).message || "获取分块失败");
      } finally {
        setLoadingChunks(false);
      }
    },
    [apiMessage],
  );

  const handleViewChunks = async (doc: DocumentItem) => {
    setSelectedDocument(doc);
    setDocumentChunks(null);
    setChunksKeyword("");
    setJumpChunkIndex(undefined);
    setActiveChunkIndex(undefined);
    setChunksModalOpen(true);
    await loadDocumentChunks(doc.documentId, 1, 20);
  };

  const handleChangeChunkPage = (page: number, pageSize: number) => {
    if (!selectedDocument) {
      return;
    }
    setActiveChunkIndex(undefined);
    void loadDocumentChunks(selectedDocument.documentId, page, pageSize);
  };

  const handleJumpChunk = () => {
    if (!selectedDocument || !jumpChunkIndex || jumpChunkIndex <= 0) {
      return;
    }
    const targetPage = Math.ceil(jumpChunkIndex / chunksPageSize);
    setActiveChunkIndex(jumpChunkIndex);
    void loadDocumentChunks(selectedDocument.documentId, targetPage, chunksPageSize);
  };

  const handleCopyChunk = async (contentText: string) => {
    try {
      await navigator.clipboard.writeText(contentText);
      apiMessage.success("分块内容已复制");
    } catch {
      apiMessage.error("复制失败，请检查浏览器权限");
    }
  };

  useEffect(() => {
    void loadDocuments("init");
  }, [loadDocuments]);

  useEffect(() => {
    if (!autoPreview || !canPreview) {
      return;
    }

    const timer = setTimeout(() => {
      void runPreview();
    }, 420);

    return () => clearTimeout(timer);
  }, [autoPreview, canPreview, runPreview]);

  const uploadProps: UploadProps = {
    maxCount: 1,
    showUploadList: false,
    customRequest: async (options) => {
      const file = options.file as File;
      setUploading(true);
      try {
        const result = await uploadDocument(file, splitStrategy);
        setLastTaskId(result.taskId);
        void loadDocuments("refresh");
        apiMessage.success(`文档已上传，任务号: ${result.taskId}`);
        options.onSuccess?.(result);
      } catch (error) {
        apiMessage.error((error as Error).message || "上传失败");
        options.onError?.(error as Error);
      } finally {
        setUploading(false);
      }
    },
  };

  return (
    <div className="documents-view page-stack">
      {contextHolder}

      {/* Tabs */}
      <Card className="panel-card">
        <Tabs
          defaultActiveKey="upload"
          items={[
            {
              key: "upload",
              label: (
                <span>
                  <InboxOutlined style={{ marginRight: 6 }} />
                  上传与预览
                </span>
              ),
              children: (
                <div className="documents-tab-content">
                  {/* Upload Area */}
                  <div className="documents-grid">
                    <Card className="panel-card" styles={{ body: { padding: 16 } }}>
                      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
                        <div>
                          <Typography.Title level={5} className="panel-title">
                            文档上传
                          </Typography.Title>
            <Typography.Text className="panel-subtitle">
              上传后将自动加入处理队列
            </Typography.Text>
          </div>

          <Form layout="vertical" style={{ marginBottom: 0 }}>
            <Form.Item label="切分策略" style={{ marginBottom: 0 }}>
              <Select
                value={splitStrategy}
                options={splitStrategyOptions.map((opt) => ({
                  value: opt.value,
                  label: (
                    <div>
                      <div style={{ fontWeight: 500 }}>{opt.label}</div>
                      <div style={{ fontSize: 12, color: "var(--rag-text-muted)" }}>
                        {opt.description}
                      </div>
                    </div>
                  ),
                }))}
                onChange={(value) => setSplitStrategy(value as SplitStrategy)}
              />
            </Form.Item>
          </Form>

          <Upload.Dragger {...uploadProps} disabled={uploading}>
                          <p className="ant-upload-drag-icon">
                            <InboxOutlined />
                          </p>
                          <p className="ant-upload-text" style={{ fontWeight: 500 }}>
                            点击或拖拽文件到此区域
                          </p>
                          <p className="ant-upload-hint" style={{ color: "var(--rag-text-muted)" }}>
                            支持 PDF、Word、TXT、Markdown 等格式
                          </p>
                        </Upload.Dragger>

                        <div className="upload-actions">
                          {lastTaskId ? (
                            <Tag
                              icon={<CheckCircleOutlined />}
                              className="thread-tag"
                              style={{ margin: 0 }}
                            >
                              任务号: {lastTaskId}
                            </Tag>
                          ) : (
                            <span style={{ color: "var(--rag-text-muted)", fontSize: 13 }}>
                              暂无上传任务
                            </span>
                          )}
                        </div>
                      </Space>
                    </Card>

                    <Card
                      className="panel-card"
                      styles={{ body: { padding: 16 } }}
                      extra={
                        <Space size={12} wrap>
                          <Typography.Text className="panel-subtitle">自动预览</Typography.Text>
                          <Switch checked={autoPreview} onChange={setAutoPreview} />
                          <Button
                            icon={previewing ? <SyncOutlined spin /> : <PartitionOutlined />}
                            onClick={() => void runPreview()}
                            size="small"
                          >
                            刷新
                          </Button>
                        </Space>
                      }
                    >
                      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
                        <div>
                          <Typography.Title level={5} className="panel-title">
                            切割预览
                          </Typography.Title>
              <Typography.Text className="panel-subtitle">
                调整参数并实时查看切割效果
              </Typography.Text>
            </div>

            <Form layout="vertical">
              <Form.Item label="切分策略">
                <Select
                  value={splitStrategy}
                  options={splitStrategyOptions.map((opt) => ({
                    value: opt.value,
                    label: (
                      <div>
                        <div style={{ fontWeight: 500 }}>{opt.label}</div>
                        <div style={{ fontSize: 12, color: "var(--rag-text-muted)" }}>
                          {opt.description}
                        </div>
                      </div>
                    ),
                  }))}
                  onChange={(value) =>
                    setSplitStrategy(
                      value as SplitStrategy,
                    )
                  }
                />
              </Form.Item>
              <Form.Item label="测试文本内容">
                <Input.TextArea
                              rows={6}
                              value={content}
                              onChange={(event) => setContent(event.target.value)}
                              placeholder="粘贴文档内容，观察切割效果..."
                              maxLength={20000}
                              showCount
                            />
                          </Form.Item>
                          <div className="chunk-params">
                            <Form.Item label="切片大小" style={{ marginBottom: 0, flex: 1 }}>
                              <InputNumber
                                min={100}
                                max={2000}
                                value={chunkSize}
                                onChange={(value) => setChunkSize(value ?? 400)}
                                style={{ width: "100%" }}
                                addonAfter="字符"
                              />
                            </Form.Item>
                            <Form.Item label="重叠长度" style={{ marginBottom: 0, flex: 1 }}>
                              <InputNumber
                                min={0}
                                max={500}
                                value={overlap}
                                onChange={(value) => setOverlap(value ?? 50)}
                                style={{ width: "100%" }}
                                addonAfter="字符"
                              />
                            </Form.Item>
                          </div>
                        </Form>
                      </Space>
                    </Card>
                  </div>

                  {/* Metrics */}
                  <div className="metric-row documents-metrics" style={{ marginTop: 16 }}>
                    <Card className="metric-card">
                      <Statistic
                        title="生成切片数"
                        value={chunks.length}
                        suffix="chunks"
                        prefix={<PartitionOutlined style={{ marginRight: 8, color: "var(--rag-accent)" }} />}
                      />
                    </Card>
                    <Card className="metric-card">
                      <Statistic
                        title="平均切片长度"
                        value={
                          chunks.length
                            ? Math.round(
                                chunks.reduce((acc, item) => acc + item.length, 0) / chunks.length,
                              )
                            : 0
                        }
                        suffix="chars"
                      />
                    </Card>
                  </div>

                  {/* Preview Results */}
                  <div style={{ marginTop: 16 }}>
                    <div className="thread-header" style={{ marginBottom: 12 }}>
                      <Typography.Text strong>
                        <FileTextOutlined style={{ marginRight: 8 }} />
                        切片结果
                      </Typography.Text>
                      <Tag className="thread-tag">
                        {previewing ? "处理中..." : `共 ${chunks.length} 个切片`}
                      </Tag>
                    </div>

                    {chunks.length === 0 ? (
                      <Empty
                        description="输入文本内容后查看切割结果"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                      />
                    ) : (
                      <List
                        loading={previewing}
                        dataSource={chunks}
                        renderItem={(item) => (
                          <List.Item className="chunk-item">
                            <div className="chunk-item__header">
                              <Typography.Text strong style={{ color: "var(--rag-text-main)" }}>
                                切片 #{item.chunkId}
                              </Typography.Text>
                              <Space size={8}>
                                <Tag style={{ borderRadius: 6 }}>
                                  {item.length} 字符 · {item.start}-{item.end}
                                </Tag>
                                {item.parentChunkId && (
                                  <Tag color="purple" style={{ borderRadius: 6 }}>
                                    父块: {item.parentChunkId}
                                  </Tag>
                                )}
                                {item.nodePath && (
                                  <Tag color="geekblue" style={{ borderRadius: 6 }}>
                                    {item.nodePath}
                                  </Tag>
                                )}
                                {typeof item.pageStart === "number" && typeof item.pageEnd === "number" && (
                                  <Tag color="cyan" style={{ borderRadius: 6 }}>
                                    页码: {item.pageStart}-{item.pageEnd}
                                  </Tag>
                                )}
                              </Space>
                            </div>
                            <Typography.Paragraph className="chunk-item__content">
                              {item.content}
                            </Typography.Paragraph>
                          </List.Item>
                        )}
                      />
                    )}
                  </div>
                </div>
              ),
            },
            {
              key: "manage",
              label: (
                <span>
                  <FileTextOutlined style={{ marginRight: 6 }} />
                  文档管理
                </span>
              ),
              children: (
                <div className="documents-tab-content">
                  {/* Toolbar */}
                  <div className="document-toolbar" style={{ marginBottom: 16 }}>
                    <Space size={12} wrap>
                      <Segmented
                        value={statusFilter}
                        onChange={(value) => setStatusFilter(value as string)}
                        options={[
                          { label: "全部", value: "all" },
                          { label: "已完成", value: "completed" },
                          { label: "处理中", value: "processing" },
                          { label: "排队中", value: "queued" },
                          { label: "失败", value: "failed" },
                        ]}
                      />
                      <Button
                        size="small"
                        icon={refreshingDocuments ? <SyncOutlined spin /> : <SyncOutlined />}
                        onClick={() => void loadDocuments("refresh")}
                      >
                        刷新
                      </Button>
                    </Space>
                    <Tag className="thread-tag">共 {documents.length} 条</Tag>
                  </div>

                  {/* Document List */}
                  {documents.length === 0 && !loadingDocuments ? (
                    <Empty
                      description="暂无上传记录，切换到「上传与预览」上传文档"
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                  ) : (
                    <List
                      loading={loadingDocuments || refreshingDocuments}
                      dataSource={documents}
                      renderItem={(item) => (
                        <List.Item className="document-item">
                          <div className="document-item__head">
                            <Typography.Text strong className="document-item__name">
                              {item.fileName}
                            </Typography.Text>
                            {renderStatusTag(item.status)}
                          </div>
                          <Typography.Text className="document-item__meta">
                            文档 ID: {item.documentId}
                          </Typography.Text>
                <Typography.Text className="document-item__meta">
                  体积: {formatFileSize(item.fileSizeBytes)} · 上传:{" "}
                  {formatTime(item.createdAt)}
                </Typography.Text>
                <Typography.Text className="document-item__meta">
                  策略: {formatStrategyLabel(item.strategy)}
                </Typography.Text>
                <div className="document-item__actions">
                            <Button
                              type="text"
                              size="small"
                              icon={<FileSearchOutlined />}
                              onClick={() => handlePreviewOriginal(item)}
                            >
                              预览原文
                            </Button>
                            <Button
                              type="text"
                              size="small"
                              icon={<EyeOutlined />}
                              onClick={() => void handleViewChunks(item)}
                            >
                              查看分块
                            </Button>
                            <Popconfirm
                              title="确认删除此文档？"
                              description="删除后相关分块和向量数据也将被删除。"
                              okText="删除"
                              cancelText="取消"
                              okButtonProps={{ danger: true, loading: deletingDocId === item.documentId }}
                              onConfirm={() => void handleDeleteDocument(item)}
                            >
                              <Button
                                type="text"
                                size="small"
                                danger
                                icon={<DeleteOutlined />}
                                loading={deletingDocId === item.documentId}
                              >
                                删除
                              </Button>
                            </Popconfirm>
                          </div>
                        </List.Item>
                      )}
                    />
                  )}
                </div>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={originalModalOpen}
        title={originalPreviewDoc ? `原文预览 - ${originalPreviewDoc.fileName}` : "原文预览"}
        onCancel={() => {
          setOriginalModalOpen(false);
          setOriginalPreviewDoc(null);
        }}
        footer={
          <Space>
            <Button
              onClick={() => {
                if (!originalPreviewUrl) {
                  return;
                }
                window.open(originalPreviewUrl, "_blank", "noopener,noreferrer");
              }}
            >
              新窗口打开
            </Button>
            <Button
              type="primary"
              onClick={() => {
                setOriginalModalOpen(false);
                setOriginalPreviewDoc(null);
              }}
            >
              关闭
            </Button>
          </Space>
        }
        width={1080}
        destroyOnHidden
        className="document-original-modal"
      >
        {originalPreviewUrl ? (
          <iframe
            key={originalPreviewUrl}
            src={originalPreviewUrl}
            className="document-original-frame"
            title="document-original-preview"
          />
        ) : (
          <Empty description="暂无可预览内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Modal>

      {/* Chunks Modal */}
      <Modal
        open={chunksModalOpen}
        title={selectedDocument ? `分块列表 - ${selectedDocument.fileName}` : "分块列表"}
        onCancel={() => {
          setChunksModalOpen(false);
          setSelectedDocument(null);
          setDocumentChunks(null);
          setChunksKeyword("");
          setJumpChunkIndex(undefined);
          setActiveChunkIndex(undefined);
        }}
        footer={null}
        width={960}
      >
        {loadingChunks ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <SyncOutlined spin style={{ fontSize: 24 }} />
          </div>
        ) : documentChunks ? (
          <div className="chunks-modal">
            <div className="chunks-modal__toolbar">
              <Space size={8} wrap>
                <Tag color="blue">状态: {documentChunks.status}</Tag>
                <Tag>范围: {chunksRangeText}</Tag>
                <Tag>当前页: {documentChunks.chunks.length} 条</Tag>
              </Space>
              <Space size={8} wrap>
                <Input
                  allowClear
                  value={chunksKeyword}
                  onChange={(event) => setChunksKeyword(event.target.value)}
                  placeholder="筛选当前页分块内容/编号"
                  prefix={<SearchOutlined />}
                  style={{ width: 240 }}
                />
                <InputNumber
                  min={1}
                  value={jumpChunkIndex}
                  onChange={(value) => setJumpChunkIndex(value ?? undefined)}
                  placeholder="跳转到分块号"
                  style={{ width: 140 }}
                />
                <Button onClick={handleJumpChunk}>跳转</Button>
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => {
                    if (!selectedDocument) {
                      return;
                    }
                    void loadDocumentChunks(selectedDocument.documentId, chunksPage, chunksPageSize);
                  }}
                >
                  刷新
                </Button>
              </Space>
            </div>

            {documentChunks.total === 0 ? (
              <Empty description="该文档暂无分块数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : filteredChunks.length === 0 ? (
              <Empty description="当前筛选条件没有命中分块" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <List
                dataSource={filteredChunks}
                style={{ maxHeight: 520, overflow: "auto" }}
                renderItem={(chunk) => (
                  <List.Item
                    className={
                      chunk.chunkIndex === activeChunkIndex
                        ? "chunk-item chunk-item--active"
                        : "chunk-item"
                    }
                  >
                    <div style={{ width: "100%" }}>
                      <div className="chunk-item__header">
                        <Space size={8} wrap>
                          <Typography.Text strong>分块 #{chunk.chunkIndex}</Typography.Text>
                          <Tag>{chunk.chunkId.slice(0, 8)}</Tag>
                          <Tag>{chunk.length} 字符</Tag>
                          <Tag>{chunk.tokenCount} tokens</Tag>
                          {chunk.nodePath ? <Tag color="geekblue">{chunk.nodePath}</Tag> : null}
                          {typeof chunk.pageStart === "number" && typeof chunk.pageEnd === "number" ? (
                            <Tag color="cyan">
                              页码: {chunk.pageStart}-{chunk.pageEnd}
                            </Tag>
                          ) : null}
                        </Space>
                        <Button
                          type="text"
                          size="small"
                          icon={<CopyOutlined />}
                          onClick={() => void handleCopyChunk(chunk.content)}
                        >
                          复制
                        </Button>
                      </div>
                      <Typography.Paragraph
                        ellipsis={{ rows: 4, expandable: true, symbol: "展开全文" }}
                        style={{
                          marginBottom: 0,
                          padding: 12,
                          background: "rgba(0,0,0,0.02)",
                          borderRadius: 6,
                        }}
                      >
                        {chunk.content}
                      </Typography.Paragraph>
                    </div>
                  </List.Item>
                )}
              />
            )}
            <div className="chunks-modal__footer">
              <Pagination
                current={chunksPage}
                pageSize={chunksPageSize}
                total={documentChunks.total}
                showSizeChanger
                pageSizeOptions={["10", "20", "50", "100"]}
                onChange={handleChangeChunkPage}
                showTotal={(totalValue, range) => `第 ${range[0]}-${range[1]} 条，共 ${totalValue} 条`}
              />
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

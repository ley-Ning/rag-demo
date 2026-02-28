"use client";

import {
  InboxOutlined,
  PartitionOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Space,
  Statistic,
  Switch,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadProps } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { splitPreview, uploadDocument } from "@/lib/rag-api";
import { ChunkPreview } from "@/types/rag";

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

export default function DocumentsPage() {
  const [content, setContent] = useState("");
  const [chunkSize, setChunkSize] = useState(400);
  const [overlap, setOverlap] = useState(50);
  const [chunks, setChunks] = useState<ChunkPreview[]>([]);
  const [uploading, setUploading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [autoPreview, setAutoPreview] = useState(true);
  const [lastTaskId, setLastTaskId] = useState<string>();
  const [apiMessage, contextHolder] = message.useMessage();

  const canPreview = useMemo(() => content.trim().length > 0, [content]);
  const estimated = useMemo(
    () => estimateChunkCount(content.length, chunkSize, overlap),
    [chunkSize, content.length, overlap],
  );

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
      });
      setChunks(result.items);
    } catch (error) {
      apiMessage.error((error as Error).message || "切割预览失败");
    } finally {
      setPreviewing(false);
    }
  }, [apiMessage, canPreview, chunkSize, content, overlap]);

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
        const result = await uploadDocument(file);
        setLastTaskId(result.taskId);
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

      {/* Hero Section */}
      <Card className="hero-card hero-card--documents">
        <div className="hero-card__grid">
          <div>
            <Typography.Text className="hero-card__eyebrow">
              文档中心
            </Typography.Text>
            <Typography.Title level={3} className="hero-card__title">
              智能解析，精准切割
            </Typography.Title>
            <Typography.Paragraph className="hero-card__desc">
              上传文档至知识库，实时预览切割效果。优化参数后再提交，确保数据质量。
            </Typography.Paragraph>
          </div>
          <div className="hero-card__stats">
            <div className="hero-stat">
              <span className="hero-stat__label">文本长度</span>
              <span className="hero-stat__value">{content.length.toLocaleString()}</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat__label">预估切片</span>
              <span className="hero-stat__value">{estimated}</span>
            </div>
          </div>
        </div>
      </Card>

      {/* Main Content Grid */}
      <div className="documents-grid">
        {/* Upload Panel */}
        <Card className="panel-card">
          <Space orientation="vertical" size={16} style={{ width: "100%" }}>
            <div>
              <Typography.Title level={5} className="panel-title">
                文档上传
              </Typography.Title>
              <Typography.Text className="panel-subtitle">
                上传后将自动加入处理队列
              </Typography.Text>
            </div>

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

        {/* Chunking Preview Panel */}
        <Card
          className="panel-card"
          extra={
            <Space size={12}>
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
              <Form.Item label="测试文本内容">
                <Input.TextArea
                  rows={8}
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  placeholder="粘贴文档内容，观察切割效果..."
                  maxLength={20000}
                  showCount
                />
              </Form.Item>
              <Space size={20} wrap>
                <Form.Item label="切片大小 (Chunk Size)" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={100}
                    max={2000}
                    value={chunkSize}
                    onChange={(value) => setChunkSize(value ?? 400)}
                    style={{ width: 140 }}
                  />
                </Form.Item>
                <Form.Item label="重叠长度 (Overlap)" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={0}
                    max={500}
                    value={overlap}
                    onChange={(value) => setOverlap(value ?? 50)}
                    style={{ width: 140 }}
                  />
                </Form.Item>
              </Space>
            </Form>
          </Space>
        </Card>
      </div>

      {/* Metrics Row */}
      <div className="metric-row" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
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
                    chunks.reduce((acc, item) => acc + item.length, 0) /
                      chunks.length,
                  )
                : 0
            }
            suffix="chars"
          />
        </Card>
      </div>

      {/* Results Panel */}
      <Card className="panel-card panel-card--results">
        <div className="thread-header">
          <Typography.Title level={5} className="panel-title">
            <FileTextOutlined style={{ marginRight: 8 }} />
            切片结果
          </Typography.Title>
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
                  <Tag style={{ borderRadius: 6 }}>
                    {item.length} 字符 · {item.start}-{item.end}
                  </Tag>
                </div>
                <Typography.Paragraph className="chunk-item__content">
                  {item.content}
                </Typography.Paragraph>
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  );
}

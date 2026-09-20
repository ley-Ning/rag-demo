"use client";

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { Button, message as antdMessage } from "antd";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownAnswerProps {
  content: string;
  /** 引用来源数量：回答中的 [n] (n <= count) 会渲染为可点击上标角标 */
  referenceCount?: number;
  /** 流式输出中：内容末尾显示打字光标 */
  streaming?: boolean;
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const [apiMessage, contextHolder] = antdMessage.useMessage();

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      apiMessage.success("代码已复制");
      setTimeout(() => setCopied(false), 1600);
    } catch {
      apiMessage.error("复制失败，请检查浏览器权限");
    }
  };

  return (
    <div className="md-answer__code">
      <div className="md-answer__code-header">
        <span className="md-answer__code-lang">{language || "text"}</span>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          onClick={() => void handleCopy()}
        >
          {copied ? "已复制" : "复制"}
        </Button>
      </div>
      <pre className="md-answer__code-body">
        <code>{code}</code>
      </pre>
      {contextHolder}
    </div>
  );
}

/**
 * 把回答里的引用标记 [n] 转成 markdown 上标链接，点击滚动到引用来源。
 * 仅处理 n 在引用范围内的标记，避免误伤普通方括号文本。
 */
function preprocessCitations(content: string, referenceCount: number): string {
  if (!referenceCount) {
    return content;
  }
  return content.replace(/\[(\d{1,2})\]/g, (match, num: string) => {
    const index = Number(num);
    if (index >= 1 && index <= referenceCount) {
      return `[<sup>${index}</sup>](#chat-ref-${index})`;
    }
    return match;
  });
}

export default function MarkdownAnswer({
  content,
  referenceCount = 0,
  streaming = false,
}: MarkdownAnswerProps) {
  const processed = useMemo(
    () => preprocessCitations(content, referenceCount),
    [content, referenceCount],
  );

  return (
    <div className="md-answer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            const isCitation = typeof href === "string" && href.startsWith("#chat-ref-");
            if (isCitation) {
              return (
                <a href={href} className="md-answer__citation" title="查看引用来源">
                  {children}
                </a>
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
          code({ className, children, ...props }) {
            const text = String(children ?? "");
            const match = /language-(\w+)/.exec(className || "");
            const isBlock = match !== null || text.includes("\n");
            if (!isBlock) {
              return (
                <code className="md-answer__inline-code" {...props}>
                  {children}
                </code>
              );
            }
            return <CodeBlock language={match?.[1] ?? ""} code={text.replace(/\n$/, "")} />;
          },
          pre({ children }) {
            // code 组件已渲染外壳，这里避免双层 pre
            return <>{children}</>;
          },
        }}
      >
        {processed}
      </ReactMarkdown>
      {streaming && <span className="md-answer__cursor" aria-hidden />}
    </div>
  );
}

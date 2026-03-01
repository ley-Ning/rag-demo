# PageIndex 文档索引系统设计方案

## 一、用大白话理解 PageIndex

### 1.1 问题是什么？

**传统切分（您现在的方式）**：
```
原始文档：一本产品手册 100 页
      ↓
切分器：咔咔咔，每 400 字切一刀
      ↓
结果：得到一堆碎片，碎片之间没有任何关系

问题：
- 碎片可能会把一句话切成两半
- 不知道哪个碎片属于哪一章
- 检索时只能靠"相似度"，找不到就完蛋
```

**举个例子**：
```
用户问："iPhone 15 Pro 的保修政策是什么？"

传统 RAG：
1. 把问题转成向量
2. 在所有碎片里找相似的
3. 可能找到一堆 "iPhone" "保修" 相关的碎片
4. 但不知道这些碎片属于哪个章节，上下文断裂
```

### 1.2 PageIndex 是什么？

**一句话总结**：PageIndex 就是给文档建一个"智能目录"。

**比喻**：
```
传统切分 = 把一本书撕成碎片
PageIndex = 保留书的目录，每个目录项都知道对应哪几页

就像你去图书馆找书：
- 笨办法：一本一本翻，看有没有你要的内容（传统向量检索）
- 聪明办法：先查目录，知道在哪个书架、哪一排，再去找（PageIndex）
```

**PageIndex 的数据结构**：
```json
{
  "title": "第一章 产品介绍",
  "node_id": "001",
  "page_start": 1,
  "page_end": 10,
  "summary": "本章介绍公司所有产品的基本信息...",
  "children": [
    {
      "title": "1.1 iPhone 系列",
      "node_id": "001-001",
      "page_start": 2,
      "page_end": 5,
      "summary": "iPhone 各型号的功能特点...",
      "children": [
        {
          "title": "1.1.1 iPhone 15 Pro",
          "node_id": "001-001-001",
          "page_start": 3,
          "page_end": 4,
          "summary": "iPhone 15 Pro 的详细参数..."
        }
      ]
    }
  ]
}
```

### 1.3 为什么 PageIndex 更好？

**核心洞察**：相似度 ≠ 相关性

```
场景：用户问 "苹果手机多少钱？"

向量检索可能返回：
1. "我们公司也卖水果，苹果 5 块钱一斤"（相似度高，但不相关！）
2. "iPhone 15 Pro 售价 8999 元"（这才是正确答案）

PageIndex 的优势：
1. 先看目录，知道"价格"应该在"产品定价"章节
2. 只在这个章节里检索
3. 避免跑到"水果销售"章节去瞎找
```

---

## 二、整体架构

```
                        ┌──────────────────────────────────────────┐
                        │           文档上传                        │
                        └──────────────┬───────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────────────────────┐
                        │      1. 文档解析 (保留结构)                │
                        │   - 提取标题层级                          │
                        │   - 记录每个章节的位置                     │
                        └──────────────┬───────────────────────────┘
                                       │
                         ┌─────────────┴─────────────┐
                         ▼                           ▼
          ┌──────────────────────────┐    ┌──────────────────────────┐
          │   2. 构建 PageIndex 树    │    │   3. 内容切分 (智能)      │
          │   - 每个章节是一个节点     │    │   - 按段落/句子切分       │
          │   - 记录页码范围          │    │   - 保持语义完整性        │
          │   - 生成章节摘要          │    │   - 关联到父节点          │
          └─────────────┬────────────┘    └─────────────┬────────────┘
                        │                               │
                        ▼                               ▼
          ┌──────────────────────────┐    ┌──────────────────────────┐
          │   doc_tree 表             │    │   document_chunks 表      │
          │   (文档的骨架结构)         │    │   (文档的血肉内容)        │
          └─────────────┬────────────┘    └─────────────┬────────────┘
                        │                               │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                            ┌──────────────────────────┐
                            │   4. 向量化              │
                            │   chunk_embeddings 表    │
                            └──────────────────────────┘
```

---

## 三、检索流程（混合检索）

```
用户提问："iPhone 15 Pro 保修多久？"
                │
                ▼
┌──────────────────────────────────────────────────────────────┐
│  第一步：意图分析（LLM）                                       │
│  - 这是一个关于什么的问题？                                    │
│  - 应该在文档的哪个部分找答案？                                │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  第二步：并行检索                                             │
│                                                              │
│  路径 A：PageIndex 路由            路径 B：向量相似检索        │
│  - 在 doc_node 表里找匹配的节点    - 直接在向量库里找相似内容   │
│  - 定位到"售后服务"章节            - 返回 top-k 相似片段       │
│  - 取出该章节下的所有 chunks                                  │
│                                                              │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  第三步：结果融合 (RRF)                                       │
│  - 把两路结果合并，去重，排序                                  │
│  - 给 PageIndex 命中的结果加权                                 │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  第四步：生成回答                                             │
│  - 用融合后的上下文                                           │
│  - 加上文档结构信息（这个内容来自哪个章节）                      │
│  - 生成带引用的精准回答                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 四、数据库设计

### 4.1 新增表结构

```sql
-- 文档树元数据（每个文档一棵树）
CREATE TABLE doc_trees (
    tree_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT,                           -- 文档标题
    description TEXT,                     -- 文档描述
    total_pages INTEGER DEFAULT 0,        -- 总页数
    total_nodes INTEGER DEFAULT 0,        -- 总节点数
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 树节点（文档的骨架）
CREATE TABLE doc_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tree_id UUID NOT NULL REFERENCES doc_trees(tree_id) ON DELETE CASCADE,
    parent_id UUID REFERENCES doc_nodes(id) ON DELETE CASCADE,
    node_level INTEGER NOT NULL,          -- 层级：0=根, 1=章, 2=节, 3=小节
    node_index INTEGER NOT NULL,          -- 同级排序
    title TEXT NOT NULL,                  -- 节点标题
    page_start INTEGER,                   -- 起始页码
    page_end INTEGER,                     -- 结束页码
    char_start INTEGER,                   -- 起始字符位置
    char_end INTEGER,                     -- 结束字符位置
    summary TEXT,                         -- 节点摘要（LLM 生成）
    keywords TEXT[],                      -- 关键词
    content_preview TEXT,                 -- 内容预览（前 200 字）
    embedding VECTOR(1536),               -- 节点标题/摘要的向量（用于路由）
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(tree_id, node_level, node_index),
    INDEX idx_doc_nodes_tree (tree_id),
    INDEX idx_doc_nodes_parent (parent_id)
);

-- 修改现有的 document_chunks 表，增加关联
ALTER TABLE document_chunks
ADD COLUMN node_id UUID REFERENCES doc_nodes(id) ON DELETE SET NULL;

-- 为节点向量创建索引
CREATE INDEX idx_doc_nodes_embedding ON doc_nodes
USING hnsw (embedding vector_cosine_ops);
```

### 4.2 数据关系图

```
documents (1) ──────► doc_trees (1) ──────► doc_nodes (N)
     │                                              │
     │                                              │
     └──────► document_chunks (N) ◄─────────────────┘
                     │
                     └──────► chunk_embeddings (1)
```

### 4.3 切分策略（这次补充，落地版）

主公，这里把“怎么切”说成执行规则，后面开发直接照这个来，不再拍脑袋。

#### 规则 1：先按结构切，再按内容切

1. 先按标题层级建 `doc_nodes`（章/节/小节）。
2. 每个节点内部再做内容切分，产出 `document_chunks`。
3. `chunk` 必须挂 `node_id`，保证后续能回溯“这个片段属于哪一章”。

#### 规则 2：优先保证语义完整，不硬砍

切分顺序：

1. 先按段落切；
2. 段落太长再按句子切；
3. 只有句子仍过长才按字数兜底切。

默认参数（建议）：

- `chunk_size = 400`（先用这个跑）
- `overlap = 50`（避免上下文断层）
- `min_chunk_size = 120`（太短就和前后合并）
- `max_chunk_size = 800`（防止单块过大影响召回）

#### 规则 3：不同内容类型用不同切法

- **正文段落**：按段落 + 句号边界切。
- **列表/步骤**：同一组步骤优先放在同一个 chunk。
- **表格内容**：按“行组”切，不要把同一行拆开。
- **代码/配置块**：代码块整体保留，避免切半导致不可读。

#### 规则 4：问答链路和切分策略联动

- **没选文档范围时**：走普通聊天，不触发 RAG 检索（也就不需要临时做 embedding 检索）。
- **选了文档范围时**：只在这些文档对应节点/片段里检索，再融合向量结果。
- 这样可以避免“普通对话也被强制走检索链路”的误伤。

#### 规则 5：每个 chunk 的 metadata 必填

每条 chunk 至少带：

- `node_id`
- `node_path`（例如 `第一章 > 1.2 > 1.2.3`）
- `level`
- `page_start/page_end`
- `char_start/char_end`
- `chunk_index`

这样前端展示引用时，能直接告诉用户“答案来自哪一章哪一页”。

---

## 五、核心代码实现

### 5.1 文档解析器

**文件路径**: `python-service/app/domain/document_parser.py`

```python
"""智能文档解析器 - 保留文档结构"""

from dataclasses import dataclass
from typing import Any
import re
from uuid import uuid4

@dataclass
class DocNode:
    """文档节点"""
    node_id: str
    level: int          # 0=根, 1=章, 2=节, 3=小节
    index: int          # 同级排序
    title: str
    content: str        # 该节点的原始内容
    page_start: int | None = None
    page_end: int | None = None
    char_start: int = 0
    char_end: int = 0
    children: list["DocNode"] = None

    def __post_init__(self):
        if self.children is None:
            self.children = []


class DocumentParser:
    """智能文档解析器"""

    # 标题匹配模式（Markdown 格式）
    HEADING_PATTERNS = [
        (r"^#{1}\s+(.+)$", 1),      # # 一级标题
        (r"^#{2}\s+(.+)$", 2),      # ## 二级标题
        (r"^#{3}\s+(.+)$", 3),      # ### 三级标题
        (r"^#{4}\s+(.+)$", 4),      # #### 四级标题
    ]

    def parse_markdown(self, content: str, filename: str = "") -> DocNode:
        """
        解析 Markdown 文档，构建节点树

        大白话解释：
        1. 按行扫描文档
        2. 遇到标题就创建新节点
        3. 标题下面的内容归到这个节点
        4. 根据标题级别建立父子关系
        """
        lines = content.split("\n")
        root = DocNode(
            node_id=str(uuid4()),
            level=0,
            index=0,
            title=filename or "文档根节点",
            content="",
            char_start=0,
            char_end=len(content)
        )

        # 用栈来追踪当前路径上的节点
        # 栈[0] = 根节点, 栈[1] = 当前一级标题, ...
        stack: list[DocNode] = [root]
        current_content_start = 0
        char_pos = 0

        for i, line in enumerate(lines):
            char_pos = sum(len(l) + 1 for l in lines[:i])  # +1 for \n
            matched = False

            for pattern, level in self.HEADING_PATTERNS:
                match = re.match(pattern, line)
                if match:
                    # 找到标题了！
                    title = match.group(1).strip()

                    # 把上一个节点的内容结束位置记录下来
                    if len(stack) > 1:
                        stack[-1].char_end = char_pos
                        stack[-1].content = content[stack[-1].char_start:char_pos]

                    # 创建新节点
                    new_node = DocNode(
                        node_id=str(uuid4()),
                        level=level,
                        index=len(stack[level].children) if level < len(stack) else 0,
                        title=title,
                        content="",
                        char_start=char_pos + len(line) + 1,  # 标题后面开始
                        char_end=len(content)
                    )

                    # 调整栈，找到正确的父节点
                    while len(stack) > level:
                        stack.pop()

                    # 添加到父节点
                    stack[-1].children.append(new_node)
                    stack.append(new_node)
                    matched = True
                    break

            if not matched and len(stack) > 1:
                # 普通内容行
                pass

        # 处理最后一个节点
        if len(stack) > 1:
            stack[-1].char_end = len(content)
            stack[-1].content = content[stack[-1].char_start:len(content)]

        return root

    def parse_pdf(self, file_bytes: bytes, filename: str = "") -> DocNode:
        """
        解析 PDF 文档

        大白话解释：
        1. 用 pdfplumber 逐页读取
        2. 识别大号字体作为标题
        3. 构建节点树

        注意：PDF 解析比较复杂，可能需要 OCR
        """
        # TODO: 实现PDF解析
        # 可以用 pdfplumber 或 PyMuPDF
        pass

    def get_all_chunks(
        self,
        root: DocNode,
        chunk_size: int = 400,
        overlap: int = 50
    ) -> list[dict]:
        """
        从节点树生成切片

        大白话解释：
        遍历树的每个节点，把节点的内容按语义切分，
        同时记录每个切片属于哪个节点（这样检索时就知道它来自哪个章节）
        """
        chunks = []

        def traverse(node: DocNode, parent_path: list[str]):
            current_path = parent_path + [node.title]

            if node.content and node.content.strip():
                # 这个节点有内容，进行切分
                node_chunks = self._smart_split(
                    node.content,
                    chunk_size,
                    overlap
                )
                for i, chunk in enumerate(node_chunks):
                    chunks.append({
                        "content": chunk,
                        "node_id": node.node_id,
                        "node_path": " > ".join(current_path),  # 完整路径，如 "产品介绍 > iPhone > iPhone 15 Pro"
                        "level": node.level,
                        "char_start": node.char_start,
                        "char_end": node.char_end,
                    })

            # 递归处理子节点
            for child in node.children:
                traverse(child, current_path)

        traverse(root, [])
        return chunks

    def _smart_split(
        self,
        content: str,
        chunk_size: int,
        overlap: int
    ) -> list[str]:
        """
        智能切分：尽量按句子边界切，不要把句子切断

        大白话解释：
        1. 先按段落/句子把内容拆开
        2. 然后把小块拼成大块，每块不超过 chunk_size
        3. 这样就不会把一句话切成两半了
        """
        # 按段落分割
        paragraphs = re.split(r'\n\s*\n', content)

        chunks = []
        current_chunk = ""
        current_size = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 如果段落本身超长，按句子切
            if len(para) > chunk_size:
                sentences = re.split(r'([。！？\n])', para)
                sentences = [''.join(i) for i in zip(sentences[0::2], sentences[1::2] + [''])]

                for sentence in sentences:
                    if current_size + len(sentence) > chunk_size and current_chunk:
                        chunks.append(current_chunk.strip())
                        # overlap: 保留最后一点内容
                        overlap_text = current_chunk[-overlap:] if overlap > 0 else ""
                        current_chunk = overlap_text + sentence
                        current_size = len(current_chunk)
                    else:
                        current_chunk += sentence
                        current_size += len(sentence)
            else:
                # 段落不长，直接加
                if current_size + len(para) + 2 > chunk_size and current_chunk:
                    chunks.append(current_chunk.strip())
                    overlap_text = current_chunk[-overlap:] if overlap > 0 else ""
                    current_chunk = overlap_text + para
                    current_size = len(current_chunk)
                else:
                    if current_chunk:
                        current_chunk += "\n\n" + para
                    else:
                        current_chunk = para
                    current_size = len(current_chunk)

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks
```

### 5.2 PageIndex 服务

**文件路径**: `python-service/app/domain/pageindex_service.py`

```python
"""PageIndex 索引服务 - 文档结构化管理"""

import json
from uuid import uuid4
from typing import Any

from app.core.database import get_db_conn
from app.domain.embedding import EmbeddingService
from app.domain.document_parser import DocNode, DocumentParser


class PageIndexService:
    """PageIndex 服务"""

    def __init__(self):
        self.parser = DocumentParser()

    async def build_index(
        self,
        document_id: str,
        content: str,
        filename: str,
        conn,
        embedding_model_id: str = "text-embedding-3-large",
        registry = None,
    ) -> dict:
        """
        为文档构建 PageIndex 索引

        大白话解释：
        1. 解析文档，得到节点树
        2. 把节点树存到数据库
        3. 对每个节点生成摘要和向量
        4. 对内容进行切分，关联到节点
        5. 对切片生成向量
        """

        # 1. 解析文档
        root = self.parser.parse_markdown(content, filename)

        # 2. 创建树记录
        tree_id = str(uuid4())
        await conn.execute(
            """
            INSERT INTO doc_trees (tree_id, document_id, title)
            VALUES ($1, $2, $3)
            """,
            tree_id, document_id, filename
        )

        # 3. 保存节点树（递归）
        node_count = await self._save_nodes(
            conn, tree_id, root, None, 0, 0
        )

        # 4. 更新树统计
        await conn.execute(
            """
            UPDATE doc_trees SET total_nodes = $1 WHERE tree_id = $2
            """,
            node_count, tree_id
        )

        # 5. 获取所有切片
        chunks = self.parser.get_all_chunks(root, chunk_size=400, overlap=50)

        # 6. 保存切片和向量
        for i, chunk in enumerate(chunks):
            # 保存切片
            chunk_id = await conn.fetchval(
                """
                INSERT INTO document_chunks
                (document_id, chunk_index, content, metadata, node_id)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id::text
                """,
                document_id,
                i + 1,
                chunk["content"],
                json.dumps({
                    "node_path": chunk["node_path"],
                    "level": chunk["level"],
                }, ensure_ascii=False),
                chunk["node_id"]
            )

            # 生成向量
            if registry:
                embedding, _ = await EmbeddingService.embed_single_with_usage(
                    chunk["content"], embedding_model_id, registry
                )
                await conn.execute(
                    """
                    INSERT INTO chunk_embeddings (chunk_id, embedding, model_id)
                    VALUES ($1, $2, $3)
                    """,
                    chunk_id, embedding, embedding_model_id
                )

        return {
            "tree_id": tree_id,
            "total_nodes": node_count,
            "total_chunks": len(chunks),
        }

    async def _save_nodes(
        self,
        conn,
        tree_id: str,
        node: DocNode,
        parent_db_id: str | None,
        level: int,
        index: int
    ) -> int:
        """
        递归保存节点到数据库

        大白话解释：
        一个一个节点存，存完爸爸存儿子，递归下去
        """
        # 生成节点摘要（简单版：取内容前 200 字）
        summary = node.content[:200] if node.content else None

        db_id = await conn.fetchval(
            """
            INSERT INTO doc_nodes
            (tree_id, parent_id, node_level, node_index, title,
             char_start, char_end, summary, content_preview)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id::text
            """,
            tree_id,
            parent_db_id,
            level,
            index,
            node.title,
            node.char_start,
            node.char_end,
            summary,
            node.content[:100] if node.content else None
        )

        count = 1

        # 递归保存子节点
        for i, child in enumerate(node.children):
            count += await self._save_nodes(
                conn, tree_id, child, db_id, level + 1, i
            )

        return count

    async def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        conn,
        top_k: int = 5,
        min_score: float = 0.5,
        document_ids: list[str] = None,
    ) -> list[dict]:
        """
        混合检索：PageIndex 路由 + 向量检索

        大白话解释：
        1. 先用查询向量在节点里找最相关的章节（路由）
        2. 同时在切片里做向量检索
        3. 如果切片属于命中的章节，加分
        4. 综合排序返回结果
        """

        # 路由：找最相关的节点
        node_sql = """
            SELECT
                id::text as node_id,
                title,
                node_level,
                summary,
                1 - (embedding <=> $1::vector) as score
            FROM doc_nodes
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT 10
        """
        relevant_nodes = await conn.fetch(node_sql, query_embedding)
        node_scores = {n["node_id"]: n["score"] for n in relevant_nodes}

        # 向量检索：找最相关的切片
        chunk_sql = """
            SELECT
                dc.id::text AS chunk_id,
                dc.document_id::text AS document_id,
                dc.content,
                dc.metadata,
                dc.node_id,
                dn.title as node_title,
                dn.node_level,
                1 - (ce.embedding <=> $1::vector) as vector_score
            FROM chunk_embeddings ce
            JOIN document_chunks dc ON ce.chunk_id = dc.id
            LEFT JOIN doc_nodes dn ON dc.node_id = dn.id::text
            WHERE 1 - (ce.embedding <=> $1::vector) >= $2
            ORDER BY ce.embedding <=> $1::vector
            LIMIT $3
        """
        chunks = await conn.fetch(chunk_sql, query_embedding, min_score, top_k * 2)

        # 融合打分
        results = []
        for chunk in chunks:
            vector_score = chunk["vector_score"]
            node_id = chunk["node_id"]

            # 如果切片属于命中的节点，加分
            node_boost = 0
            if node_id and node_id in node_scores:
                node_boost = node_scores[node_id] * 0.3  # 30% 加成

            final_score = min(vector_score + node_boost, 1.0)

            results.append({
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "content": chunk["content"],
                "node_path": chunk["metadata"].get("node_path", ""),
                "node_title": chunk["node_title"],
                "score": final_score,
                "vector_score": vector_score,
                "node_boost": node_boost,
            })

        # 排序并返回 top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    async def get_node_tree(self, document_id: str, conn) -> dict:
        """
        获取文档的节点树（用于前端展示）
        """
        tree = await conn.fetchrow(
            """
            SELECT tree_id, title, total_nodes
            FROM doc_trees
            WHERE document_id = $1
            """,
            document_id
        )

        if not tree:
            return None

        nodes = await conn.fetch(
            """
            SELECT
                id::text, parent_id::text, node_level, node_index,
                title, summary, char_start, char_end
            FROM doc_nodes
            WHERE tree_id = $1
            ORDER BY node_level, node_index
            """,
            tree["tree_id"]
        )

        # 构建树结构
        node_map = {n["id"]: dict(n) for n in nodes}
        root = None

        for node in nodes:
            if node["parent_id"] is None:
                root = node_map[node["id"]]
            else:
                parent = node_map.get(node["parent_id"])
                if parent:
                    if "children" not in parent:
                        parent["children"] = []
                    parent["children"].append(node_map[node["id"]])

        return {
            "tree_id": tree["tree_id"],
            "title": tree["title"],
            "total_nodes": tree["total_nodes"],
            "root": root,
        }
```

### 5.3 修改现有的 RAG 服务

**文件路径**: `python-service/app/domain/rag_service.py`

在现有的 `ask` 方法中，把向量检索改成混合检索：

```python
# 原来：
results = await VectorStore.similarity_search(conn, embedding, top_k, min_score)

# 改成：
from app.domain.pageindex_service import PageIndexService
results = await PageIndexService().hybrid_search(
    question, embedding, conn, top_k, min_score, document_ids
)
```

---

## 六、实现步骤

### 第一步：创建数据库表（DDL）

1. 修改 `infra/postgres/init/001_init_schema.sql`
2. 添加 `doc_trees` 和 `doc_nodes` 表
3. 给 `document_chunks` 表添加 `node_id` 字段

### 第二步：实现文档解析器

1. 创建 `python-service/app/domain/document_parser.py`
2. 实现 Markdown 解析（最简单，先做这个）
3. 后续可扩展 PDF、Word 解析

### 第三步：实现 PageIndex 服务

1. 创建 `python-service/app/domain/pageindex_service.py`
2. 实现 `build_index` 方法
3. 实现 `hybrid_search` 方法

### 第四步：实现文档处理 Worker

1. 创建 `python-service/app/workers/document_worker.py`
2. 消费 RabbitMQ 队列
3. 调用 PageIndex 服务处理文档

### 第五步：修改 RAG 服务

1. 在 `rag_service.py` 中使用混合检索
2. 在返回结果中包含节点路径信息

### 第六步：添加前端展示

1. 在文档详情页显示节点树
2. 在聊天结果中显示内容来源章节

---

## 七、验证方案

### 7.1 单元测试

```python
# 测试文档解析
def test_parse_markdown():
    content = """
# 第一章 产品介绍

这是产品介绍的内容。

## 1.1 iPhone

iPhone 是苹果公司的手机产品。

### 1.1.1 iPhone 15 Pro

iPhone 15 Pro 是最新的旗舰机型。
"""
    parser = DocumentParser()
    root = parser.parse_markdown(content, "测试文档")

    assert root.title == "测试文档"
    assert len(root.children) == 1  # 第一章
    assert root.children[0].title == "第一章 产品介绍"
    assert len(root.children[0].children) == 1  # 1.1 iPhone
```

### 7.2 集成测试

```bash
# 1. 上传一个 Markdown 文档
curl -X POST http://localhost:8090/api/v1/documents/upload \
  -F "file=@test.md"

# 2. 检查文档树是否正确生成
curl http://localhost:8090/api/v1/documents/{doc_id}/tree

# 3. 测试检索
curl -X POST http://localhost:8090/api/v1/chat/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "iPhone 15 Pro 的特点是什么？", "modelId": "gpt-4"}'

# 4. 验证返回结果中包含 node_path 信息
```

### 7.3 效果对比

准备一个包含多个章节的测试文档，对比：

| 指标 | 传统切分 | PageIndex |
|------|----------|-----------|
| 跨章节问题准确率 | ? | ? |
| 返回结果相关性 | ? | ? |
| 上下文完整性 | ? | ? |

---

## 八、后续优化方向

1. **LLM 生成摘要**：用 LLM 为每个节点生成更好的摘要
2. **PDF 智能解析**：识别 PDF 中的标题层级
3. **多模态支持**：图片、表格的提取和索引
4. **增量更新**：文档修改后只更新变化的部分
5. **节点向量路由**：为每个节点的标题/摘要生成向量，用于更精准的路由

---

**文档创建时间**: 2026-03-01

**相关文档**:
- [项目 CLAUDE.md](../CLAUDE.md)
- [后端开发记录](../backend/)

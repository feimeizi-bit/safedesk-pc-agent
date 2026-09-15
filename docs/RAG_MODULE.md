# SafeDesk RAG Module

SafeDesk 的 RAG 子模块负责知识检索，不负责决定是否执行桌面操作。模型提出
`query_knowledge_base` 工具调用后，检索层先返回候选答案、匹配分数和来源；只有
达到置信度阈值的候选才会作为知识回答返回。

## 调用边界

```python
from tools.rag_query import query_knowledge_base_detailed

result = query_knowledge_base_detailed("电脑运行缓慢怎么办？")
```

结果包含：

- `answer`：当前最佳答案，或低置信度/无命中提示；
- `confidence`：候选匹配分数；
- `matched_question`：命中的知识库问句（问答格式文档可用）；
- `sources`：去重后的来源文件；
- `status`：`answered`、`low_confidence` 或 `no_match`；
- `retrieved_count`：归一化后的候选数量。

## 当前实现

- 默认使用持久化 Chroma 与中文 embedding；embedding 和向量库采用懒加载。
- 优先使用向量库提供的相关性分数；接口不可用时回退到普通相似度检索。
- 对“问：/答：”格式的知识块使用字符串相似度辅助排序，保证小规模本地知识库可解释。
- 分数低于 `PC_AGENT_RAG_MIN_CONFIDENCE`（默认 `0.35`）时主动拒答，避免把弱匹配片段交给模型。
- Agent 仍可使用 `query_knowledge_base()` 获取带来源后缀的纯文本，兼容既有 Tool Calling 协议。

## 后续验证

运行 `python -m evals.run_rag_eval` 可在离线确定性检索器上扫描阈值。当前 19 条样例
（15 条可回答、4 条负例）在阈值 0.35～0.45 时达到 Recall@2 93.3%、答案命中率
93.3%、precision 100%、负例拒答率 100%。其中 1 条同义改写因字符相似度不足未命中，
说明后续需要用真实 embedding 或增加同义词归一化；该离线结果不等价于生产向量库指标。

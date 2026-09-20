# MATCH AGAINST release-8.5 交付状态与 ETA

更新时间：2026-09-20  
本文档是当前唯一的交付状态基线。更早的设计和状态文档仅保留历史记录。

## 1. 结论

当前状态分为两部分：

| 范围 | 当前状态 | 交付判断 |
| --- | --- | --- |
| #70484/#70485：TiFlash Boolean MATCH 下推 | TiDB + tipb + TiFlash 链路已打通，本地 TiUP E2E 已通过 | 可交付研发测试环境，不建议直接作为正式版本发布 |
| #70486：无 FULLTEXT 索引时 TiDB 本地执行 | analyzer、matcher 和 local builtin 已移植，但无索引 E2E 仍报找不到 FULLTEXT index | 尚未完成 |

当前不能称为完整 MySQL `MATCH ... AGAINST` 兼容实现，因为评分结果、自然语言排名和 query expansion 不在本次范围内。

## 2. 分支和代码状态

### TiDB

- 路径：`/Users/solotzg/Work/tidb-2`
- 分支：`match_against-release-8.5`
- HEAD：`b5224ea7e0`
- 二进制：`v8.5.8-27-gb5224ea7e0`
- 当前有 4 个未提交的 planner/protocol 修复：
  - `pkg/planner/core/expression_rewriter.go`
  - `pkg/planner/core/find_best_task.go`
  - `pkg/planner/core/operator/logicalop/logical_datasource.go`
  - `pkg/planner/core/plan_to_pb.go`

### TiFlash

- 路径：`/Users/solotzg/Work/tiflash`
- 分支：`match_against`
- HEAD：`bd4f78a06c`
- 二进制：`v8.5.8-2-gbd4f78a06c`
- 当前 tracked 修改：
  - `dbms/src/TiDB/Schema/TiDB.cpp`：处理 tipb 传入的 signed collation ID；
  - `libs/libcommon/include/common/demangle.h`：已有的 `<cstdlib>` 修复。
- `.vscode/build-debug.sh` 的外部 proxy 配置修改被 `.gitignore` 忽略，属于本地构建脚本修改。

### tipb

- TiFlash submodule：`0a9c803d4e7b`
- 分支：`release-8.5-match-against`
- TiDB 与 TiFlash 必须继续使用同一个 tipb commit。

## 3. #70484/#70485 已验证能力

当前 native TiFlash 下推需要同时满足：

```text
direct WHERE/HAVING/ON Boolean MATCH
AND 单列 MATCH
AND public STANDARD_V1 FULLTEXT index
AND TiFlash replica AVAILABLE=1
AND 默认 analyzer 配置
AND constant search string
```

已验证：

- `+MySQL -tutorial` 返回 `2,3,4,5`；
- `+PostgreSQL` 返回 `4`；
- 无匹配词返回 `0`；
- `COUNT(*)` 返回 `4`；
- 执行计划包含 `mpp[tiflash]`；
- TiFlash production path 已接入 Boolean AST、required/prohibited term、phrase、prefix 和 NULL 文档处理；其中 phrase、prefix、NULL 仍需纳入最终 runtime 差分验收，不能仅以源码测试代替。

关键修复包括：

1. TiDB expression rewriter 增加 `ast.MatchAgainst` 分发；
2. 逻辑和物理 column pruning 保留 TiFlash FTS 所需的文档列；
3. table-scan protocol 边界补齐被聚合算子裁剪掉的 FTS 列；
4. TiFlash 支持 tipb signed collation ID。

## 4. #70486 当前状态

#70486 的目标是：没有 FULLTEXT 索引时，在 TiDB classic kernel 中使用本地 analyzer 和 Boolean matcher 完成无评分过滤；它不需要 TiFlash、TiKV FTS 或 TiCI。

已经存在：

- `pkg/expression/fulltext` analyzer；
- `pkg/expression/matchagainst` Boolean parser；
- `FTSLocalEvalInfo` 和本地 `match_against` builtin；
- `tidb_enable_local_match_against` 开关；
- analyzer、query matcher 和 builtin 单元测试。

当前阻塞点：

```text
无 FULLTEXT 索引 + tidb_enable_local_match_against=ON
仍返回：Can't find FULLTEXT index matching the column list
```

原因是本地路径仍通过 `resolveLocalFullTextIndex` 获取 parser 配置，并把 FULLTEXT index 当作必需条件。无索引场景应该直接使用 `STANDARD_V1` 和 session analyzer 配置。

因此，#70486 当前是“基础实现已移植、触发路径未完成”，不能标记为完成。

## 5. 明确不在本次交付范围

- TiKV FTS 下推；
- TiCI；
- natural-language relevance score；
- `SELECT MATCH(...) AGAINST(...)` 评分结果；
- query expansion；
- 多列 TiFlash native MATCH；
- NGRAM/MULTILINGUAL TiFlash native path；
- 非默认 analyzer 配置的 TiFlash native path；
- 完整 MySQL Boolean 扩展、proximity 和复杂评分操作符。

例如以下语句当前仍会被拒绝，这是预期范围限制：

```sql
SELECT MATCH(title) AGAINST('MySQL') AS score FROM articles;
```

## 6. ETA

估算前提：1 名工程师全职投入，当前分支和本地构建环境可继续使用，不计算外部 code review、CI 排队和 release 冻结时间。

| 阶段 | 工作内容 | 预计耗时 | 预计完成 |
| --- | --- | ---: | --- |
| P0 | 修复 #70486 无索引 parser 选择和 local planner 触发条件 | 0.5–1 个工作日 | 2026-09-21 |
| P0 | 增加无索引 E2E：词边界、短词、短语、prefix、NULL、prepared query | 1 个工作日 | 2026-09-22 |
| P0 | 完成 #70484/#70485 TiDB-TiFlash 差分回归和 TiFlash gtest runtime | 1–2 个工作日 | 2026-09-24 |
| P1 | 完整构建、混合版本协议验证、schema/replica/restart 场景 | 1–2 个工作日 | 2026-09-26 |
| P1 | 清理未提交变更、补文档、推送分支、形成 release-8.5 测试包 | 1 个工作日 | 2026-09-28 |

### ETA 结论

- **研发测试包**：约 2 个工作日，目标 2026-09-22；
- **release-8.5 内部验收包**：约 3–4 个工作日，目标 2026-09-24～2026-09-25；
- **具备正式发布条件的候选版本**：约 5–6 个工作日，目标 2026-09-28～2026-09-29。

如果只交付 #70484/#70485 的 TiFlash Boolean 下推，可以跳过 #70486，内部验收 ETA 可缩短到 2–3 个工作日；如果要求完整 MySQL 评分和自然语言模式，则不属于当前 ETA，需要另立项目。

## 7. 交付验收标准

### #70484/#70485

- TiDB、TiFlash、tipb 使用一致 commit；
- TiDB 和 TiFlash 完整构建通过；
- TiFlash gtest runtime 通过；
- 真实 TiUP Playground E2E 通过；
- required、optional、prohibited、phrase、prefix、NULL 和聚合场景通过；
- 无索引、非默认 parser、非默认 analyzer 不会错误 native 下推；
- 工作树清理并形成可审查 commit/PR。

### #70486

- 无 FULLTEXT 索引表在开关开启时可执行 Boolean MATCH；
- 词边界与 token size 行为通过差分测试；
- phrase、prefix、required/prohibited、NULL 和 prepared query 通过；
- 无索引路径只在 TiDB 本地执行，不发送到 TiFlash native FTS；
- 开关关闭时保持明确、兼容的错误或既有 fallback 行为；
- 文档明确 no-score 限制。

## 8. 当前交付建议

当前版本建议标记为：

> #70484/#70485：核心链路完成，测试环境可交付；  
> #70486：基础代码完成，无索引触发路径待修复；  
> 整体：暂不作为 release-8.5 正式生产版本发布。

历史文档：

- [TiDB/TiFlash 实现计划](./2026-09-18-match-against-tiflash-tidb.md)
- [旧版当前状态报告](./2026-09-19-match-against-current-status.md)
- [旧版 release-8.5 移植计划](./2026-09-20-tidb-release-8.5-fts-port-plan.md)

# MATCH AGAINST release-8.5 交付状态与 ETA

更新时间：2026-09-22
本文档是当前唯一的交付状态基线。更早的设计和状态文档仅保留历史记录。

## 1. 结论

当前状态分为两部分：

| 范围 | 当前状态 | 交付判断 |
| --- | --- | --- |
| #70484/#70485：TiFlash Boolean MATCH 下推 | TiDB + tipb + TiFlash 链路已打通，真实 TiUP native/fallback E2E 已通过 | 可交付研发测试环境；正式发布仍需完成 release 验收 |
| #70486：无 FULLTEXT 索引时 TiDB 本地执行 | 不属于本次 #70484/#70485 交付范围 | 不纳入本次 release 验收 |

当前不能称为完整 MySQL `MATCH ... AGAINST` 兼容实现，因为评分结果、自然语言排名和 query expansion 不在本次范围内。

## 2. 分支和代码状态

### TiDB

- 路径：`/Users/solotzg/Work/tidb-2`
- 分支：`match_against-release-8.5`
- HEAD：`19ec517d96`
- 本次 collation 修复已提交并推送到 `origin/match_against-release-8.5`。

### TiFlash

- 路径：`/Users/solotzg/Work/tiflash`
- 分支：`match_against`
- HEAD：`b6f1477fa1`
- `origin/match_against` 与本地一致，工作区干净。

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

- `+tidb -mysql` 返回 `id=2`；
- `+tidb` 在 `utf8mb4_general_ci` 下匹配 `id=1,2,4`；
- NULL 文档和空查询不会误匹配；
- 有 replica 时执行计划包含 `mpp[tiflash]`；
- 撤销 replica 后执行计划切换为 TiDB root `Selection -> cop[tikv]`，结果仍为 `id=2`；
- TiDB fallback 已按 MATCH 列 collation 处理大小写和重音。

关键修复包括：

1. TiDB expression rewriter 增加 `ast.MatchAgainst` 分发；
2. 逻辑和物理 column pruning 保留 TiFlash FTS 所需的文档列；
3. table-scan protocol 边界补齐被聚合算子裁剪掉的 FTS 列；
4. TiFlash 支持 tipb signed collation ID。

## 4. FULLTEXT 元数据边界

本次 #70484/#70485 交付要求 MATCH 列被 public STANDARD_V1 FULLTEXT index
覆盖。该索引在当前阶段是 TiDB planner 的元数据和 parser 配置来源；TiDB
fallback 不读取或维护物理 FULLTEXT 倒排索引，而是扫描普通表数据并在 TiDB
内执行 analyzer 和 Boolean matcher。

因此，没有 FULLTEXT 索引元数据时返回：

```text
Can't find FULLTEXT index matching the column list
```

属于预期行为，不是本次交付缺陷。无索引时直接执行 Boolean MATCH 属于
#70486，当前不纳入 release-8.5 交付范围。

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
| P0 | 完成 #70484/#70485 TiDB-TiFlash 差分回归和 TiFlash gtest runtime | 1–2 个工作日 | 待 CI/环境确认 |
| P1 | 完整构建、混合版本协议验证、schema/replica/restart 场景 | 1–2 个工作日 | 待 CI/环境确认 |
| P1 | 形成 release-8.5 测试包并完成 code review | 1 个工作日 | 待评审排期 |

### ETA 结论

- **研发测试包**：核心 native/fallback E2E 已具备；
- **release-8.5 内部验收包**：取决于 clean build、gtest、差分回归和 code review；
- **正式发布候选版本**：不能仅由当前手工 E2E 单独确认。

当前只交付 #70484/#70485 的 TiFlash Boolean 下推；#70486、完整 MySQL 评分和自然语言模式均需要另立范围。

## 7. 交付验收标准

### #70484/#70485

- TiDB、TiFlash、tipb 使用一致 commit；
- TiDB 和 TiFlash 完整构建通过；
- TiFlash gtest runtime 通过；
- 真实 TiUP Playground E2E 通过；
- required、optional、prohibited、phrase、prefix、NULL 和聚合场景通过；
- public FULLTEXT 元数据缺失时保持预期错误；
- 非默认 parser、非默认 analyzer 不会错误 native 下推；
- 工作树清理并形成可审查 commit/PR。

### #70486

本次 release-8.5 交付不验收 #70486。无 FULLTEXT 索引时直接执行 Boolean
MATCH 的行为保持为后续独立工作项。

## 8. 当前交付建议

当前版本建议标记为：

> #70484/#70485：TiDB、tipb、TiFlash native/fallback 核心链路完成，真实
> TiUP 测试环境已验证；
> #70486：不属于本次 release-8.5 交付范围；
> 整体：正式发布仍需完成 clean build、gtest、差分回归和 code review。

历史文档：

- [TiDB/TiFlash 实现计划](./2026-09-18-match-against-tiflash-tidb.md)
- [旧版当前状态报告](./2026-09-19-match-against-current-status.md)
- [旧版 release-8.5 移植计划](./2026-09-20-tidb-release-8.5-fts-port-plan.md)

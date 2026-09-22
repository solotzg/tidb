# release-8.5 `MATCH ... AGAINST` TiFlash 下推实现与验证

更新时间：2026-09-22
适用范围：TiDB `release-8.5`、TiFlash `release-8.5`、tipb 协议
目标语法：`MATCH(col) AGAINST('+tidb -mysql' IN BOOLEAN MODE)`

本文是当前实现的统一说明，汇总目标、执行链路、代码入口、分支提交、
验证结果和交付边界。早期设计和状态文档仍保留为历史记录，不能替代本文。

FULLTEXT 索引在本阶段是 SQL/planner 的元数据前置条件：表上必须存在
public FULLTEXT index，TiDB 才会接受对应的 MATCH 列并取得 parser 配置。
TiDB fallback 不读取或维护物理 FULLTEXT 倒排索引，而是通过普通表扫描在
TiDB 内执行 no-score analyzer 和 Boolean matcher。

## 1. 交付目标

本次只交付 Boolean Mode 的过滤语义：

```sql
SELECT *
FROM articles
WHERE MATCH(title) AGAINST('+tidb -mysql' IN BOOLEAN MODE);
```

执行策略：

```text
TiFlash replica 可用且查询满足 native 条件
    -> TiDB 生成 Boolean AST
    -> tipb.FTSQueryInfo.boolean_query
    -> TiFlash 原生 FTS 算子

否则
    -> TiDB 本地 analyzer + Boolean matcher
    -> 返回 0/1 过滤结果
```

明确不包含 TiKV、TiCI、自然语言 relevance score、query expansion，
也不把 `SELECT MATCH(...) AGAINST(...)` 的评分结果作为本次交付目标。

## 2. 当前结论

当前代码已经形成完整的 TiDB → tipb → TiFlash Boolean FTS 链路，并完成了
本地单元测试、planner/protocol 编译检查和 TiUP Playground 场景验证。

已覆盖的核心行为：

- `+term`：required term；
- `-term`：prohibited term；
- 普通 term：optional term；
- phrase：`"tidb database"`；
- prefix：`mysql*`；
- NULL 文档和无匹配词；
- 聚合前的 Boolean 过滤；
- TiFlash replica 可用时的 `mpp[tiflash]` 计划；
- 没有 TiFlash replica 时的 TiDB 本地 no-score 计算。
- TiDB fallback 按 MATCH 列 collation 执行大小写和重音比较，包括
  `utf8mb4_bin`、`utf8mb4_general_ci` 和 `utf8mb4_0900_ai_ci`。

当前仍需作为正式 release-8.5 发布前置条件完成的工作：

1. TiDB 与 TiFlash analyzer 的跨引擎差分测试；
2. TiFlash 完整 gtest runtime 及干净环境下的完整构建；
3. 混合版本、TiFlash 重启、replica rebuild、schema change 场景；
4. 性能、并发、资源上限和回滚策略；
5. 将本次范围、限制和 feature gate 加入正式 release 文档。

## 3. 代码和分支

### 3.1 TiDB

- 分支：[match_against-release-8.5](https://github.com/solotzg/tidb/tree/match_against-release-8.5)
- 关键提交：[19ec517d96](https://github.com/solotzg/tidb/commit/19ec517d96)
- 目标基线：[AilinKid/tidb codex/local-fts-jsm-release-8.5](https://github.com/AilinKid/tidb/tree/codex/local-fts-jsm-release-8.5)

### 3.2 TiFlash

- 分支：[match_against](https://github.com/solotzg/tiflash/tree/match_against)
- 当前提交：[b6f1477fa1](https://github.com/solotzg/tiflash/commit/b6f1477fa1)
- 分支包含 Boolean 下推、NULL 列处理和设计文档更新；基于 `release-8.5`。

### 3.3 tipb

- 协议提交：[0a9c803d4e7b](https://github.com/solotzg/tipb/commit/0a9c803d4e7bf37a7d67a3e74609b8dd56399d09)
- 基于 `release-8.5`，增加 Boolean query message 和
  `used_columnar_indexes` 对应字段；TiDB 和 TiFlash 必须使用同一个 tipb
  commit。

## 4. 执行链路

```text
SQL parser
  -> expression_rewriter.matchAgainstToExpression
  -> FTSInfo / FTSLocalEvalInfo
  -> ftsNativeViable 判断 TiFlash native 条件
  -> FullTextIndexResolverWhere
  -> tipb.FTSQueryInfo.boolean_query
  -> TableScan.used_columnar_indexes
  -> TiFlash TiDBTableScan / PhysicalTableScan
  -> fts_match_expression
  -> TiFlash C++ Boolean matcher
```

native path 和 local path 的职责不同：

| 路径 | 作用 | 结果 | 典型条件 |
| --- | --- | --- | --- |
| TiFlash native | 使用 TiFlash FTS 扫描/过滤 | 0/1 过滤语义 | replica 可用、Boolean AST 可编码、存储侧能力满足 |
| TiDB local | 在 TiDB executor 内分析文本并匹配 | 0/1 过滤语义 | 没有 replica，或 native 条件不满足 |
| score / natural-language | 本次不交付 | 不承诺 | 需要另立功能范围 |

`tidb_enable_local_match_against` 不是 TiFlash 下推开关；它属于 TiDB 本地
执行路径的兼容配置。关闭本地路径不会阻止满足条件的 TiFlash native path，
也不会把不支持的评分场景变成 Boolean 过滤场景。

## 5. TiDB 代码入口

| 文件 | 作用 | 链接 |
| --- | --- | --- |
| `pkg/planner/core/expression_rewriter.go` | 识别 `MATCH ... AGAINST`，选择 native 或 local evaluator，校验 Boolean 查询 | [expression_rewriter.go#L2263-L2480](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/expression_rewriter.go#L2263-L2480) |
| `pkg/planner/core/fts_resolve_index.go` | 从 direct predicate 生成 `FTSQueryInfo` 和 Boolean AST | [fts_resolve_index.go#L64-L170](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/fts_resolve_index.go#L64-L170) |
| `pkg/expression/builtin_fts.go` | TiDB 本地 no-score Boolean evaluator | [builtin_fts.go](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/expression/builtin_fts.go) |
| `pkg/expression/fts_helper.go` | 解释 FTS scalar expression 并提取元数据 | [fts_helper.go#L48-L101](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/expression/fts_helper.go#L48-L101) |
| `pkg/expression/fts_tipb.go` | 将 TiDB Boolean AST 转成 tipb Boolean AST | [fts_tipb.go#L25-L118](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/expression/fts_tipb.go#L25-L118) |
| `pkg/planner/core/operator/logicalop/logical_datasource.go` | 保存逻辑计划中的 FTS pushdown metadata | [logical_datasource.go#L52-L63](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/operator/logicalop/logical_datasource.go#L52-L63) |
| `pkg/planner/core/find_best_task.go` | 将 FTS metadata 绑定到 TiFlash TableScan | [find_best_task.go#L3014-L3018](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/find_best_task.go#L3014-L3018)、[find_best_task.go#L3518-L3522](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/find_best_task.go#L3518-L3522) |
| `pkg/planner/core/plan_to_pb.go` | 写入 TableScan/PartitionTableScan 的 `used_columnar_indexes` | [plan_to_pb.go#L289-L361](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/plan_to_pb.go#L289-L361) |
| `pkg/planner/core/optimizer.go` | 在普通 predicate pushdown 前运行 FTS resolver | [optimizer.go#L1038-L1045](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/planner/core/optimizer.go#L1038-L1045) |
| `pkg/sessionctx/variable/tidb_vars.go` | `tidb_enable_local_match_against` 声明和默认值 | [tidb_vars.go#L340-L350](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/pkg/sessionctx/variable/tidb_vars.go#L340-L350) |

## 6. TiFlash 代码入口

| 文件 | 作用 | 链接 |
| --- | --- | --- |
| `dbms/src/Functions/FunctionsFullText.cpp` | Boolean query 解码、term/phrase/prefix/required/prohibited 匹配 | [FunctionsFullText.cpp#L337-L745](https://github.com/solotzg/tiflash/blob/match_against/dbms/src/Functions/FunctionsFullText.cpp#L337-L745) |
| `dbms/src/Flash/Coprocessor/TiDBTableScan.cpp` | 从 TableScan 请求提取 FTS query | [TiDBTableScan.cpp#L22-L60](https://github.com/solotzg/tiflash/blob/match_against/dbms/src/Flash/Coprocessor/TiDBTableScan.cpp#L22-L60) |
| `dbms/src/Flash/Planner/Plans/PhysicalTableScan.cpp` | 将 FTS metadata 构造为 TiFlash scalar expression | [PhysicalTableScan.cpp#L42-L96](https://github.com/solotzg/tiflash/blob/match_against/dbms/src/Flash/Planner/Plans/PhysicalTableScan.cpp#L42-L96) |
| `dbms/src/Flash/Coprocessor/DAGUtils.cpp` | 注册 `fts_match_expression` 函数 | [DAGUtils.cpp#L459](https://github.com/solotzg/tiflash/blob/match_against/dbms/src/Flash/Coprocessor/DAGUtils.cpp#L459) |
| `dbms/src/Functions/tests/gtest_fulltext.cpp` | TiFlash FTS matcher 单元测试 | [gtest_fulltext.cpp](https://github.com/solotzg/tiflash/blob/match_against/dbms/src/Functions/tests/gtest_fulltext.cpp) |

当前 TiFlash 通过内部 marker 参数把序列化 Boolean AST 传入
`fts_match_expression`。这是为了复用现有 scalar function 调用链的过渡方案；
后续若要将协议产品化，应评估把 Boolean AST 作为正式执行参数而不是 marker
字符串传递。

## 7. tipb 协议内容

TiDB 发送的核心信息包括：

- `FTSQueryInfo.query_func = FTSMatchExpression`；
- `query_text`；
- `query_tokenizer`；
- MATCH 列的 column metadata；
- `FTSQueryInfo.boolean_query`；
- TiFlash table scan 的 `used_columnar_indexes`。

Boolean AST 中包含 term、phrase、prefix 以及 required、optional、prohibited
等节点。TiFlash 不重新解析 SQL 字符串，而是消费 TiDB 已解析的 AST，从而避免
两端 Boolean 语法解析不一致。

## 8. 验证记录

### 8.1 TiDB 单元和编译检查

已通过：

```bash
go test --tags=intest ./pkg/expression \
  -run 'Test(FTSMysqlMatchAgainst|BuildFTS)' -count=1

go test --tags=intest ./pkg/planner/core \
  -run '^TestNonExistent$' -count=1
```

第一条覆盖本地 evaluator 和 Boolean AST 构造；第二条用于确认 planner core
在当前 tipb 协议下可以编译链接。

### 8.2 integration test

已有 TiDB FTS 单元测试、planner 编译检查和本地 fallback 手工 E2E，覆盖：

- Boolean term、required/prohibited term；
- phrase、prefix、NULL；
- prepared query；
- local fallback；
- native/local 路径选择。

官方 `run-tests.sh` runner 在当前环境缺少 `mysql_tester` 依赖且无法访问
`proxy.golang.org`，因此不能把完整 integration test runner 结果宣称为本次
验收证据；真实 TiUP 集群验证结果见下一节。

测试入口：[fulltext_search.test](https://github.com/solotzg/tidb/blob/codex/local-fts-jsm-release-8.5-match-against/tests/integrationtest/t/planner/core/fulltext_search.test)

### 8.3 TiUP Playground + TiFlash

已验证场景：

1. 创建带 public FULLTEXT index 元数据的测试表；
2. 设置 `TIFLASH REPLICA 1`，等待 `AVAILABLE=1`、`PROGRESS=1`；
3. 关闭 `tidb_enable_local_match_against`，执行
   `MATCH(body) AGAINST('+tidb -mysql' IN BOOLEAN MODE)`；
4. `EXPLAIN` 出现 `mpp[tiflash]`，查询返回 `id=2`；
5. `EXPLAIN ANALYZE` 显示 TiFlash 扫描 5 行、输出 1 行；
6. 将 replica 设置为 0 并开启 `tidb_enable_local_match_against`；
7. 计划切换为 TiDB root `Selection -> cop[tikv]`，结果仍为 `id=2`。

#### 实际 `EXPLAIN` 证据

测试 SQL：

```sql
EXPLAIN
SELECT id
FROM articles
WHERE MATCH(body) AGAINST('+tidb -mysql' IN BOOLEAN MODE);
```

实际记录的关键输出是：

```text
mpp[tiflash] TableFullScan
```

完整输出中的算子编号和估算行数会随表结构、统计信息和优化器状态变化，
典型形态如下：

```text
+-------------------+---------+--------------+---------------+----------------------+
| id                | estRows | task         | access object | operator info        |
+-------------------+---------+--------------+---------------+----------------------+
| TableFullScan_7   | 4.00    | mpp[tiflash] | table:articles| ...                  |
+-------------------+---------+--------------+---------------+----------------------+
```

`mpp[tiflash] TableFullScan` 只能证明查询任务选择了 TiFlash，不能单独证明
Boolean FTS metadata 已经传入 TableScan。最终下推证据还需要检查 TiFlash
日志，确认出现：

```text
used_columnar_indexes {
    index_type: TypeFulltext
    fts_query_info {
        query_func: FTSMatchExpression
        boolean_query { ... }
    }
}
```

因此，本次 E2E 对“已下推到 TiFlash”的判定标准是三项同时满足：

```text
EXPLAIN: mpp[tiflash] TableFullScan
+ TiFlash log: TypeFulltext / FTSMatchExpression / boolean_query
+ SQL 返回结果正确
```

这证明 `+tidb -mysql` 这个单列、常量、直接 WHERE Boolean 查询已经
真实进入 TiFlash native path；撤销 replica 后同一查询可以由 TiDB fallback
执行并保持结果一致。`mpp[tiflash]` 计划中的 MATCH 条件由 TiFlash scan
执行，不一定以独立的 root `Selection` 节点显示。

#### E2E 覆盖边界

当前 TiUP E2E 已验证 native path，但不能据此宣称所有 Boolean 语法都完成
生产级验收。phrase、prefix、NULL、prepared query、replica 重启/rebuild、
schema change 和大规模 TiDB/TiFlash analyzer 差分仍需要独立验收。19 个
`planner/core/fulltext_search` integration cases 主要覆盖 TiDB planner/local
路径；它们不能替代 TiFlash 真实 runtime 验证。

### 8.4 构建限制

TiFlash 增量构建曾在第三方 abseil 编译阶段被 macOS clang-18/CoreFoundation
SDK 的枚举声明错误阻断，错误发生在 FTS translation unit 之前。运行时验证
使用了已经成功生成的 TiFlash binary；因此“TiUP 场景已验证”与“从干净环境
完整构建通过”必须分开记录。

## 9. 交付边界和已知限制

### 本次交付

- Boolean Mode 过滤，不返回 relevance score；
- 单列 MATCH 的 native TiFlash 加速；
- TiFlash 不可用时 TiDB 本地 no-score fallback；
- 默认 analyzer 和已支持的 STANDARD Boolean 语法；
- tipb/TiDB/TiFlash 三方协议一致。

### 不在本次交付

- TiKV FTS；
- TiCI；
- natural-language mode 和 relevance score；
- query expansion；
- 完整 MySQL Boolean 扩展、proximity 和复杂评分操作符；
- NGRAM/MULTILINGUAL parser 的 TiFlash native path；
- 多列 MATCH 的 TiFlash native path；
- 非默认 analyzer 配置的 native path；
- 以 LIKE 近似结果替代真正 Boolean matcher。

## 10. 正式发布前检查清单

- [ ] TiDB、TiFlash、tipb 使用记录中的对应 commit；
- [ ] TiFlash clean build 和 gtest runtime 通过；
- [ ] TiDB local/native 结果与 TiFlash 结果完成差分测试；
- [ ] replica 不可用、重启、rebuild、schema change 场景通过；
- [ ] 混合版本协议兼容性通过；
- [ ] 对 query 长度、AST 节点数、token 数设置资源保护；
- [ ] 增加 native/local/fallback 的 EXPLAIN、日志和 metrics；
- [ ] 明确 feature gate、灰度和回滚方案；
- [ ] 对外文档明确“Boolean 过滤、不提供评分结果”的限制。

## 11. 历史文档

- [TiDB/TiFlash 初始实现计划](./2026-09-18-match-against-tiflash-tidb.md)
- [旧版当前状态报告](./2026-09-19-match-against-current-status.md)
- [旧版 release-8.5 交付状态](./2026-09-20-match-against-release-8.5-delivery-status.md)
- [旧版 release-8.5 移植计划](./2026-09-20-tidb-release-8.5-fts-port-plan.md)

# TiDB release-8.5 MATCH AGAINST FTS 移植计划与结果

> 历史移植计划。当前交付状态、#70486 缺口和 ETA 以
> [2026-09-20 交付状态与 ETA](./2026-09-20-match-against-release-8.5-delivery-status.md)
> 为准。

更新时间：2026-09-20

## 目标

在 TiDB `release-8.5` 上实现 #70484/#70485 范围内的 MySQL 兼容入口：

```sql
MATCH (column) AGAINST ('query' IN BOOLEAN MODE)
```

本计划明确不包含 TiKV、TiCI、PostgreSQL `tsvector/tsquery` 语法、自然语言
评分、query expansion，也不把超出 #70484/#70485 的完整 FTS 产品能力作为本次
交付目标。

## 当前结果

- TiDB 分支：`match_against-release-8.5`，直接基于
  `pingcap/release-8.5` 的 `2787a8f1136`。
- TiDB 当前提交：`c44ec5c6d3`。
- TiFlash 分支：`match_against`，当前提交 `bd4f78a06c`。
- tipb 分支：`release-8.5-match-against`，提交
  `0a9c803d4e7b`，已经推送到 `solotzg/tipb`。
- TiDB `go.mod` 使用远程 tipb：
  `github.com/solotzg/tipb v0.0.0-20260920053028-0a9c803d4e7b`。

TiDB 和 TiFlash 父仓库以及 tipb 协议分支均已推送；这不等于完成真实集群
E2E 交付，后者仍需单独验收。

## 已实现链路

```text
SQL MATCH ... AGAINST (... IN BOOLEAN MODE)
  -> TiDB parser / expression rewriter
  -> TiDB Boolean AST
  -> tipb.FTSQueryInfo.boolean_query
  -> TiDB TableScan.used_columnar_indexes
  -> TiFlash TableScan
  -> fts_match_expression
  -> TiFlash C++ Boolean matcher
```

### TiDB

已完成：

1. `pkg/expression/fulltext`：tokenizer、stopword、token 长度、文档 token
   position、Boolean term/phrase/prefix matcher。
2. `pkg/expression/matchagainst`：STANDARD Boolean syntax parser。
3. `pkg/expression/fts_tipb.go`：把 TiDB Boolean AST 转换为 tipb AST。
4. `pkg/planner/core/expression_rewriter.go`：只在原生能力足够时选择 TiFlash
   native builtin；否则走 local evaluator 或报不支持。
5. `pkg/planner/core/fts_resolve_index.go`：识别 direct WHERE Boolean MATCH，
   匹配 public FULLTEXT index，生成 `FTSQueryInfo`。
6. `pkg/planner/core/plan_to_pb.go`：把 FTS query 放入 TiFlash
   `used_columnar_indexes`。
7. metadata-only FULLTEXT DDL 生命周期：CREATE/ALTER/删除相关 metadata，
   不写 TiKV FTS 索引，不增加 TiCI 逻辑。
8. release-8.5 API 适配：使用 `pkg/sessionctx/variable`；使用 parser model 的
   `NewCIStr`；补充 TiFlash 约定的 virtual FTS score column ID。

### TiFlash

TiFlash 当前 `match_against` 实现已覆盖：

- DAG/TableScan 读取 FTS query metadata；
- PhysicalTableScan 构造 FTS builtin 参数；
- Boolean protocol marker 序列化/反序列化；
- UTF-8 基础 tokenization、默认 stopword、token 长度；
- required / optional / prohibited term；
- phrase position；
- prefix term；
- NULL 文档与 0/1 Boolean 结果。

## 原生下推准入条件

当前 native TiFlash path 必须同时满足：

```text
direct WHERE/HAVING/ON Boolean MATCH
AND one MATCH column
AND constant query string
AND public FULLTEXT index on the column
AND STANDARD_V1 parser
AND TiFlash replica available
AND default analyzer configuration
AND Boolean syntax can be represented by tipb AST
```

其中默认 analyzer 配置对应 InnoDB 默认值：min token size 3、max token size
84、启用默认 stopword。非默认配置不会错误下推到 TiFlash。

## 明确不支持或不承诺

- TiKV FTS 下推；
- TiCI；
- 多列 MATCH 的 TiFlash native path；
- `NGRAM_V1`、`MULTILINGUAL_V1` native path；
- 非默认 analyzer/stopword/token size 的 native path；
- natural-language relevance score；
- query expansion；
- 完整 MySQL Boolean 扩展语法、proximity 和复杂评分操作符；
- 真实 TiDB + TiFlash 集群 E2E 结果一致性。

不满足 native 条件时，若 `tidb_enable_local_match_against=1`，允许使用 TiDB
本地 no-score evaluator；否则返回明确的不支持错误。当前不把 LIKE 近似 fallback
作为 release-8.5 FTS 交付能力。

## tipb 选择

不能直接使用仅含 FTS 的旧分支：它删除了 release-8.5 仍被 TiFlash 使用的
协议字段。最终协议分支以 `origin/release-8.5` 为基线，再增加：

- `FTSBooleanQuery`；
- `FTSBooleanNode`；
- `FTSBooleanTerm`；
- Boolean enums；
- `FTSQueryInfo.boolean_query = 22`；
- TableScan/PartitionTableScan 的 `used_columnar_indexes`。

TiDB 和 TiFlash 必须使用同一个 tipb commit，避免 Go/C++ generated protocol
和 wire field 不一致。

## 验证记录

已通过：

```bash
GOCACHE=/tmp/tidb-release85-go-cache go test --tags=intest \
  ./pkg/expression ./pkg/expression/fulltext ./pkg/expression/matchagainst \
  ./pkg/planner/core -run 'TestBuildFTSBooleanQuery|TestFTS|^$'

go mod download github.com/solotzg/tipb@v0.0.0-20260920053028-0a9c803d4e7b
git diff --check
```

tipb 仓库的 `go test ./...` 已通过。TiDB expression 和 planner core 在远程
tipb replacement 下编译通过。

当前未作为通过项记录：

- DDL 包测试受到既有 etcd/otelgrpc API 错误：
  `undefined: otelgrpc.UnaryServerInterceptor`；
- mock TiFlash planner runtime 测试需要绑定 IPv6 本地端口，当前沙箱禁止该
  操作；
- 尚未完成 TiFlash gtest 完整链接/runtime；
- 尚未完成真实 TiDB -> TiFlash DAG RPC 和结果差分；
- 尚未完成性能、并发、重启、replica rebuild、schema change 验证。

## 产品化前剩余工作

### P0 正确性

1. 将 TiDB local evaluator 与 TiFlash evaluator 做 SQL 差分测试。
2. 完成 TiFlash gtest runtime 和真实 TiDB + TiFlash E2E。
3. 验证 Unicode、collation、stopword 后 phrase position、token 边界、空 query
   和全 stopword query。
4. 验证 FULLTEXT index state、replica availability、schema change、重建和
   TiFlash 重启过程中的 planner 行为。
5. 增加 tipb/TiDB/TiFlash 混合版本兼容测试。

### P1 交付质量

1. 增加 native/local/fallback 的 EXPLAIN、日志和 metrics。
2. 增加 Boolean query 长度、节点数、token 数限制。
3. 测量 FTS index scan 的 CPU、内存、selectivity 和并发性能。
4. 明确 feature gate、灰度和回滚策略。

### P2 后续能力

1. 多列 MATCH native pushdown；
2. analyzer 配置协议化；
3. NGRAM/MULTILINGUAL parser；
4. 更完整的 Boolean 分组/proximity；
5. natural-language score 和 query expansion。

## 相关分支

- TiDB: https://github.com/solotzg/tidb/tree/match_against-release-8.5
- TiFlash: https://github.com/solotzg/tiflash/tree/match_against
- tipb: https://github.com/solotzg/tipb/tree/release-8.5-match-against

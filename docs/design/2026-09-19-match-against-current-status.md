# MATCH AGAINST BOOLEAN MODE 下推当前状态报告

更新时间：2026-09-20
实现范围：#70484、#70485 及 TiFlash 下推
当前状态：TiDB、TiFlash 父仓库和 tipb 协议分支均已推送；端到端验收和产品化差分验证仍未完成。

## 1. 结论摘要

当前已经形成以下完整代码链路：

```text
SQL MATCH ... AGAINST (... IN BOOLEAN MODE)
    -> TiDB expression rewriter
    -> TiDB Boolean AST
    -> tipb.FTSQueryInfo.boolean_query
    -> TiFlash TableScan
    -> fts_match_expression
    -> TiFlash C++ Boolean matcher
```

当前实现可以在满足限定条件时，把 Boolean MATCH 谓词下推到 TiFlash：

- 单列 MATCH；
- public `STANDARD_V1` FULLTEXT index；
- TiFlash replica 已 available；
- TiDB 使用默认 InnoDB analyzer 配置；
- 查询是直接的 WHERE Boolean MATCH 谓词；
- 不涉及 TiKV、TiCI、自然语言评分和 query expansion。

当前还不能把它称为完整生产能力，主要原因是：

1. TiDB 和 TiFlash 目前各自维护 analyzer 实现，尚未完成严格差分验证；
2. TiFlash 只完成了生产代码和测试源文件编译，完整 gtest 可执行文件尚未链接运行；
3. 尚未完成真实 TiDB + TiFlash 集群端到端测试；
4. Boolean AST 当前通过 marker-prefixed scalar function 参数传递，仍属于内部过渡协议；
5. 非默认 analyzer、多列 MATCH 和非 STANDARD parser 尚未实现原生下推。

## 2. Git 与远端状态

### 2.1 TiDB

| 项目 | 当前值 |
| --- | --- |
| 本地路径 | `/Users/solotzg/Work/tidb-2` |
| 分支 | `match_against-release-8.5` |
| upstream | `origin/match_against-release-8.5` |
| HEAD | `9e6ee16f5b` |
| 提交说明 | `fts: push MATCH AGAINST boolean predicates to TiFlash` |
| origin | `git@github.com:solotzg/tidb.git` |
| 工作树 | FTS tracked changes clean；原有 `.codex/`、`bazel-tidb-2` 未跟踪项保留 |

TiDB 分支直接基于 `pingcap/release-8.5` 提交 `2787a8f1136`，未包含
master 上的无关 query-limit 提交。`pkg/sessionctx/variable` 是 release-8.5
对应 master `pkg/sessionctx/vardef`/`tidb_vars.go` 的目录与包重命名，不是
功能缺失，因此没有重复引入 sysvar 包。

### 2.2 TiFlash

| 项目 | 当前值 |
| --- | --- |
| 本地路径 | `/Users/solotzg/Work/tiflash` |
| 分支 | `match_against` |
| upstream | `origin/match_against` |
| HEAD | `bd4f78a06c` |
| 提交说明 | `fts: support MATCH AGAINST boolean pushdown` |
| origin | `git@github.com:solotzg/tiflash.git` |
| 工作树 | 干净 |

### 2.3 tipb 子模块

TiFlash 的 `contrib/tipb` 已推送到用户 fork：

| 项目 | 当前值 |
| --- | --- |
| 路径 | `/Users/solotzg/Work/tiflash/contrib/tipb` |
| 提交 | `0a9c803d4e7bf37a7d67a3e74609b8dd56399d09` |
| 分支 | `release-8.5-match-against` |
| origin | `git@github.com:solotzg/tipb.git` |
| 内容 | FTS Boolean query protocol |

该提交基于 tipb `origin/release-8.5`，保留 release-8.5 的旧协议字段，增加
`FTSQueryInfo.boolean_query`、`FTSBooleanQuery` 和
`used_columnar_indexes`。TiFlash 父仓库已把 submodule 指针更新到该提交。

远端地址：

- [TiDB match_against-release-8.5](https://github.com/solotzg/tidb/tree/match_against-release-8.5)
- [TiFlash match_against](https://github.com/solotzg/tiflash/tree/match_against)
- [tipb release-8.5-match-against](https://github.com/solotzg/tipb/tree/release-8.5-match-against)

## 3. 当前支持范围

### 3.1 支持的 Boolean 语义

当前 TiDB Boolean parser 和 TiFlash protocol evaluator 覆盖：

| 语法/语义 | 状态 | 说明 |
| --- | --- | --- |
| 普通 term | 支持 | 作为 optional term 处理 |
| `+term` | 支持 | required term |
| `-term` | 支持 | prohibited term |
| `"phrase words"` | 支持 | 同一列内按 token position 匹配 |
| `prefix*` | 支持 | 前缀匹配 |
| 只有 prohibited term | 按本地 matcher 语义返回无匹配 | 已补充 TiFlash 协议路径处理 |
| NULL MATCH 列 | 支持 | NULL 列贡献空 token 集合 |
| 多个 term | 支持 | required/optional 规则由 Boolean AST 表示 |
| 空 query/全部 stopword | 返回无匹配 | 由 analyzer 结果决定 |

### 3.2 明确不在当前范围内

- 自然语言模式 relevance score；
- query expansion；
- TiKV 下推；
- TiCI；
- `NGRAM_V1` 和 `MULTILINGUAL_V1` 的 TiFlash 原生路径；
- 多列 MATCH 的 TiFlash 原生路径；
- 非默认 token size、stopword 等 analyzer 配置；
- phrase proximity 和需要评分的 Boolean 操作符；
- 完整的分组/嵌套 Boolean 语义；当前 `STANDARD_V1` parser 会拒绝相关扩展语法。

## 4. TiDB 实现状态

### 4.1 Expression 层

主要文件：

- `pkg/expression/fts_helper.go`
- `pkg/expression/builtin_fts.go`
- `pkg/expression/fts_tipb.go`
- `pkg/expression/distsql_builtin.go`
- `pkg/expression/infer_pushdown.go`

实现内容：

1. `FTSInfo` 增加 MATCH AGAINST 标识、modifier 和多列元数据；
2. 读取并保存 Boolean mode modifier；
3. 只把稳定的 constant search string 转换成 TiFlash Boolean AST；
4. 通过 `BuildFTSBooleanQuery` 将 TiDB parser AST 转换成 tipb AST；
5. `FTSFunctionIsUsed` 会正确标记 MATCH AGAINST；
6. TiDB 本地 evaluator 返回 no-score 的 0/1 结果；
7. 包装在其它表达式中的 MATCH 会被既有 FTS usage 检查拦截。

### 4.2 Planner 层

主要文件：

- `pkg/planner/core/expression_rewriter.go`
- `pkg/planner/core/fts_resolve_index.go`

当前 native pushdown 需要同时满足：

```text
Boolean mode
AND one MATCH column
AND available TiFlash replica
AND public FULLTEXT index
AND STANDARD_V1 parser
AND default analyzer configuration
AND Boolean query syntax can be parsed
```

不满足条件时，planner 不会错误地声称 TiFlash native path 可用；在本地
MATCH 开关允许时，优先走 TiDB local no-score evaluator，否则按现有能力报
不支持或进入已有 fallback 路径。

### 4.3 Protocol 层

TiDB 写入：

- `FTSQueryInfo.query_func = FTSMatchExpression`；
- `FTSQueryInfo.query_text`；
- `FTSQueryInfo.query_tokenizer`；
- MATCH column metadata；
- `FTSQueryInfo.boolean_query`。

当前 TiDB 使用：

```text
github.com/solotzg/tipb v0.0.0-20260920053028-0a9c803d4e7b
```

该版本保留 release-8.5 协议兼容性，同时包含对应 Boolean message 和
`used_columnar_indexes` 字段；TiFlash submodule 已指向相同提交。

## 5. TiFlash 实现状态

### 5.1 请求和计划链路

修改覆盖：

- DAG storage interpreter；
- DAG utils/function mapping；
- Remote request；
- TiDB table scan；
- PhysicalTableScan；
- FTS function registration。

`PhysicalTableScan` 会从 `FTSQueryInfo` 构造：

```text
fts_match_expression(query_text, column_ref, ...,
    "__tiflash_fts_boolean_query__:" + SerializeAsString(boolean_query))
```

额外参数只在 `boolean_query` 存在时添加，旧的 `FTS_MATCH_WORD` 路径保持
兼容。

### 5.2 C++ evaluator

主要文件：

- `dbms/src/Functions/FunctionsFullText.cpp`
- `dbms/src/Functions/FunctionsFullText.h`
- `dbms/src/Functions/tests/gtest_fulltext.cpp`

当前实现包含：

- UTF-8 tokenization；
- ASCII/Unicode 字母数字识别；
- lower-case；
- 默认 stopword；
- 默认 token 长度边界；
- required/optional/prohibited clause；
- phrase position 匹配；
- prefix 匹配；
- NULL 列空文档语义；
- tipb Boolean AST 解码；
- Boolean protocol path 返回 0/1，而不是 relevance count。

## 6. 正确性与“精度”评估

这里的“精度”指结果语义一致性和误匹配/漏匹配风险，不是浮点数精度。

| 维度 | 当前评估 | 依据 | 结论 |
| --- | --- | --- | --- |
| tipb Boolean AST 构造 | 高 | Go conversion tests 通过 | 协议字段映射基本可靠 |
| TiDB planner 下推判定 | 高 | planner regression 通过 | 已避免无 index/非标准 parser 误下推 |
| 默认 analyzer 语义 | 中 | TiDB analyzer tests 通过，TiFlash C++ 独立实现 | 仍需跨引擎差分测试 |
| required/prohibited 逻辑 | 中高 | TiDB local tests、TiFlash source test 编译 | TiFlash gtest runtime 尚未执行 |
| phrase/prefix | 中 | 有实现和测试输入 | Unicode/边界情况仍需差分验证 |
| NULL 多列行为 | 中 | planner/local 逻辑有覆盖 | 尚未完成真实 TiFlash runtime 验证 |
| prepared statement | TiDB local 路径较高 | #70485 local matcher 有测试 | native TiFlash 只接受稳定 constant query |
| 多列 MATCH | 低/不支持 native | planner 明确限制单列 | 当前必须视为产品限制 |
| 非默认 analyzer | 低/不支持 native | TiFlash 未接收 analyzer config | 必须回退或显式拒绝 |
| end-to-end 结果一致性 | 未验收 | 尚未启动真实 TiDB+TiFlash 集群 | 不能宣称生产级一致性 |

### 6.1 已知的潜在差异点

TiDB 和 TiFlash 目前没有共享同一个 analyzer 实现，以下场景可能产生
误差，需要产品化前建立差分测试：

- Unicode 大小写和字符分类；
- underscore 与标点边界；
- 非 ASCII token 长度统计；
- stopword 后 phrase position；
- 超长 prefix；
- 空 phrase、空 query 和全 stopword query；
- 不同 collation 下的大小写行为。

在这些问题没有完成验证前，默认配置下的当前实现应定位为“较高可信的实验
路径”，不能称为完整 MySQL/TiDB 语义兼容。

## 7. Fallback 与错误行为

| 场景 | 当前 native TiFlash | 当前预期行为 |
| --- | ---: | --- |
| 单列 + STANDARD_V1 + 默认配置 + replica available | 支持 | TiFlash FTS pushdown |
| 无 TiFlash replica | 不支持 | local evaluator 或不支持 |
| 无 matching FULLTEXT index | 不支持 | local evaluator 或错误 |
| 多列 MATCH | 不支持 | 不进入 native path |
| NGRAM/MULTILINGUAL parser | 不支持 | 不进入 native path |
| 非默认 analyzer config | 不支持 | 不进入 native path |
| query expansion | 不支持 | 保持现有不支持边界 |
| 非 constant search string | 不进入 native path | local prepared path 或不支持 |
| 复杂/非法 Boolean syntax | 不进入 native path | planner/parser 报错或走已有 fallback |

产品发布前需要把“local evaluator、fallback、报错”三种行为通过显式配置和
文档固定下来，避免用户只看到性能变化而不知道执行引擎发生了切换。

## 8. 已完成验证

### 8.1 TiDB

以下定向检查已通过：

```bash
GOCACHE=/tmp/tidb-release85-go-cache go test --tags=intest \
  ./pkg/expression ./pkg/expression/fulltext ./pkg/expression/matchagainst \
  ./pkg/planner/core -run 'TestBuildFTSBooleanQuery|TestFTS|^$'
GONOSUMDB=github.com/solotzg/tipb GOPRIVATE=github.com/solotzg/tipb \
  go mod download github.com/solotzg/tipb@v0.0.0-20260920053028-0a9c803d4e7b
git diff --check
```

TiDB expression 和 planner core 在远程 tipb replacement 下通过编译；DDL
定向测试受到本地 etcd/otelgrpc 依赖错误阻塞，与本次 FTS 改动无关。尚未把
无法绑定本地端口的 mock TiFlash planner runtime 测试作为通过项。

### 8.2 TiFlash

以下目标已编译通过：

```bash
env CCACHE_DISABLE=1 ninja -C /tmp/tiflash-match-against-build -j2 \
  dbms/src/Flash/CMakeFiles/flash_service.dir/Planner/Plans/PhysicalTableScan.cpp.o \
  dbms/src/Functions/CMakeFiles/tiflash_functions.dir/FunctionsFullText.cpp.o \
  dbms/CMakeFiles/gtests_dbms.dir/src/Functions/tests/gtest_fulltext.cpp.o
```

当前还没有完成：

- `gtests_dbms` 完整链接；
- `MatchExpressionProtocolBooleanQuery` gtest runtime 执行；
- TiFlash server 启动；
- TiDB -> TiFlash 真实 DAG RPC；
- 真实数据上的结果差分和性能压测。

### 8.3 未作为通过项的检查

曾运行：

```bash
GOCACHE=/tmp/tidb-go-cache go test ./pkg/expression ./pkg/planner/core
```

未作为通过项记录，原因是本地环境出现与本次改动无关的既有问题：

- expression 测试要求 `--tags=intest`；
- planner mock TiFlash 需要监听 IPv6 本地端口，而当前沙箱禁止绑定该端口；
- DDL 依赖链出现 `otelgrpc.UnaryServerInterceptor` 缺失。

## 9. 产品化必须完成的工作

### P0：正确性与兼容性

1. 建立 TiDB local evaluator 与 TiFlash evaluator 的 SQL 差分测试集；
2. 完成完整 TiFlash gtest 链接和 runtime 执行；
3. 完成真实 TiDB + TiFlash 集群端到端测试；
4. 明确多列、非默认 analyzer、非 STANDARD parser 的产品行为；
5. 将 marker transport 替换为正式可版本化的协议字段，或至少定义严格的
   function argument contract；
6. 增加 tipb/TiDB/TiFlash 混合版本兼容测试；
7. 验证 DDL、index state、replica availability 变化时不会产生 false negative。

### P1：性能与运维

1. 测量 FTS index scan、过滤 selectivity、CPU、内存和并发性能；
2. 避免每行重复解析 Boolean AST，确认 query-level cache 生命周期；
3. 增加 Boolean query 长度、节点数和 token 数限制；
4. 增加 EXPLAIN、日志和 metrics，能够识别 native/local/fallback；
5. 增加灰度开关和回滚开关；
6. 验证 TiFlash 重启、replica 重建、index rebuild 和 schema change。

### P2：能力扩展

1. 多列 MATCH 原生下推；
2. NGRAM/MULTILINGUAL parser；
3. analyzer 配置和 stopword 配置协议化；
4. 分组、proximity 和更完整的 Boolean 语法；
5. 自然语言评分和 query expansion。

## 10. 当前发布建议

当前适合：

- 开发环境验证；
- planner/protocol 联调；
- TiFlash C++ evaluator 开发；
- 差分测试和性能实验。

当前不建议：

- 默认对所有用户开启；
- 在非默认 analyzer 配置下宣称语义兼容；
- 在多列 MATCH、非 STANDARD parser 下宣称 native pushdown；
- 未完成端到端验证就作为稳定生产特性发布。

## 11. 相关设计文档

- [TiDB/TiFlash 实现计划](./2026-09-18-match-against-tiflash-tidb.md)
- [TiDB match_against 分支](https://github.com/solotzg/tidb/tree/match_against)
- [TiFlash match_against 分支](https://github.com/solotzg/tiflash/tree/match_against)
- [tipb match_against 分支](https://github.com/solotzg/tipb/tree/match_against)

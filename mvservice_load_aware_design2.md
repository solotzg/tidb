# TiDB MVService 负载感知调度总体设计方案（V2）

## 0. 文档说明

1. 本文档基于当前代码实现约束（截至当前工作区）给出可落地的负载感知调度方案。
2. 目标是一次性打通「负载上报 -> 权重计算 -> owner 重建 -> 观测与回退」链路。
3. 调度语义保持 at-least-once，不改变 refresh/purge 任务正确性边界。

## 1. 当前实现基线（As-Is）

### 1.1 主调度链路

当前 MVService 主循环为：

1. `Run()` 启动后执行 server init、启动 executor。
2. 周期执行 `refreshServersIfDue`。
3. 按 `ddlDirty || shouldFetchMVMeta` 触发 `fetchAllMVMeta`。
4. 当 `serverChanged || metaChanged` 时执行 `rebuildOwnedTasksFromAllMeta`。
5. 取出到期任务并分发 refresh/purge。

### 1.2 server refresh 语义

1. 当前 `ServerConsistentHash.refresh()` 返回单信号：`changed bool`。
2. 只能表达 membership 变化，不能区分“拓扑变化”和“权重变化”。

### 1.3 owner 重建语义

1. 当前 owner 重建路径已是 `allMeta + rebuildOwnedTasksFromAllMeta`。
2. 不再依赖旧的 `filterUnownedTasks` 模式。
3. running 任务不抢占，queued 任务可在重建时被释放/接管。

### 1.4 配置与观测现状

当前已存在相关 sysvar：

1. `tidb_mview_task_max`
2. `tidb_mview_task_threshold_cpu`
3. `tidb_mview_task_threshold_memory`
4. `tidb_mview_refresh_hist_time`
5. `tidb_mlog_purge_hist_time`

当前无：

1. `tidb_mview_auto_rebalance`
2. `node_load/task_weight_snapshot` 自动调权链路

## 2. 设计目标（V2）

1. 在不新增系统表、不改变任务语义前提下实现跨节点负载再平衡。
2. 提供明确、可操作、可观测的全局控制面。
3. 权重异常、链路失败可自动回退到等权一致性哈希。
4. 保持混部与滚动升级可用性。

## 3. 非目标

1. 不做 running 任务中断或迁移。
2. 不追求单任务严格 single-execution。
3. 不引入复杂全局最优调度器。

## 4. 总体架构（To-Be）

新增两条内部链路，保留现有 MVService 主体：

1. `LoadReporter`（每节点）

- 周期采样本机负载并写 `node_load/<exec_id>`。
- 仅上报，不直接写权重。

2. `WeightManager`（集群单写者）

- 通过 etcd lease/lock 选主。
- 汇总全部 `node_load`，计算目标 weight，原子写入 `task_weight_snapshot`。

3. `MVService`（已有）

- refresh membership 时叠加读取 `task_weight_snapshot` overlay。
- 构建 weighted consistent hash ring。
- 根据变化类型执行轻重不同的收敛路径。

4. `MVCoordination`（由 Domain 注入的窄协调接口）

- 作为 `mvservice` 与 etcd/协调层之间的唯一边界。
- 在 `RegisterMVService` 时传入，避免 `mvservice` 直接依赖 `domain` 或裸 etcd client。
- 只暴露 MV 负载感知所需的最小读写/选主能力。

## 5. etcd 内部数据模型

本章全部为内部实现细节，不作为外部接口。

### 5.1 key 空间

1. `/tidb/mvservice/node_load/<exec_id>`
2. `/tidb/mvservice/task_weight_snapshot`
3. `/tidb/mvservice/weight_manager`
4. 继续保留 `/tidb/mvservice/ddl`（现有 DDL 广播 key）

其中：

1. `exec_id` 统一定义为 `disttaskutil.GenerateExecID(*infosync.ServerInfo)` 的返回值。
2. 即统一使用实例级 `exec_id` 作为节点身份，当前语义为 `IP:Port`。

### 5.2 value 建议

`node_load/<exec_id>`：

1. `cpu`
2. `mem`
3. `waiting`
4. `backpressure_blocked`
5. `updated_at_unix_sec`

`task_weight_snapshot`：

1. `epoch`
2. `manager_id`
3. `updated_at_unix_sec`
4. `items`
5. `items[*].exec_id`
6. `items[*].weight`（`0..100`）
7. `items[*].reason`
8. `items[*].exec_id` 在同一 snapshot 内必须唯一

### 5.3 语义

1. `task_weight_snapshot` 缺失或读取失败：回退默认 `100`。
2. `items[*].weight=0`：节点不参与新 owner 分配（drain）。
3. `task_weight_snapshot` 过期（建议阈值 `30s`）视为无效，回退默认权重。
4. 仅 `WeightManager` 允许写 `task_weight_snapshot`。
5. `node_load/<exec_id>` 与 `task_weight_snapshot.items[*].exec_id` 必须使用同一种实例身份编码，即 `disttaskutil.GenerateExecID`。
6. 同一 TiDB 实例只要 `GenerateExecID` 结果不变，重启前后应视为同一节点，不应导致 owner 漂移。
7. `node_load/<exec_id>` 的正确性不依赖 etcd TTL；默认通过当前 membership 过滤和 `updated_at_unix_sec` 判 stale。
8. 若实现侧为 `node_load/<exec_id>` 增加 etcd TTL，该 TTL 仅作为孤儿 key 清理优化，不能作为唯一正确性前提。
9. `items[*].reason` 必须使用低基数枚举，建议取值：

- `normal`
- `high_cpu`
- `high_mem`
- `high_waiting`
- `backpressure`
- `stale_node_load`
- `control_off`

10. 若同一 snapshot 中出现重复 `exec_id`，该 snapshot 视为非法，reader 应整体忽略并回退默认权重。
11. `reason` 的优先级建议固定为：

- `control_off`
- `stale_node_load`
- `backpressure`
- `high_cpu`
- `high_mem`
- `high_waiting`
- `normal`

### 5.4 协调接口约束

文档中的 etcd 读写能力不应直接下沉到 `mvservice` 的各个模块。  
推荐在 `RegisterMVService` 时注入窄接口 `MVCoordination`，例如：

```go
type TaskWeightItem struct {
    ExecID string
    Weight int
    Reason string
}

type TaskWeightSnapshot struct {
    Epoch            int64
    ManagerID        string
    UpdatedAtUnixSec int64
    Items            []TaskWeightItem
}

type TaskWeightSnapshotReader interface {
    LoadTaskWeightSnapshot(ctx context.Context) (*TaskWeightSnapshot, error)
}

type NodeLoadWriter interface {
    StoreNodeLoad(ctx context.Context, execID string, load NodeLoad) error
}

type NodeLoadReader interface {
    LoadNodeLoads(ctx context.Context) (map[string]NodeLoad, error)
}

type WeightManagerSession interface {
    ID() string
    Done() <-chan struct{}
    Close() error
}

type TaskWeightSnapshotWriter interface {
    StoreTaskWeightSnapshot(ctx context.Context, session WeightManagerSession, snapshot TaskWeightSnapshot) error
}

type WeightManagerElector interface {
    CampaignWeightManager(ctx context.Context, execID string) (WeightManagerSession, error)
}

type RebalanceControl struct {
    Enabled bool
}

type RebalanceControlReader interface {
    LoadRebalanceControl(ctx context.Context) (RebalanceControl, error)
}

type MVCoordination interface {
    TaskWeightSnapshotReader
    NodeLoadWriter
    NodeLoadReader
    TaskWeightSnapshotWriter
    WeightManagerElector
    RebalanceControlReader
}
```

要求：

1. `mvservice` 只依赖该接口，不依赖裸 etcd client。
2. 具体 etcd client、lease、watch、序列化细节都留在 Domain/协调层实现。
3. `WeightManager` 必须能通过该接口读取全量 `node_load`、原子发布完整 `task_weight_snapshot`，否则无法形成闭环。
4. `StoreTaskWeightSnapshot` 必须基于单 key 快照保证原子读写可见性，避免 reader 看到混合代际数据。
5. 控制面状态也通过该接口读取，避免 `WeightManager` 再额外回读 Domain 内部状态。
6. `WeightManagerSession` 必须代表当前仍有效的 leader 身份；当 session 失效后，后续写快照必须失败或被拒绝。
7. `epoch` 由 `WeightManager` 单调递增；同一轮写入共享同一 `epoch`。
8. membership 是 owner 计算的唯一真源；snapshot 中不属于当前 membership 的 `exec_id` 必须被忽略。
9. 当前 membership 中缺失于 snapshot 的节点按默认权重 `100` 处理。
10. `MVService`、`LoadReporter`、`WeightManager` 必须共享同一套 `exec_id` 生成逻辑，统一复用 `disttaskutil.GenerateExecID`，不得各自手写 `IP:Port` 拼接规则。
11. `StoreTaskWeightSnapshot` 必须在同一 etcd 事务内完成“校验当前 leader 身份仍有效 + 写入 snapshot”，避免先检查、后写入的竞态窗口。
12. `LoadTaskWeightSnapshot` 必须使用线性一致读，不能依赖可陈旧的本地缓存或 serializable 读。
13. V2 直接采用“按需 linearizable Get 单快照 key”的读路径，不引入 watch 驱动的本地 snapshot cache。
14. `WeightManager` 的 live TiDB membership 直接复用 `infosync.GetAllServerInfo(ctx)`；`MVCoordination` 不负责 membership 读取。
15. `WeightManager` 在消费 `node_load`、生成 `task_weight_snapshot` 前，必须先将 `infosync.GetAllServerInfo(ctx)` 转成 `exec_id` 集合，并仅对该集合内的节点生效。
16. `WeightManager` 的“cluster ready”判定可直接基于 live membership 的 `node_load` 完整性与新鲜度：
    仅当所有 live TiDB 节点的 `exec_id` 都存在 fresh `node_load/<exec_id>` 时，才允许发布有效 `task_weight_snapshot`。
17. 上述 ready 判定依赖一个前提：所有会写 `node_load` 的节点，都已经包含 MV rebalance V2 的读写路径实现。
18. 其中 fresh 的判定建议明确为：`now - updated_at_unix_sec <= staleThreshold`，当前文档建议 `staleThreshold = 30s`。
19. 便于单测通过 mock 注入覆盖全部分支。

### 5.5 模块依赖切分

不要让所有模块都依赖完整 `MVCoordination`，而是按职责依赖子接口：

1. `serviceHelper`

- 只依赖 `TaskWeightSnapshotReader`
- 用于 `getAllServerInfo(ctx)` 做 `infosync + snapshot` overlay

2. `LoadReporter`

- 只依赖 `NodeLoadWriter`
- 周期写本机 `node_load`

3. `WeightManager`

- 依赖 `NodeLoadReader`
- 依赖 `TaskWeightSnapshotWriter`
- 依赖 `WeightManagerElector`
- 依赖 `RebalanceControlReader`
- membership 直接复用 `infosync.GetAllServerInfo(ctx)`，不通过 `MVCoordination`

4. `MVService`

- 不直接读写协调层
- 仍通过 `serviceHelper` 获取合并后的 `serverInfo`

## 6. 权重算法与防抖策略

## 6.1 输入信号

仅使用可直接获取的信号：

1. CPU 使用率
2. 内存使用率
3. `exec_waiting`
4. `exec_backpressure_blocked`
5. 全局 `tidb_mview_task_max`

说明：

1. `LoadReporter` 直接读取本地 `MVService/TaskExecutor` 的进程内状态。
2. 不通过 Prometheus 指标反采样这些值，避免额外延迟、聚合误差和自依赖。
3. CPU 使用率采用 TiDB 进程级口径：

- 优先按 cgroup/container CPU quota 归一化。
- 若无 cgroup 限制，则按宿主机逻辑 CPU 数归一化。

4. 内存使用率采用 TiDB 进程级口径：

- 优先按 cgroup/container memory limit 归一化。
- 若无 cgroup 限制，则按宿主机总内存归一化。

5. CPU/MEM 都按最近 `30s` 的滑动窗口或等价 EWMA 口径计算，不使用单点瞬时值参与调权。

## 6.2 容量基线

权重算法直接读取当前全局 `tidb_mview_task_max`。

约束与假设：

1. 该 sysvar 会在所有 TiDB 节点生效。
2. 集群部署配置一致。
3. 若 `tidb_mview_task_max = 0`，则按 `GOMAXPROCS(0)` 解释。
4. 采用该语义的前提是所有 TiDB 节点的 `GOMAXPROCS` 等价；若不满足，该部署形态不在本方案支持范围内。

`tidb_mview_task_max` 的作用是“容量归一化基线”，不是直接等于最终权重：

1. 当 `tidb_mview_task_max > 0` 时，它表示 MV 后台任务的理论并发容量上限。
2. 当 `tidb_mview_task_max = 0` 时，按 `GOMAXPROCS(0)` 解释本地容量上限。
3. `WeightManager` 通过它把 `waiting` 这类绝对值信号转成“相对容量压力”。
4. 例如同样是 `waiting=10`：

- 对 `task_max=64` 的节点，通常不构成明显压力。
- 对 `task_max=4` 的节点，通常已构成严重堆积。

5. 因此，调节 `tidb_mview_task_max` 不仅会改变本地 executor 并发，也会改变全局调权基线与 owner 分布。
6. 同理，调节 `tidb_mview_task_threshold_cpu` 与 `tidb_mview_task_threshold_memory`，也会改变全局调权决策，而不只是本地背压行为。

建议在算法中先计算：

1. `effectiveTaskMax`

- 若 `tidb_mview_task_max > 0`，取该值。
- 若 `tidb_mview_task_max = 0`，取 `GOMAXPROCS(0)`。

2. `queueRatio = waiting / max(1, effectiveTaskMax)`

再与 CPU/MEM/backpressure 信号一起映射到最终权重档位。

## 6.3 档位

建议离散档位：`100 / 60 / 30 / 0`。

1. `100`：正常
2. `60`：轻中度过载
3. `30`：持续过载
4. `0`：stale/offline 或持续严重过载

建议映射规则：

1. `weight=0`

- `node_load` stale/offline

2. `weight=30`

- `backpressure_blocked > 0`
- 或 `queueRatio >= 2`
- 或 CPU/MEM 持续明显高于阈值

3. `weight=60`

- `queueRatio >= 1`
- 或 CPU/MEM 轻中度高于阈值

4. `weight=100`

- 其余情况

## 6.4 防抖约束

1. 连续 3 个窗口命中目标档位才允许变更。
2. 冷却时间 5 分钟。
3. 单步变化：`100->60->30->0`，反向同理。
4. 始终保证至少 1 个节点 `weight>0`。
5. 当所有节点都将降为 `0` 时，保底节点选择规则必须稳定且确定，建议：

- 优先选择当前 membership 中 `exec_id` 字典序最小的节点；
- 或在实现中选择更合理的固定规则，但必须全局确定且可复现。

### 6.5 上报周期与 stale 判定

1. `LoadReporter` 建议每 `10s` 上报一次 `node_load`。
2. `WeightManager` 判定 `node_load` stale 的阈值建议为 `30s`。
3. 即：`now - updated_at_unix_sec > 30s` 时，可判定该节点为 stale；按默认 `10s` 上报周期，也等价于连续 `3` 个上报周期未更新。
4. stale 节点按 `weight=0` 处理，但仍受“至少一节点 `weight>0`”保底约束。
5. `task_weight_snapshot` 的 stale 阈值建议为 `30s`，与 snapshot 心跳周期保持同一数量级。
6. `WeightManager` 在消费 `node_load` 前，必须先与当前 membership 取交集；不在当前 membership 中的 `exec_id` 一律忽略。
7. 默认不要求为 `node_load` 配置 etcd TTL；若实现了 TTL，其值也应明显大于 stale 阈值，并继续保留 membership 过滤与 `updated_at_unix_sec` 判 stale。

### 6.6 snapshot 心跳与变化判定

1. `WeightManager` 建议每 `10s` 刷新一次 `task_weight_snapshot` 心跳。
2. 即使权重集合未变化，也允许仅前推 `updated_at_unix_sec` 并发布新 snapshot，以避免 reader 将 snapshot 误判为 stale。
3. `weightChanged` 的判定只基于“membership + effective weight map”。
4. `epoch`、`manager_id`、`updated_at_unix_sec` 的单独变化，不应触发 `weightChanged`。
5. 为避免无意义抖动，写入与比较前应按 `exec_id` 对 `items` 排序，或转成稳定的 `map[string]int` 后比较。
6. `epoch` 只在 effective weight 集合发生变化时递增。
7. 若仅做 snapshot 心跳刷新，则保持 `epoch` 不变，只前推 `updated_at_unix_sec`。

### 6.7 snapshot 大小约束

1. `task_weight_snapshot` 必须保持远小于 etcd 单值大小限制。
2. 按当前 TiDB 预期节点规模，snapshot 应控制在 KB 级到低几十 KB 级。
3. 建议内部硬上限为 `256 KiB`。
4. 若编码后的 snapshot 超过该上限，应拒绝发布新 snapshot，保留旧 snapshot，并打错误日志与监控。
5. 该功能依赖“TiDB 节点数量处于可由单 snapshot 承载的规模”这一前提。

## 7. 一致性哈希构建策略

### 7.1 weighted replicas

设 `baseReplicas=100`（保持当前默认）：

1. `weight=0`：`effectiveReplicas=0`（不入 ring）
2. `weight>0`：`effectiveReplicas=floor(baseReplicas*weight/100)`
3. `weight>0` 的最小保障：`effectiveReplicas>=1`

### 7.2 回退策略

若 `task_weight_snapshot` 整体不可用：

1. 使用 membership 等权构建 ring。
2. 不阻塞调度主链路。

## 8. 调度收敛策略

### 8.1 refresh 返回双信号

将 `refresh()` 语义扩展为：

1. `topologyChanged`
2. `weightChanged`

### 8.2 Run 中策略

1. `topologyChanged=true`：

- 走重路径：全量 metadata fetch + rebuild。

2. `weightChanged=true && topologyChanged=false`：

- 走轻路径：直接 rebuild owned（不强制 fetch）。

3. 均为 false：

- 不触发 ownership 重建。

### 8.3 语义保证

1. running 任务不抢占。
2. queued 任务允许短时双节点重复触发。
3. 由任务内部隔离/幂等保证最终正确性。

## 9. 接口与模块改造清单

### 9.1 `pkg/mvservice/server_maintainer.go`

1. `serverInfo` 增加权重信息字段。
2. `refresh()` 返回双信号。
3. membership 与 snapshot overlay 合并后统一判定变化。
4. `weightChanged` 只比较 effective weight，不比较 snapshot 元数据字段。

### 9.2 `pkg/mvservice/consistenthash.go`

1. 支持按节点 `effectiveReplicas` 构建 ring。
2. 保留现有 hash 函数与排序语义。

### 9.3 `pkg/mvservice/service_helper.go`

1. `RegisterMVService(...)`：

- 在现有参数基础上增加 `MVCoordination` 注入。
- `newServiceHelper()` 改为接收 `TaskWeightSnapshotReader` 子接口。

2. `getAllServerInfo(ctx)`：

- 读取 `infosync.GetAllServerInfo`。
- 通过 `MVCoordination.LoadTaskWeightSnapshot` 读取权重快照。
- 仅对当前 membership 中存在的节点做 overlay。
- 不在 snapshot 中的成员按默认权重 `100` 处理。
- 合并为 `serverInfo`。

3. 不在 `service_helper.go` 中直接暴露裸 etcd 读写 helper。

### 9.4 `pkg/mvservice/service.go`

1. `refreshServersIfDue` 适配双信号。
2. `Run` 中按双信号做轻重分流。
3. 保持 `fetch -> rebuild -> dispatch` 主体不变。

### 9.5 新增模块建议

1. `pkg/mvservice/coordination.go`
2. `pkg/mvservice/load_reporter.go`
3. `pkg/mvservice/weight_manager.go`

## 10. 配置与开关策略

1. 自动调权需要有明确的启停与回退控制面。
2. 推荐控制面统一抽象为 `RebalanceControl.Enabled`，由 Domain 侧实现并通过 `MVCoordination.LoadRebalanceControl` 暴露给 `WeightManager`。
3. 默认值建议为 `Enabled=false`，先灰度开启，再逐步扩大范围。
4. 最终控制面采用全局 sysvar 实现；它天然是集群级别，不支持按节点灰度。
5. 发布策略只能按环境或集群维度推进，不能在同一集群内做节点级灰度。
6. 混部阶段必须保持 `tidb_mview_rebalance_enabled=OFF`；只有所有 TiDB 节点都升级到支持 V2 的版本后，才允许开启该功能。
7. 若用户在混部阶段将 `tidb_mview_rebalance_enabled` 设为 `ON`，系统不要求在设置阶段报错；但 `WeightManager` 必须在运行时检测到“集群能力未齐”后强制按 `Enabled=false` 处理，并记录明确日志与监控事件。
8. “集群能力未齐”建议直接定义为：当前 live membership 中，存在任一节点缺少 fresh `node_load/<exec_id>`。
9. 不应把“手工清理 etcd 前缀”作为唯一回退手段。
10. 当 `Enabled=false` 时，`WeightManager` 应通过写入空 `task_weight_snapshot` 清空权重集合，并停止后续写入。
11. 当 `LoadRebalanceControl` 读取失败时：

- 若当前进程尚无最近一次成功状态，则保守按 `Enabled=false` 处理。
- 若已有最近一次成功状态，则本轮沿用上次成功状态，不立即清空权重。
- 同时记录失败指标与限流日志。

12. 但“沿用上次成功状态”必须有失效边界：

- 连续 `6` 次读取失败，或
- 超过 `1m` 未成功读取控制面

时，强制回退为 `Enabled=false`。
13. 若控制面关闭后当前 leader 无法成功写入空 snapshot，则系统最坏会在“snapshot stale 阈值 + 一次 server refresh 周期”内回退到等权模型。

### 10.1 控制面 sysvar 契约

建议新增全局系统变量，例如：

1. `tidb_mview_rebalance_enabled`
2. Scope: `GLOBAL`
3. Persists to cluster: `Yes`
4. Applies to hint `SET_VAR`: `No`
5. Type: `boolean`
6. Default: `OFF`

要求：

1. Domain 在 sysvar 更新后刷新本地缓存，并驱动 `WeightManager` 重新读取控制状态。
2. `WeightManager` 不直接读取系统表或 session 状态，只通过 `LoadRebalanceControl` 获取结果。
3. 关闭该变量必须触发一次清权重动作，而不是仅停止后续写入。

## 11. 可观测性设计

### 11.1 新增指标建议

1. `tidb_mv_service_task_weight{exec_id}`（gauge）
2. `tidb_mv_service_weight_manager`（gauge）
3. `tidb_mv_service_run_event_total{type="node_load_update_ok|node_load_update_err"}`
4. `tidb_mv_service_run_event_total{type="rebalance_cluster_not_ready"}`
5. `tidb_mv_service_run_event_total{type="task_weight_snapshot_update_ok|task_weight_snapshot_update_err"}`
6. `tidb_mv_service_run_event_total{type="server_changed_topology|server_changed_weight"}`
7. `tidb_mv_service_run_event_total{type="rebalance_control_read_ok|rebalance_control_read_err"}`

## 12. 测试方案

### 12.1 单元测试

1. weighted ring 构建正确性（含 `weight=0`）。
2. `refresh` 双信号四象限。
3. 轻收敛路径（仅 weight changed）不触发 fetch。
4. 重收敛路径（topology changed）触发 fetch+rebuild。
5. stale/缺失 `task_weight_snapshot` 回退到等权行为。
6. 同一 TiDB 实例在 `exec_id` 不变、仅 `ddl_id` 变化时，不应触发 topology change，也不应导致 owner 漂移或 `node_load`/snapshot 身份变化。

### 12.2 集成测试

1. 三节点场景下构造单节点热点。
2. 验证热点节点权重下降、任务向其他节点迁移。
3. 验证负载恢复后权重逐级回升。
4. 验证 manager 切换后调权连续性。

### 12.3 故障注入

1. etcd 读失败：回退默认权重，不中断调度。
2. etcd 写失败：无写风暴，保留旧值。
3. stale `node_load`：节点自动降为 `weight=0`（受保底约束）。
4. 控制面读取失败：

- 冷启动阶段按 `Enabled=false` 处理。
- 非冷启动阶段沿用最近一次成功状态。
- 不允许因为一次控制面读失败就立即抖动清空全部权重。
- 连续 `6` 次失败或超过 `1m` 未成功读取后，强制回退为 `Enabled=false`。

5. `StoreTaskWeightSnapshot` 写入中断：

- 不允许留下混合代际的可见结果。
- 旧 snapshot 继续保持可见，下一轮允许按新 `epoch` 整体重试。

## 13. 发布与回退策略

1. 先在灰度环境或测试集群整体开启调权链路，观察 1~2 周期。
2. 重点观察：

- `exec_waiting`、`mv_refresh_warning`、`mv_refresh_overdue` 收敛
- ring 重建频率
- metadata fetch 频率
3. 需要明确一个运行语义：任一 live TiDB 节点缺少 fresh `node_load/<exec_id>` 时，系统会将集群视为 not ready，并整体回退到等权模式；这是预期保护行为，不应按故障误判。

3. 回退策略：

- 通过控制面将 `RebalanceControl.Enabled` 置为 `false`。
- `WeightManager` 收到关闭状态后写入空 `task_weight_snapshot`。
- `MVService` 在读不到或读不到有效 `task_weight_snapshot` 时自动回退到等权一致性哈希，无需停服。

## 14. 验收标准（Exit Criteria）

1. 正确性：重复执行窗口内结果一致，无数据错误。
2. 稳定性：无重建/拉取风暴。
3. 收敛性：热点节点等待与告警指标可观测下降。
4. 可回退性：回退到等权后系统持续可执行。
5. 可发布性：默认关闭时系统行为与当前版本一致；开启后可在独立环境中单独验证负载感知链路。

## 15. 实施顺序（建议）

1. 第 1 步：扩展 `serverInfo` 与 weighted ring 能力（不启用调权）。
2. 第 2 步：引入 `task_weight_snapshot` 读取与双信号 refresh。
3. 第 3 步：实现 LoadReporter + WeightManager。
4. 第 4 步：接入监控面板。
5. 第 5 步：灰度、压测、故障演练后全量启用。

## 16. Commit Plan（函数级拆分）

### Commit 1: Weighted server model + dual signal scaffold

1. 文件：

- `pkg/mvservice/server_maintainer.go`
- `pkg/mvservice/server_maintainer_test.go`

2. 改动：

- `serverInfo` 增加权重相关字段（raw/effective）。
- `refresh()` 返回 `(topologyChanged, weightChanged bool, err error)`。
- 抽出成员变化与权重变化判定函数。

3. 验收：

- 四象限 UT（TT/TF/FT/FF）通过。

### Commit 2: ConsistentHash 支持节点级 replicas

1. 文件：

- `pkg/mvservice/consistenthash.go`
- `pkg/mvservice/utils_test.go`（或专用测试文件）

2. 改动：

- 新增 `RebuildWithReplicas(map[string]int)`。
- 保留 `Rebuild(nodes)` 兼容路径。
- `replicas=0` 节点不入 ring。

3. 验收：

- `weight=0` 不分配。
- 分布稳定性 UT 通过。

### Commit 3: 权重 overlay 读取（先只读）

1. 文件：

- `pkg/mvservice/coordination.go`（新）
- `pkg/mvservice/service_helper.go`
- `pkg/mvservice/task_handler_test.go`

2. 改动：

- 定义 `MVCoordination` 窄接口。
- `RegisterMVService` 增加 `MVCoordination` 注入。
- `newServiceHelper` 仅接收 `TaskWeightSnapshotReader` 子接口。
- `getAllServerInfo(ctx)` 通过 `MVCoordination` 读取 `task_weight_snapshot` 并 merge。
- 解析失败/stale/缺失 snapshot 回退默认权重 `100`。
- 计算 effective replicas（base=100）。

3. 验收：

- etcd 不可用时行为与等权一致。
- stale/坏值 UT 通过。

### Commit 4: Run 主循环接双信号

1. 文件：

- `pkg/mvservice/service.go`
- `pkg/mvservice/task_handler_test.go`

2. 改动：

- `refreshServersIfDue` 接双信号。
- `Run` 分流：
    - `topologyChanged` => fetch + rebuild。
    - `weightChanged && !topologyChanged` => rebuild。
- 新增 `server_changed_topology/server_changed_weight` run event。

3. 验收：

- “仅权重变化不全量 fetch” UT 通过。

### Commit 5: LoadReporter（每节点负载上报）

1. 文件：

- `pkg/mvservice/load_reporter.go`（新）
- `pkg/mvservice/mvservice_test.go`

2. 改动：

- 周期采样 CPU/MEM + MV 关键指标。
- 通过 `NodeLoadWriter` 写 `node_load/<exec_id>`。
- 失败限流日志。

3. 验收：

- 上报格式与周期行为 UT 通过。

### Commit 6: WeightManager（leader 计算并写权重）

1. 文件：

- `pkg/mvservice/weight_manager.go`（新）
- `pkg/mvservice/mvservice_test.go`

2. 改动：

- 通过 `WeightManagerElector` 完成 lease/lock 选主。
- 通过 `NodeLoadReader` 读取全量 `node_load`。
- 通过 `TaskWeightSnapshotWriter.StoreTaskWeightSnapshot` 原子写入完整权重快照。
- 通过 `RebalanceControlReader` 读取启停控制状态。
- 档位算法 + 防抖 + 冷却 + 单步变化 + 至少一节点 `weight>0`。
- 即使权重不变，也按心跳周期刷新 snapshot 的 `updated_at_unix_sec`。
- session 失效后拒绝继续写权重。
- 控制面关闭时写入空快照并停止后续写入。
- 控制面读失败时按“冷启动默认关闭、非冷启动沿用上次状态”处理。
- 控制面连续失败超过阈值后强制回退为 `Enabled=false`。

3. 验收：

- leader 切换、防抖/冷却、保底约束 UT 通过。
- 控制面关闭与恢复 UT 通过。
- `WeightManagerSession` 失效 UT 通过。

### Commit 7: Domain 生命周期接线

1. 文件：

- `pkg/domain/domain.go`

2. 相关文件：

- `pkg/mvservice/service_helper.go`

3. 改动：

- 实现 `MVCoordination` 的 domain 侧适配。
- 启停 LoadReporter + WeightManager goroutine。
- 与现有 MVService 生命周期对齐。
- 将控制面实现统一收口到 Domain 侧，避免 `mvservice` 直接读取外部配置。

4. 验收：

- 启停链路 UT，无 goroutine leak。

### Commit 8: Metrics

1. 文件：

- `pkg/metrics/materialized_view.go`
- `pkg/mvservice/metrics_reporter.go`

2. 改动：

- 增加 weight/manager/load 指标。

3. 验收：

- metrics 采集与展示正常。

### Commit 9: 文档与 Grafana

1. 文件：

- `mvservice_load_aware_design2.md`
- `pkg/metrics/grafana/tidb.json`（如需）

2. 改动：

- 文档与实现对齐。
- 增加面板查询与字段说明。

3. 验收：

- 文档无过期描述，指标可观测。

### 每个 Commit 的统一检查

1. `gofmt`
2. `go test ./pkg/mvservice --tags=intest`
3. 关联包最小回归：`pkg/domain`、`pkg/server/handler/tests`、`pkg/metrics`
4. `nogo/revive/license` 检查通过

## 17. 并行开发看板（建议）

| ID | 任务 | 负责人 | 依赖 | 预计工时 | 交付物 |
| --- | --- | --- | --- | --- | --- |
| W1 | `server_maintainer` 双信号改造 | Core-A | 无 | 1.0d | `refresh()` 双信号 + 四象限 UT |
| W2 | `consistenthash` 支持节点级 replicas | Core-B | 无 | 0.8d | `RebuildWithReplicas` + 分布 UT |
| W3 | `service_helper` 权重快照读取 | Core-A | W1,W2 | 1.0d | membership+snapshot merge + stale 回退 |
| W4 | `service.Run` 轻重收敛分流 | Core-A | W1,W3 | 1.0d | `topologyChanged/weightChanged` 分流 |
| W5 | LoadReporter 实现 | Infra-C | 无 | 1.2d | `load_reporter.go` + 上报 UT |
| W6 | WeightManager 实现 | Infra-C | W5 | 1.8d | `weight_manager.go` + 选主/防抖 UT |
| W7 | Domain 生命周期接线 | Core-B | W5,W6 | 0.8d | 启停接线 + leak-free UT |
| W8 | Metrics | Obs-D | W4,W6 | 1.0d | 指标与采集验证 |
| W9 | Grafana + 文档对齐 | Obs-D | W8 | 0.6d | `tidb.json` + 文档更新 |
| W10 | 集成测试与故障注入 | QA-E | W4,W7,W8 | 1.5d | 3 节点迁移/回退/故障注入用例 |

### 17.1 关键路径

1. `W1 -> W3 -> W4 -> W8 -> W10`
2. `W5 -> W6 -> W7 -> W10`

### 17.2 里程碑

1. M1（可编译 + 主链路单测）：完成 `W1~W4`
2. M2（调权闭环可运行）：完成 `W5~W7`
3. M3（可观测 + 可验证）：完成 `W8~W10`

### 17.3 风险与预案

1. etcd 读写抖动导致频繁重建：

- 预案：WeightManager 防抖/冷却 + Run 侧回退限流。

2. 混部版本行为不一致：

- 预案：保持 at-least-once 语义，不引入强一致阻塞点。

3. 指标与日志噪声过高：

- 预案：限制高基数标签，补充日志去重与节流。

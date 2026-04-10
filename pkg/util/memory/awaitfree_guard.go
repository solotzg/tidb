// Copyright 2026 PingCAP, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package memory

// AwaitFreeGuard maintains quota/heap-inuse accounting against the global
// await-free pool in a best-effort, non-blocking way.
//
// NOTE:
//  1. It is not goroutine-safe.
//  2. `Grow` and `AdjustTo` are best-effort and never fail.
type AwaitFreeGuard struct {
	budget     *TrackedConcurrentBudget
	arbitrator *MemArbitrator
	used       int64
}

// NewAwaitFreeGuard creates an await-free guard on the global mem arbitrator.
func NewAwaitFreeGuard(uid uint64) AwaitFreeGuard {
	return NewAwaitFreeGuardWithArbitrator(GlobalMemArbitrator(), uid)
}

// NewAwaitFreeGuardWithArbitrator creates an await-free guard on a specific mem arbitrator.
func NewAwaitFreeGuardWithArbitrator(m *MemArbitrator, uid uint64) AwaitFreeGuard {
	if m == nil || uid == 0 {
		return AwaitFreeGuard{}
	}
	return AwaitFreeGuard{
		budget:     m.GetAwaitFreeBudgets(uid),
		arbitrator: m,
	}
}

// Enabled indicates whether the await-free accounting is active.
func (g *AwaitFreeGuard) Enabled() bool {
	return g != nil && g.budget != nil
}

// Used returns current accounted bytes.
func (g *AwaitFreeGuard) Used() int64 {
	if g == nil {
		return 0
	}
	return g.used
}

// Arbitrator returns the backing mem arbitrator.
func (g *AwaitFreeGuard) Arbitrator() *MemArbitrator {
	if g == nil {
		return nil
	}
	return g.arbitrator
}

// Grow grows accounted bytes by `delta`.
func (g *AwaitFreeGuard) Grow(delta int64) {
	if g == nil || delta <= 0 {
		return
	}
	if !g.Enabled() {
		g.used += delta
		return
	}
	_ = g.budget.ConsumeQuota(g.arbitrator.ApproxUnixSec(), delta)
	g.budget.ReportHeapInuse(delta)
	g.used += delta
}

// Shrink decreases accounted bytes by `delta`.
func (g *AwaitFreeGuard) Shrink(delta int64) {
	if g == nil || delta <= 0 {
		return
	}
	if delta > g.used {
		delta = g.used
	}
	if delta <= 0 {
		return
	}
	if g.Enabled() {
		_ = g.budget.ConsumeQuota(g.arbitrator.ApproxUnixSec(), -delta)
		g.budget.ReportHeapInuse(-delta)
	}
	g.used -= delta
}

// AdjustTo adjusts accounted bytes to `target`.
func (g *AwaitFreeGuard) AdjustTo(target int64) {
	if g == nil {
		return
	}
	if target < 0 {
		target = 0
	}
	if target > g.used {
		g.Grow(target - g.used)
		return
	}
	g.Shrink(g.used - target)
}

// Close releases all accounted bytes.
func (g *AwaitFreeGuard) Close() {
	if g == nil || g.used <= 0 {
		return
	}
	g.Shrink(g.used)
}

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

package core

import (
	"context"

	"github.com/pingcap/tidb/pkg/expression"
	metamodel "github.com/pingcap/tidb/pkg/meta/model"
	"github.com/pingcap/tidb/pkg/planner/core/base"
	"github.com/pingcap/tidb/pkg/planner/core/operator/logicalop"
	"github.com/pingcap/tidb/pkg/util"
	"github.com/pingcap/tidb/pkg/util/dbterror/plannererrors"
	"github.com/pingcap/tipb/go-tipb"
)

const maxFTSTopK = ^uint32(0)

// FullTextIndexPlanVisitor finds DataSource nodes directly below a selection.
// The name is retained for compatibility with the existing optimizer rule, but
// MATCH ... AGAINST no longer requires a physical FULLTEXT index.
type FullTextIndexPlanVisitor struct {
	parents           []base.LogicalPlan
	onEnterDataSource func(v *FullTextIndexPlanVisitor, ds *logicalop.DataSource) (bool, error)
}

func (v *FullTextIndexPlanVisitor) getParent(n int) base.LogicalPlan {
	idx := len(v.parents) - 1 - n
	if idx >= 0 {
		return v.parents[idx]
	}
	return nil
}

func (v *FullTextIndexPlanVisitor) visit(plan base.LogicalPlan) (bool, error) {
	if ds, ok := plan.(*logicalop.DataSource); ok {
		return v.onEnterDataSource(v, ds)
	}
	v.parents = append(v.parents, plan)
	changed := false
	for _, child := range plan.Children() {
		childChanged, err := v.visit(child)
		if err != nil {
			return false, err
		}
		changed = changed || childChanged
	}
	v.parents = v.parents[:len(v.parents)-1]
	return changed, nil
}

// FullTextIndexResolverWhere resolves supported MATCH ... AGAINST predicates
// to a TiFlash table-scan filter. It intentionally carries query metadata even
// when the table has no FULLTEXT index: the product contract is a
// syntax-compatible row matcher with an optional TiFlash fast path.
type FullTextIndexResolverWhere struct{}

func (*FullTextIndexResolverWhere) Name() string { return "fts_resolve_index_where" }

func (o *FullTextIndexResolverWhere) Optimize(_ context.Context, plan base.LogicalPlan) (base.LogicalPlan, bool, error) {
	visitor := &FullTextIndexPlanVisitor{onEnterDataSource: o.onEnterDataSource}
	changed, err := visitor.visit(plan)
	return plan, changed, err
}

func (*FullTextIndexResolverWhere) onEnterDataSource(v *FullTextIndexPlanVisitor, ds *logicalop.DataSource) (bool, error) {
	parent := v.getParent(0)
	selection, ok := parent.(*logicalop.LogicalSelection)
	if !ok || len(selection.Conditions) == 0 {
		return false, nil
	}

	var ftsInfo *expression.FTSInfo
	var ftsExpr expression.Expression
	conditionIndex := -1
	for i, cond := range selection.Conditions {
		if info := expression.InterpretFullTextSearchExpr(cond); info != nil {
			ftsInfo, ftsExpr, conditionIndex = info, cond, i
			break
		}
	}
	if ftsInfo == nil || !ftsInfo.IsMatchAgainst {
		return false, nil
	}
	if sf, ok := ftsExpr.(*expression.ScalarFunction); ok {
		if _, local := expression.FTSMysqlMatchAgainstLocalEvalInfo(sf); local {
			// Local TiDB evaluation remains in the selection.
			return false, nil
		}
	}

	const parserType = metamodel.FullTextParserTypeStandardV1
	topK := maxFTSTopK
	booleanQuery, err := expression.BuildFTSBooleanQuery(ftsInfo.Query, parserType)
	if err != nil {
		return false, plannererrors.ErrWrongUsage.FastGen("unsupported BOOLEAN MODE full-text query: %s", err)
	}
	queryInfo := &tipb.FTSQueryInfo{
		QueryType:      tipb.FTSQueryType_FTSQueryTypeNoScore,
		QueryText:      ftsInfo.Query,
		QueryTokenizer: string(parserType),
		TopK:           &topK,
		QueryFunc:      tipb.ScalarFuncSig_FTSMatchExpression,
		BooleanQuery:   booleanQuery,
	}
	for _, column := range ftsInfo.Columns {
		queryInfo.Columns = append(queryInfo.Columns, util.ColumnToProto(column.ToInfo(), false, false))
		queryInfo.ColumnNames = append(queryInfo.ColumnNames, column.OrigName)
	}

	// IndexInfo is intentionally nil. TiFlash consumes the query and document
	// columns from QueryInfo; no persistent FULLTEXT index is required.
	ds.FtsPushDown = &logicalop.FTSPushDown{QueryInfo: queryInfo}

	newConditions := make([]expression.Expression, 0, len(selection.Conditions)-1)
	newConditions = append(newConditions, selection.Conditions[:conditionIndex]...)
	newConditions = append(newConditions, selection.Conditions[conditionIndex+1:]...)
	selection.Conditions = newConditions
	if len(selection.Conditions) == 0 {
		removeSelectionNode(v, selection)
	}
	return true, nil
}

func removeSelectionNode(v *FullTextIndexPlanVisitor, selection *logicalop.LogicalSelection) {
	parent := v.getParent(1)
	if parent == nil {
		return
	}
	childIndex := -1
	for i, child := range parent.Children() {
		if child == selection {
			childIndex = i
			break
		}
	}
	if childIndex < 0 {
		return
	}
	children := make([]base.LogicalPlan, 0, len(parent.Children())-1+len(selection.Children()))
	children = append(children, parent.Children()[:childIndex]...)
	children = append(children, selection.Children()...)
	children = append(children, parent.Children()[childIndex+1:]...)
	parent.SetChildren(children...)
}

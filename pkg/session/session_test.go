// Copyright 2024 PingCAP, Inc.
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

package session

import (
	"testing"

	"github.com/pingcap/tidb/pkg/ddl"
	"github.com/stretchr/testify/require"
)

func TestGetStartMode(t *testing.T) {
	require.Equal(t, ddl.Normal, getStartMode(currentBootstrapVersion))
	require.Equal(t, ddl.Normal, getStartMode(currentBootstrapVersion+1))
	require.Equal(t, ddl.Upgrade, getStartMode(currentBootstrapVersion-1))
	require.Equal(t, ddl.Bootstrap, getStartMode(0))
}

func TestMemArbitratorSession(t *testing.T) {
	require.Equal(t, int64(15), approxParseSQLTokenCnt("/*select * from **/SELECT x FROM `t\\`` # abc \nwhere a = 1.23 and b = 'abc\"d\\'e' -- abc \nand c_1_2 in \"abc'd\\\"e\" # (1,2,3)\n"))
	require.Equal(t, int64(0), approxParseSQLTokenCnt("select @@version @a")) // not select ... from ...
	require.Equal(t, int64(0), approxParseSQLTokenCnt("set @a=1"))
	require.Equal(t, int64(4), approxParseSQLTokenCnt("desc analyze table t"))
	require.Equal(t, int64(3), approxParseSQLTokenCnt("analyze table t"))
	require.Equal(t, int64(0), approxParseSQLTokenCnt("/*select * from **/explain show warnings"))
	require.Equal(t, int64(0), approxParseSQLTokenCnt("/*select * from **/desc show columns from t"))
	require.Equal(t, int64(5), approxParseSQLTokenCnt("insert into t values 1"))
	require.Equal(t, int64(5), approxParseSQLTokenCnt("update t set a=1"))
	require.Equal(t, int64(6), approxParseSQLTokenCnt("delete from t where a=1"))
	require.Equal(t, int64(5), approxParseSQLTokenCnt("replace into t values 1"))
	require.Equal(t, int64(6), approxParseSQLTokenCnt("execute stmt1 using @a,@b,@c"))
	require.Equal(t, int64(6), approxParseSQLTokenCnt("execute stmt1 using @a,@b,@c"))
	require.Equal(t, int64(10), approxParseSQLTokenCnt("select * from `a_1`.`b_2` where c1 = ? and c2 = ?"))
	require.Equal(t, int64(9), approxCompilePlanTokenCnt("select * from `a_1`.`b_2` where c1 = ? and c2 = ?", true))
	require.Equal(t, int64(0), approxCompilePlanTokenCnt("select @@version @a", true))
	require.Equal(t, int64(3), approxCompilePlanTokenCnt("select @@version @a", false))
}

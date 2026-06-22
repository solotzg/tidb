// Copyright 2016 PingCAP, Inc.
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

package kv

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func requireCoprRequestLimiterCapacity(t *testing.T, limiter CoprRequestLimiter, expected int) {
	t.Helper()
	require.Equal(t, expected, limiter.Capacity())
}

func TestCoprRequestLimiterWaitsUntilRelease(t *testing.T) {
	limiter := NewCoprRequestRateLimit(1)
	done := make(chan struct{})
	require.False(t, limiter.Acquire(done))

	acquired := make(chan struct{})
	acquireExit := make(chan bool, 1)
	go func() {
		exit := limiter.Acquire(done)
		acquireExit <- exit
		close(acquired)
		if !exit {
			limiter.Release()
		}
	}()

	select {
	case <-acquired:
		require.Fail(t, "second acquire should wait until release")
	case <-time.After(10 * time.Millisecond):
	}

	limiter.Release()
	select {
	case <-acquired:
	case <-time.After(time.Second):
		require.Fail(t, "second acquire should be admitted after release")
	}
	require.False(t, <-acquireExit)
}

func TestCoprRequestLimiterAcquireCanBeCanceled(t *testing.T) {
	limiter := NewCoprRequestRateLimit(1)
	require.False(t, limiter.Acquire(make(chan struct{})))

	done := make(chan struct{})
	result := make(chan bool)
	var acquireStarted atomic.Bool
	go func() {
		acquireStarted.Store(true)
		result <- limiter.Acquire(done)
	}()

	require.Eventually(t, func() bool {
		return acquireStarted.Load()
	}, time.Second, time.Millisecond)
	time.Sleep(10 * time.Millisecond)
	close(done)
	require.True(t, <-result)

	limiter.Release()
	require.False(t, limiter.Acquire(make(chan struct{})))
	limiter.Release()
}

func TestCoprRequestLimiterRedundantReleasePanics(t *testing.T) {
	limiter := NewCoprRequestRateLimit(1)
	require.Panics(t, func() {
		limiter.Release()
	})
}

func TestCoprRequestLimiterConcurrentAcquireRelease(t *testing.T) {
	const capacity = int64(3)
	limiter := NewCoprRequestRateLimit(int(capacity))
	done := make(chan struct{})
	var active atomic.Int64
	var maxActive atomic.Int64
	var acquireExit atomic.Bool
	var capacityExceeded atomic.Bool
	var wg sync.WaitGroup

	for range 32 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 20 {
				if limiter.Acquire(done) {
					acquireExit.Store(true)
					return
				}
				cur := active.Add(1)
				if cur > capacity {
					capacityExceeded.Store(true)
				}
				for {
					old := maxActive.Load()
					if cur <= old || maxActive.CompareAndSwap(old, cur) {
						break
					}
				}
				time.Sleep(time.Millisecond)
				active.Add(-1)
				limiter.Release()
			}
		}()
	}

	waitCh := make(chan struct{})
	go func() {
		wg.Wait()
		close(waitCh)
	}()

	select {
	case <-waitCh:
	case <-time.After(5 * time.Second):
		require.Fail(t, "concurrent acquire/release should finish")
	}
	require.False(t, acquireExit.Load())
	require.False(t, capacityExceeded.Load())
	require.LessOrEqual(t, maxActive.Load(), capacity)
	require.Equal(t, int64(0), active.Load())
}

func TestCompositeCoprRequestLimiter(t *testing.T) {
	limiter1 := NewCoprRequestRateLimit(1)
	limiter2 := NewCoprRequestRateLimit(2)
	limiter := NewCompositeCoprRequestLimiter(limiter1, nil, limiter2)
	require.NotNil(t, limiter)
	requireCoprRequestLimiterCapacity(t, limiter, 1)

	done := make(chan struct{})
	require.False(t, limiter.Acquire(done))

	blocked := make(chan struct{})
	go func() {
		defer close(blocked)
		require.False(t, limiter.Acquire(done))
		limiter.Release()
	}()

	select {
	case <-blocked:
		require.Fail(t, "second acquire should wait for the first release")
	case <-time.After(10 * time.Millisecond):
	}

	limiter.Release()
	select {
	case <-blocked:
	case <-time.After(time.Second):
		require.Fail(t, "second acquire should succeed after release")
	}
}

func TestCompositeCoprRequestLimiterReleasesAcquiredTokensOnExit(t *testing.T) {
	limiter1 := NewCoprRequestRateLimit(1)
	limiter2 := NewCoprRequestRateLimit(1)
	require.False(t, limiter2.Acquire(make(chan struct{})))
	defer limiter2.Release()

	limiter := NewCompositeCoprRequestLimiter(limiter1, limiter2)
	done := make(chan struct{})
	acquired := make(chan struct{})
	result := make(chan bool)
	go func() {
		close(acquired)
		exit := limiter.Acquire(done)
		if !exit {
			limiter.Release()
		}
		result <- exit
	}()
	<-acquired
	time.Sleep(10 * time.Millisecond)
	close(done)
	require.True(t, <-result)

	require.False(t, limiter1.Acquire(make(chan struct{})))
	limiter1.Release()
}

func TestInterface(t *testing.T) {
	storage := newMockStorage()
	storage.GetClient()
	storage.UUID()
	version, err := storage.CurrentVersion(GlobalTxnScope)
	assert.Nil(t, err)

	snapshot := storage.GetSnapshot(version)
	_, err = snapshot.BatchGet(context.Background(), []Key{Key("abc"), Key("def")})
	assert.Nil(t, err)

	snapshot.SetOption(Priority, PriorityNormal)
	transaction, err := storage.Begin()
	assert.Nil(t, err)
	assert.NotNil(t, transaction)

	err = transaction.LockKeys(context.Background(), new(LockCtx), Key("lock"))
	assert.Nil(t, err)

	transaction.SetOption(23, struct{}{})
	if mock, ok := transaction.(*mockTxn); ok {
		mock.GetOption(23)
	}
	transaction.StartTS()
	if transaction.IsReadOnly() {
		_, err = transaction.Get(context.TODO(), Key("lock"))
		assert.Nil(t, err)
		err = transaction.Set(Key("lock"), []byte{})
		assert.Nil(t, err)
		_, err = transaction.Iter(Key("lock"), nil)
		assert.Nil(t, err)
		_, err = transaction.IterReverse(Key("lock"), nil)
		assert.Nil(t, err)
	}
	_ = transaction.Commit(context.Background())

	transaction, err = storage.Begin()
	assert.Nil(t, err)

	// Test for mockTxn interface.
	assert.Equal(t, "", transaction.String())
	assert.True(t, transaction.Valid())
	assert.Equal(t, 0, transaction.Len())
	assert.Equal(t, 0, transaction.Size())
	assert.Nil(t, transaction.GetMemBuffer())

	transaction.(*mockTxn).Reset()
	err = transaction.Rollback()
	assert.Nil(t, err)
	assert.False(t, transaction.Valid())
	assert.False(t, transaction.IsPessimistic())
	assert.Nil(t, transaction.Delete(nil))

	assert.Nil(t, storage.GetOracle())
	assert.Equal(t, "KVMockStorage", storage.Name())
	assert.Equal(t, "KVMockStorage is a mock Store implementation, only for unittests in KV package", storage.Describe())
	assert.False(t, storage.SupportDeleteRange())

	status, err := storage.ShowStatus(context.Background(), "")
	assert.Nil(t, status)
	assert.Nil(t, err)

	err = storage.Close()
	assert.Nil(t, err)
}

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

package kv

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCompositeCoprRequestLimiter(t *testing.T) {
	limiter1 := NewCoprRequestRateLimit(1)
	limiter2 := NewCoprRequestRateLimit(2)
	limiter := NewCompositeCoprRequestLimiter(limiter1, nil, limiter2)
	require.NotNil(t, limiter)
	require.Equal(t, 1, limiter.Capacity())

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

# MATCH ... AGAINST Boolean pushdown to TiFlash

This is a living implementation plan. It records the limited TiDB-side work
for the #70484/#70485 feature scope and must be updated as milestones finish.

## Purpose / Big Picture

After this change, a direct `MATCH(columns) AGAINST(search IN BOOLEAN MODE)`
predicate can be evaluated by TiFlash when the referenced table has an
available TiFlash replica and matching FULLTEXT indexes. TiDB will send the
parsed Boolean query through `tipb.FTSQueryInfo.boolean_query`. Queries that
need relevance scores, natural-language ranking, query expansion, TiKV, or
TiCI remain outside this plan.

## Progress

- [x] (2026-09-18) Confirm the current branch shares the #70484 base commit and
  that the tipb protocol contains the Boolean query message family.
- [x] (2026-09-18) Import the #70484 analyzer/query matcher and #70485 local
  Boolean evaluation commits without unrelated branch changes.
- [x] (2026-09-18) Extend TiDB expression/planner metadata so a supported
  Boolean MATCH carries its columns, parser type, and Boolean AST to the FTS
  resolver.
- [x] (2026-09-18) Emit `tipb.FTSQueryInfo.boolean_query` for TiFlash
  pushdown while keeping old `FTS_MATCH_WORD()` requests compatible.
- [x] (2026-09-18) Route local evaluation only when TiFlash native execution is
  not viable.
- [x] (2026-09-18) Add targeted planner/protocol regression coverage. TiFlash
  now consumes the serialized Boolean AST as an explicit function argument;
  TiDB `make bazel_prepare` and `make lint` now pass; full TiFlash executable
  linkage/runtime validation remains pending.

## Surprises & Discoveries

- The current TiDB branch does not contain the #70484/#70485 working-tree
  files, but its history has the exact common parent `052084c303`; the remote
  PR branches can therefore be applied commit-by-commit without merging their
  unrelated branch history.
- The tipb dependency already exposes `FTSBooleanQuery`, while the local
  `fts_resolve_index.go` only recognizes the internal `FTS_MATCH_WORD()`
  function. Protocol availability and planner emission are separate tasks.
- The existing TiDB FULLTEXT model supports STANDARD_V1 and
  MULTILINGUAL_V1. The first TiFlash implementation has STANDARD behavior only;
  planner pushdown must not silently claim parser parity for unsupported parser
  types.
- TiDB's FTS resolver was gated by `StmtCtx.FTSFunctionIsUsed`, but the
  `MATCH ... AGAINST` builtin did not set that flag. The builtin now marks it,
  otherwise a valid native Boolean expression remains an ordinary Selection.
- TiFlash's scalar FTS function has no direct access to the table-scan's
  `FTSQueryInfo`. The table-scan planner appends a marker-prefixed serialized
  Boolean AST to the generated scalar expression, and the function consumes it
  instead of re-parsing the SQL search string.
- `STANDARD_V1` currently rejects parenthesized groups, proximity, and other
  scoring operators at the TiDB parser layer. The TiFlash protocol consumer
  therefore implements the term, phrase, prefix, required, optional, and
  prohibited nodes needed by the accepted #70484/#70485 syntax; it does not
  invent semantics for rejected extensions.

## Decision Log

- Decision: Apply only the relevant #70484/#70485 commits instead of merging
  the PR branches wholesale.
  Rationale: the PR branches contain unrelated changes relative to this working
  branch; cherry-picking the feature commits keeps the diff reviewable.
  Date/Author: 2026-09-18 / Codex.
- Decision: Use the existing `boolean_query` field and preserve old fields and
  field numbers.
  Rationale: this is the protocol already agreed by the tipb change, and old
  consumers can continue using `query_text` when the field is absent.
  Date/Author: 2026-09-18 / Codex.
- Decision: Do not add analyzer-configuration fields in this step.
  Rationale: the current TiFlash implementation uses the STANDARD default
  analyzer. Non-default `innodb_ft_*` settings must be gated or wired in a
  later compatibility step rather than silently producing different matches.
  Date/Author: 2026-09-18 / Codex.

## Outcomes & Retrospective

The TiDB planner/protocol path and TiFlash production/test translation units
compile. The targeted TiDB planner regression, Boolean AST conversion tests,
analyzer tests, and TiDB lint gate pass. The implementation is intentionally
limited to a single-column `STANDARD_V1` FULLTEXT index with default analyzer
settings; the full TiFlash gtest executable and an end-to-end TiDB+TiFlash
cluster have not yet been run.

## Context and Orientation

`pkg/expression/fulltext` and `pkg/expression/matchagainst` provide the pure-Go
STANDARD/NGRAM analyzer and Boolean query representation introduced by #70484.
`pkg/expression/builtin_fts.go` provides the #70485 local no-score evaluator.
`pkg/planner/core/expression_rewriter.go` decides whether a parsed SQL
`MatchAgainst` becomes a native builtin or a local/fallback expression.
`pkg/planner/core/fts_resolve_index.go` associates native FTS expressions with
FULLTEXT indexes and creates `tipb.FTSQueryInfo` for TiFlash table scans.

## Plan of Work

The dependency-free #70484 analyzer/matcher and the #70485 local evaluation
commits are imported and focused expression tests pass. The FTS expression
metadata and resolver now recognize the Boolean MATCH builtin. The resolver
serializes the Boolean AST, column list, query text, and parser type while
retaining `FTSMatchExpression` as the query function. The TiFlash table-scan
path forwards the serialized AST into the scalar expression and evaluates it
with the same STANDARD token/phrase/prefix semantics.

The rewriter regards Boolean mode as natively pushdownable only for a
supported STANDARD FULLTEXT index and available TiFlash replica. When native
execution is unavailable, #70485's local evaluator remains the fallback if
enabled; score and natural-language positions continue to use the existing
native semantics.

## Validation and Acceptance

At minimum, targeted expression tests must pass for analyzer boundaries,
phrases, prefixes, required/prohibited terms, NULL columns, and prepared-query
recompilation. Targeted planner tests must prove that a Boolean MATCH predicate
creates a FULLTEXT table-scan request with `boolean_query`, while unsupported
parser/configuration cases do not claim native TiFlash execution. The final
validation must also satisfy the repository's required `make bazel_prepare` and
`make lint` gates because Go files and Bazel metadata are affected.

## Idempotence and Recovery

Cherry-pick is safe to retry only after resolving or aborting an interrupted
cherry-pick. All source edits use normal Git patches. If the planner change is
not ready, the #70484/#70485 commits can be reviewed independently; do not
reset or discard unrelated working-tree changes.

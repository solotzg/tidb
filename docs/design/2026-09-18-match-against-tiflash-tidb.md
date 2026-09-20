# MATCH ... AGAINST Boolean pushdown to TiFlash

This is a living implementation plan. It records the limited TiDB-side work
for the #70484/#70485 feature scope and must be updated as milestones finish.

## Purpose / Big Picture

After this change, a direct `MATCH(columns) AGAINST(search IN BOOLEAN MODE)`
predicate can be evaluated by TiFlash when the referenced table has an
available TiFlash replica and string columns. No persistent FULLTEXT index is
required. TiDB will send the
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
- [x] (2026-09-20) Route to TiDB local evaluation automatically when TiFlash
  native execution is not viable; the compatibility system variable no longer
  gates the fallback.
- [x] (2026-09-20) Validate a no-index TiDB integration case and a TiDB +
  TiFlash playground case. TiFlash received a `FTSMatchExpression` request
  with the serialized Boolean AST and returned the expected rows.

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
- TiDB's FTS resolver is invoked before ordinary predicate pushdown. This is
  necessary because the product path does not require a FULLTEXT index and
  still needs the original table/column context to build `FTSQueryInfo`.
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

The TiDB planner/protocol path compiles. The targeted Boolean AST conversion
and local evaluator tests pass. The no-index TiDB integration test passes. A
TiDB + TiFlash playground test also passed with `MATCH(title)` and a
`FTSMatchExpression` Boolean query visible in the TiFlash DAG log. A fresh
full TiFlash rebuild remains blocked by the local clang-18/CoreFoundation SDK
compatibility error in third-party abseil, before the FTS translation units.

## Context and Orientation

`pkg/expression/fulltext` and `pkg/expression/matchagainst` provide the pure-Go
STANDARD/NGRAM analyzer and Boolean query representation introduced by #70484.
`pkg/expression/builtin_fts.go` provides the #70485 local no-score evaluator.
`pkg/planner/core/expression_rewriter.go` decides whether a parsed SQL
`MatchAgainst` becomes a native builtin or a local/fallback expression.
`pkg/planner/core/fts_resolve_index.go` associates native FTS expressions with
an available TiFlash table scan and creates `tipb.FTSQueryInfo`; it does not
require persistent FULLTEXT index metadata.

## Plan of Work

The dependency-free #70484 analyzer/matcher and the #70485 local evaluation
commits are imported and focused expression tests pass. The FTS expression
metadata and resolver now recognize the Boolean MATCH builtin. The resolver
serializes the Boolean AST, column list, query text, and parser type while
retaining `FTSMatchExpression` as the query function. The TiFlash table-scan
path forwards the serialized AST into the scalar expression and evaluates it
with the same STANDARD token/phrase/prefix semantics.

The rewriter regards Boolean mode as natively pushdownable for a supported
STANDARD_V1 query, a string column, and an available TiFlash replica. When
native execution is unavailable, #70485's local evaluator is selected
automatically. Score and natural-language positions remain unsupported for
this release-8.5 delivery scope.

## Validation and Acceptance

At minimum, targeted expression tests must pass for analyzer boundaries,
phrases, prefixes, required/prohibited terms, NULL columns, and prepared-query
recompilation. Integration coverage must prove both no-index local fallback
and available-replica TiFlash execution. The remaining release gate is a
clean TiFlash rebuild on a compatible toolchain, followed by the normal TiDB
and TiFlash CI checks.

## Idempotence and Recovery

Cherry-pick is safe to retry only after resolving or aborting an interrupted
cherry-pick. All source edits use normal Git patches. If the planner change is
not ready, the #70484/#70485 commits can be reviewed independently; do not
reset or discard unrelated working-tree changes.

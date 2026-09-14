# Readable code standards
Keep source code understandable without requiring readers to reconstruct hidden
state or infer operational intent from database queries and Discord API calls.

## Structure
- Keep one clear responsibility per module.  Move pure validation, formatting,
  constants, and policy calculations out of Discord cogs and database adapters.
- Prefer focused helpers/services over long methods with unrelated phases.
  A method that validates input, mutates durable state, calls Discord, and
  formats output should normally delegate each phase to named helpers.
- When a Python module approaches 500 lines, consider splitting it.  Split it
  when it exceeds 800 lines unless the file is generated or the cohesion is
  clearly stronger than the cost of another module.
- Preserve public imports and cog-facing methods with small compatibility
  wrappers or aliases when extracting code; do not force unrelated callers to
  know the new internal layout.
- Avoid circular imports.  Put dependency-free helpers in support modules and
  let cogs/services depend on them, not the reverse.

## Comments and naming
- Use English for identifiers, comments, docstrings, exceptions, and source
  metadata.  Korean belongs only in localized user-facing catalogs or explicit
  legacy-data compatibility paths.
- Use inline comments generously to make code easy to read, especially in long,
  stateful, or unfamiliar flows. Explain both *what* each meaningful step does
  and *why* validations, locks, ordering rules, writes, retries, audits, and
  error paths are necessary; prefer a clear comment over leaving intent implicit.
- In transactional or asynchronous workflows, annotate the boundaries between
  validation, locking, durable persistence, external Discord calls, retry/
  rollback behavior, and member notification.
- Give public helpers a concise docstring that states their contract.  Use
  names that expose units, ownership, and state transitions.
- Do not add comments for self-evident assignments or syntax.  If every line
  needs narration, extract a well-named helper instead.

## Change discipline
- Keep functions small enough to scan.  Extract a helper once nested branches,
  repeated policy checks, or more than one independent responsibility obscure
  the happy path.
- Keep database queries parameterized.  If dynamic SQL is necessary, derive it
  only from a closed, validated set of values and document that invariant.
- Preserve idempotency and audit behavior when moving code.  Existing tests and
  public method names are compatibility contracts unless the task changes them.
- Run Black, syntax compilation, relevant unit tests, and `git diff --check`
  after structural work.  Add or update focused tests when an extraction changes
  a public boundary or policy behavior.

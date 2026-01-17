# Changelog

All notable changes to Armillaria will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-01-17

### Added

#### Core Storage (Phase 1)
- Content-addressable chunk store with BLAKE3 hashing
- Automatic deduplication via content addressing
- Atomic writes using write-to-temp-rename pattern
- Integrity verification with `get_verified()`

#### Versioned Catalog (Phase 2)
- File-based catalog with JSON metadata
- Sequential version enforcement
- Time travel queries to any historical version
- Table listing and version history

#### Query Layer (Phase 3)
- DuckDB integration for SQL queries
- TableWriter for DataFrame/Arrow table ingestion
- TableReader with chunked reading support
- QueryEngine with caching and multiple output formats

#### Git-like Branching (Phase 4)
- Zero-copy branch creation (branches are pointers, not copies)
- Branch isolation for safe experimentation
- Branch diffing and comparison
- Fast-forward merge support
- Checkout and branch switching

#### Cross-table ACID Transactions (Phase 5)
- Atomic multi-table commits
- Snapshot isolation with conflict detection
- Read-your-writes within transactions
- Automatic rollback on exceptions
- Transaction recovery after crashes

#### Changelog & Subscriptions (Phase 6)
- ChangelogEntry tracking for all commits
- ChangelogQuery builder with filtering
- Subscriber API for change notifications
- Polling, iterator, and background processing modes

#### Python Bindings
- PyO3-based bindings for all Rust functionality
- Type stubs for IDE support
- Pythonic API with context managers

### Security
- SQL injection protection in `diff_versions()`
- Path traversal protection in table names
- Input validation throughout

### Testing
- 127 Rust tests
- 153 Python tests including concurrency tests
- Clippy and Ruff linting (clean)

---

## Future Releases

See [udr_roadmap.md](./udr_roadmap.md) for planned features:
- Phase 7: Production migration tooling
- Phase 8: Distributed storage backends
- Phase 9: Advanced merge strategies

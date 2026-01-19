//! Distributed systems primitives for coordination-free transactions.
//!
//! This module provides the building blocks for distributed Rhizo deployments
//! where algebraic operations can commit locally without coordination.
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────┐
//! │                      Distributed Module                      │
//! ├─────────────────────────────────────────────────────────────┤
//! │  VectorClock     - Causality tracking                       │
//! │  (Future) LocalCommit   - Coordination-free commit protocol │
//! │  (Future) Gossip        - Anti-entropy propagation          │
//! └─────────────────────────────────────────────────────────────┘
//! ```
//!
//! # Theory
//!
//! Traditional distributed databases require consensus (Paxos/Raft) for
//! strong consistency. However, for algebraic operations (commutative +
//! associative), we can achieve strong eventual consistency without
//! coordination:
//!
//! - **Semilattice operations** (MAX, MIN, UNION): Idempotent merge
//! - **Abelian operations** (ADD, MULTIPLY): Combine deltas
//!
//! Vector clocks track causality to determine when merge is needed.
//!
//! # Example
//!
//! ```
//! use rhizo_core::distributed::{VectorClock, NodeId, CausalOrder};
//! use rhizo_core::algebraic::{OpType, AlgebraicMerger, AlgebraicValue};
//!
//! let node_a = NodeId::new("sf");
//! let node_b = NodeId::new("tokyo");
//!
//! // Node A increments counter
//! let mut clock_a = VectorClock::new();
//! clock_a.tick(&node_a);
//! let delta_a = AlgebraicValue::integer(5);
//!
//! // Node B increments counter (concurrent)
//! let mut clock_b = VectorClock::new();
//! clock_b.tick(&node_b);
//! let delta_b = AlgebraicValue::integer(3);
//!
//! // Detect concurrent updates
//! assert_eq!(clock_a.compare(&clock_b), CausalOrder::Concurrent);
//!
//! // Merge using algebraic properties (order doesn't matter!)
//! let merged = AlgebraicMerger::merge(
//!     OpType::AbelianAdd,
//!     &delta_a,
//!     &delta_b
//! );
//!
//! // Result: 5 + 3 = 8
//! if let rhizo_core::algebraic::MergeResult::Merged(v) = merged {
//!     assert_eq!(v, AlgebraicValue::integer(8));
//! }
//! ```

mod vector_clock;

pub use vector_clock::{CausalOrder, NodeId, VectorClock};

// Future modules (Phase 3+):
// mod local_commit;
// mod gossip;
// mod simulation;

# Coordination-Free Distributed Transactions

> **Status:** DESIGN PHASE
> **Goal:** Achieve distributed ACID with O(1) local commit latency for algebraic operations

## The Core Insight

Traditional distributed databases need consensus (Paxos/Raft) because:
- Multiple nodes might write different values to the same location
- Without agreement on ORDER, you get inconsistency

But for **algebraic operations**, order doesn't matter:
```
Node A: counter += 5
Node B: counter += 3
Result: counter += 8 (regardless of order!)
```

**Rhizo already has the algebraic classification.** We just need to:
1. Track causality (what happened before what)
2. Propagate changes between nodes
3. Merge using existing AlgebraicMerger

## Mathematical Foundation

### Theorem: Algebraic Operations Don't Need Consensus

**Given:**
- Operation type T with properties: commutative, associative
- Initial state S₀
- Operations O₁, O₂, ..., Oₙ (in any order)

**Claim:**
$$\forall \text{ permutations } \pi: \text{apply}(S_0, O_{\pi(1)}, O_{\pi(2)}, ..., O_{\pi(n)}) = S_{\text{final}}$$

**Proof:**
1. Commutativity: apply(S, O₁, O₂) = apply(S, O₂, O₁)
2. Associativity: apply(apply(S, O₁), O₂) = apply(S, apply(O₁, O₂))
3. By induction: Any permutation yields the same result
4. Therefore: No need to agree on order ∎

### Corollary: Local Commit is Safe

If a transaction T contains only algebraic operations:
- T can commit locally without coordination
- Other nodes will eventually see T
- All nodes will converge to the same state
- ACID properties are preserved through mathematics, not locks

## What We're NOT Changing

| Component | Status | Reason |
|-----------|--------|--------|
| ChunkStore | KEEP AS-IS | Content-addressing is perfect for distributed |
| Catalog core | KEEP AS-IS | Just add optional metadata |
| AlgebraicMerger | KEEP AS-IS | This IS the solution |
| Transaction API | KEEP AS-IS | Just extend with new mode |
| OLAP engine | KEEP AS-IS | Queries work the same |

## What We're Adding (New Code Only)

### 1. Vector Clocks (New Module)

```rust
// NEW: rhizo_core/src/distributed/vector_clock.rs

/// Logical timestamp for causality tracking
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VectorClock {
    /// Map from node_id to logical time
    clocks: HashMap<NodeId, u64>,
}

impl VectorClock {
    /// Increment this node's clock (on local event)
    pub fn tick(&mut self, node_id: &NodeId);

    /// Merge with another clock (on receiving message)
    pub fn merge(&mut self, other: &VectorClock);

    /// Check causality: did self happen before other?
    pub fn happened_before(&self, other: &VectorClock) -> bool;

    /// Are these concurrent (neither happened before the other)?
    pub fn concurrent(&self, other: &VectorClock) -> bool;
}
```

**Key insight:** Vector clocks tell us WHEN to merge, AlgebraicMerger tells us HOW.

### 2. Distributed Transaction Classification

```rust
// NEW: Extension to existing transaction

pub enum TransactionMode {
    /// Traditional: requires coordinator (existing behavior)
    Coordinated,

    /// New: can commit locally if all ops are algebraic
    CoordinationFree,
}

impl Transaction {
    /// Check if this transaction can commit without coordination
    pub fn can_commit_locally(&self) -> bool {
        self.operations.iter().all(|op| op.op_type.is_conflict_free())
    }
}
```

### 3. Gossip Protocol (Minimal)

```rust
// NEW: rhizo_core/src/distributed/gossip.rs

/// Simple anti-entropy protocol for state propagation
pub struct GossipProtocol {
    node_id: NodeId,
    peers: Vec<PeerAddress>,
    pending_updates: Vec<VersionedUpdate>,
}

impl GossipProtocol {
    /// Broadcast local commit to peers
    pub async fn broadcast(&self, update: VersionedUpdate);

    /// Receive update from peer, merge if needed
    pub async fn receive(&mut self, update: VersionedUpdate) -> MergeResult;
}
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Application                             │
├─────────────────────────────────────────────────────────────┤
│                   Transaction Layer                          │
│  ┌─────────────────┐    ┌─────────────────────────────────┐ │
│  │  Coordinated    │    │  Coordination-Free (NEW)        │ │
│  │  (existing)     │    │  - Vector clocks                │ │
│  │                 │    │  - Local commit                 │ │
│  │                 │    │  - Gossip propagation           │ │
│  └─────────────────┘    └─────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                   AlgebraicMerger (existing)                 │
│         Merge concurrent updates using math properties       │
├─────────────────────────────────────────────────────────────┤
│                   Catalog + ChunkStore (existing)            │
│              Content-addressed, immutable storage            │
└─────────────────────────────────────────────────────────────┘
```

## Consistency Guarantees

| Operation Type | Consistency Level | Latency |
|----------------|-------------------|---------|
| Algebraic (ADD, MAX, UNION) | Strong eventual | O(1) local |
| Non-algebraic (OVERWRITE) | Linearizable | O(consensus) |
| Mixed transaction | Linearizable | O(consensus) |

**Strong eventual consistency** means:
1. If no new updates, all nodes converge to same state
2. Convergent state is mathematically determined (not arbitrary)
3. Updates are never lost

## Failure Modes and Recovery

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Node crash before local commit | Transaction lost | Client retry |
| Node crash after local commit | Transaction durable locally | Gossip on restart |
| Network partition | Both sides continue locally | Merge on reconnect |
| Permanent node loss | Other nodes have copies via gossip | No action needed |

## Performance Expectations

| Metric | Traditional Distributed | Coordination-Free |
|--------|------------------------|-------------------|
| Write latency | 50-200ms (consensus) | 1-10ms (local) |
| Throughput | Limited by coordinator | Linear with nodes |
| Availability | Needs quorum | Any single node |

## Non-Goals (Explicit Scope Limits)

1. **Full distributed SQL** - Only algebraic operations are coordination-free
2. **Byzantine fault tolerance** - We trust nodes (crash faults only)
3. **Global transactions across non-algebraic columns** - Fall back to coordination
4. **Real-time sync** - Eventual consistency is acceptable

## Success Criteria

1. **Correctness:** Prove algebraic merge is equivalent regardless of order
2. **Performance:** <10ms commit latency for algebraic transactions
3. **Convergence:** All nodes reach same state within bounded time
4. **No regression:** All existing tests pass unchanged

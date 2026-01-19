# ACID Without Consensus: Algebraic Transactions for Geo-Distributed Data

> **Status:** DRAFT
> **Target Venue:** SIGMOD / VLDB / OSDI

---

## Abstract

Distributed databases traditionally require consensus protocols (Paxos, Raft) to achieve strong consistency, incurring significant latency overhead for geo-distributed deployments. We observe that many common operations—counters, max/min aggregates, set unions—have algebraic properties that mathematically guarantee convergence regardless of operation order. We present Rhizo, a distributed data system that classifies operations by their algebraic structure and commits transactions locally when all operations are algebraically conflict-free. For these workloads, Rhizo achieves O(1) local commit latency compared to O(consensus) for traditional systems, while maintaining strong eventual consistency with provable convergence. Our evaluation shows [X]x latency improvement and [Y]x throughput improvement for algebraic workloads, with identical results to coordination-based approaches.

---

## 1. Introduction

### 1.1 The Problem

Geo-distributed applications face a fundamental tension:
- **Strong consistency** (linearizability) requires coordination → high latency
- **Weak consistency** (eventual) avoids coordination → anomalies and conflicts

Example: A global e-commerce platform with users in San Francisco, London, and Tokyo. Traditional approaches:

| Approach | Latency (Tokyo write) | Consistency |
|----------|----------------------|-------------|
| Single leader (SF) | ~150ms RTT | Strong |
| Multi-Paxos | ~100ms (quorum) | Strong |
| Eventual consistency | ~5ms (local) | Weak |

The "eventual consistency" option is fast but risks lost updates, conflicting writes, and user-visible anomalies.

### 1.2 The Insight

Not all operations require coordination. Consider:

```sql
-- User A (San Francisco)
UPDATE counters SET page_views = page_views + 1;

-- User B (Tokyo), concurrent
UPDATE counters SET page_views = page_views + 1;
```

These operations **commute**: applying them in either order yields the same result (`page_views + 2`). No coordination is needed to determine order—the mathematical properties guarantee correctness.

### 1.3 Contributions

1. **Algebraic transaction classification:** A formal framework for identifying operations that commute, enabling coordination-free commit.

2. **Coordination-free commit protocol:** Transactions containing only algebraic operations commit locally with O(1) latency, propagate via gossip, and merge automatically.

3. **Rhizo system:** A full implementation demonstrating the approach with [benchmark results].

4. **Formal proofs:** Mathematical guarantees of convergence and consistency.

---

## 2. Background and Related Work

### 2.1 Consensus-Based Distributed Databases

**Spanner** [Corbett et al., 2012]: Uses TrueTime and Paxos. Achieves external consistency but requires synchronized clocks and quorum for every write.

**CockroachDB** [Taft et al., 2020]: Uses Raft consensus. Strong consistency but latency proportional to round-trip time between replicas.

**Calvin** [Thomson et al., 2012]: Deterministic execution avoids consensus during execution but requires global ordering of transactions beforehand.

### 2.2 Eventual Consistency and CRDTs

**Dynamo** [DeCandia et al., 2007]: Eventual consistency with vector clocks. Fast but requires application-level conflict resolution.

**CRDTs** [Shapiro et al., 2011]: Conflict-free Replicated Data Types. Mathematically guaranteed convergence for specific data structures.

Our work differs: CRDTs are data structures; we classify database **operations** algebraically and apply the insight to transactions.

### 2.3 Hybrid Approaches

**RedBlue consistency** [Li et al., 2012]: Classifies operations as "red" (coordination) or "blue" (local). Requires manual classification.

**CRDT-based databases** [Riak, Antidote]: Embed CRDTs as column types. Limited to predefined types.

**Our approach:** Automatic classification based on operation semantics, applicable to standard SQL operations.

---

## 3. Algebraic Transaction Model

### 3.1 Definitions

**Definition 1 (Algebraic Operation):** An operation $o$ is algebraic if it satisfies:
- **Commutative:** $o_1 \circ o_2 = o_2 \circ o_1$
- **Associative:** $(o_1 \circ o_2) \circ o_3 = o_1 \circ (o_2 \circ o_3)$

**Definition 2 (Operation Types):**

| Type | Properties | Examples |
|------|------------|----------|
| Semilattice | Commutative, Associative, Idempotent | MAX, MIN, UNION |
| Abelian Group | Commutative, Associative, Identity, Inverse | ADD, MULTIPLY |
| Generic | None | OVERWRITE, CAS |

**Definition 3 (Algebraic Transaction):** A transaction $T$ is algebraic iff all operations $o \in T$ are algebraic.

### 3.2 Theorem: Algebraic Transactions Converge

**Theorem 1:** If all transactions in a distributed system are algebraic, then all replicas converge to the same state regardless of message ordering.

*Proof sketch:* See Appendix A. Key insight: commutativity implies order independence; associativity implies grouping independence.

### 3.3 Corollary: No Coordination Needed

**Corollary 1:** Algebraic transactions can commit locally without coordination, and replicas will converge.

This is the theoretical foundation for our system.

---

## 4. System Design

### 4.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                       Client                                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
         v                v                v
    ┌─────────┐      ┌─────────┐      ┌─────────┐
    │ Node A  │←────→│ Node B  │←────→│ Node C  │
    │ (SF)    │      │ (London)│      │ (Tokyo) │
    └─────────┘      └─────────┘      └─────────┘
         │                │                │
         └────────────────┼────────────────┘
                          │
                    Anti-entropy
                      (gossip)
```

### 4.2 Transaction Flow

**Algebraic Transaction (fast path):**
1. Client sends transaction to nearest node
2. Node classifies operations
3. If all algebraic: commit locally, return success
4. Propagate to other nodes via gossip
5. Other nodes merge using algebraic properties

**Non-Algebraic Transaction (coordination path):**
1. Client sends transaction to nearest node
2. Node detects non-algebraic operation
3. Fall back to coordination protocol (Raft/2PC)
4. Return after quorum acknowledgment

### 4.3 Causality Tracking

We use **vector clocks** to track happened-before relationships:

```rust
struct VectorClock {
    clocks: HashMap<NodeId, u64>,
}
```

On merge:
- If $V_A < V_B$: A happened-before B, apply B
- If $V_B < V_A$: B happened-before A, already have it
- If $V_A \| V_B$: Concurrent, use algebraic merge

### 4.4 Algebraic Merge

```rust
fn merge(op_type: OpType, v1: Value, v2: Value) -> Value {
    match op_type {
        AbelianAdd => v1 + v2,
        SemilatticeMax => max(v1, v2),
        SemilatticeUnion => v1.union(v2),
        // ...
    }
}
```

---

## 5. Implementation

Rhizo is implemented in Rust with Python bindings.

### 5.1 Core Components

| Component | Lines of Code | Description |
|-----------|---------------|-------------|
| ChunkStore | ~1,500 | Content-addressed storage |
| Catalog | ~2,000 | Versioned metadata |
| AlgebraicMerger | ~500 | Operation merge logic |
| VectorClock | ~200 | Causality tracking |
| Gossip | ~300 | Anti-entropy protocol |

### 5.2 Operation Classification

Operations are classified at transaction creation:

```python
with engine.transaction(mode="coordination_free") as tx:
    tx.increment("counters", "views", 1)  # → AbelianAdd
    tx.add_to_set("users", "tags", "premium")  # → SemilatticeUnion
```

Classification is automatic based on operation type.

---

## 6. Evaluation

### 6.1 Experimental Setup

- **Nodes:** 5 AWS instances (us-west, us-east, eu-west, ap-south, ap-northeast)
- **Workload:** [TBD - counter increments, set operations]
- **Comparison:** Rhizo vs CockroachDB vs Cassandra

### 6.2 Latency

[TBD - Graphs showing latency comparison]

Expected result: Rhizo achieves ~5ms local latency vs ~150ms for coordination-based systems.

### 6.3 Throughput

[TBD - Graphs showing throughput scaling]

Expected result: Linear scaling with nodes for algebraic workloads.

### 6.4 Convergence Time

[TBD - Time for all nodes to converge after partition]

### 6.5 Mixed Workloads

[TBD - Performance with mix of algebraic and non-algebraic operations]

---

## 7. Discussion

### 7.1 When to Use Coordination-Free Transactions

**Good fit:**
- Counters, analytics aggregates
- Tag/label management
- High-water marks, timestamps
- Add-only collections

**Poor fit:**
- Balance transfers (need atomicity across accounts)
- Inventory with hard limits
- Sequential ID generation

### 7.2 Limitations

1. **Not all operations are algebraic:** OVERWRITE, CAS require coordination.
2. **Bounded counters:** Overflow requires special handling.
3. **Set deletion:** Requires tombstones (OR-Set).

### 7.3 Future Work

- Automatic algebraic inference from query logs
- Hybrid transactions mixing algebraic and coordinated operations
- Formal verification of implementation

---

## 8. Conclusion

We presented Rhizo, a distributed data system that achieves strong consistency without consensus for algebraic operations. By classifying operations by their mathematical properties, we enable O(1) local commit latency while maintaining provable convergence. For common workloads like counters and set operations, this represents a fundamental improvement over coordination-based approaches.

---

## References

[Corbett et al., 2012] Spanner: Google's Globally-Distributed Database. OSDI.

[Taft et al., 2020] CockroachDB: The Resilient Geo-Distributed SQL Database. SIGMOD.

[Thomson et al., 2012] Calvin: Fast Distributed Transactions for Partitioned Database Systems. SIGMOD.

[DeCandia et al., 2007] Dynamo: Amazon's Highly Available Key-value Store. SOSP.

[Shapiro et al., 2011] Conflict-free Replicated Data Types. SSS.

[Li et al., 2012] Making Geo-Replicated Systems Fast as Possible, Consistent when Necessary. OSDI.

---

## Appendix A: Proofs

See `proofs/convergence_proof.md` and `proofs/causality_proof.md` for full proofs.

---

## Appendix B: API Reference

```python
# Create coordination-free transaction
with engine.transaction(mode="coordination_free") as tx:
    # Algebraic operations
    tx.increment(table, key, column, delta)  # AbelianAdd
    tx.max(table, key, column, value)        # SemilatticeMax
    tx.add_to_set(table, key, column, item)  # SemilatticeUnion

    # Commits locally, propagates via gossip
```

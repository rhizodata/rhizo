# Edge Cases and Limitations

> **Purpose:** Document cases where algebraic properties break down or require special handling.

---

## 1. Integer Overflow

### Problem
`AbelianAdd` assumes infinite integers, but computers use fixed-width:
```rust
let a: i64 = i64::MAX;
let b: i64 = 1;
let c = a + b;  // OVERFLOW!
```

### Impact on Commutativity
With overflow, addition is still commutative:
$$a + b = b + a \text{ (mod } 2^{64})$$

But the result may be unexpected (wrap-around or panic).

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Saturating arithmetic** | `i64::MAX + 1 = i64::MAX`. Loses information but safe. |
| **Checked arithmetic** | Return error on overflow. Requires error handling. |
| **Big integers** | Arbitrary precision. Slower, more storage. |
| **Detect and coordinate** | If overflow possible, fall back to coordination. |

### Recommendation
Use **checked arithmetic** with fallback:
```rust
fn add_safe(a: i64, b: i64) -> Result<i64, Overflow> {
    a.checked_add(b).ok_or(Overflow)
}

// On overflow: transaction must coordinate
```

---

## 2. Floating Point Precision

### Problem
Floating point is NOT associative:
```rust
let a = 1e20_f64;
let b = -1e20_f64;
let c = 1.0_f64;

// (a + b) + c = 0 + 1 = 1
// a + (b + c) = 1e20 + (-1e20 + 1) = 1e20 + (-1e20) = 0
```

### Impact
Different merge orders could yield different results!

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Use integers only** | Multiply by 100 for currency. Safe but limited. |
| **Fixed-point decimal** | e.g., `rust_decimal`. Exact but slower. |
| **Accept imprecision** | For analytics, ±0.0001% may be acceptable. |
| **Kahan summation** | Reduces error accumulation. More complex. |

### Recommendation
For financial data: Use `Decimal` type (exact arithmetic).
For analytics: Accept floating point with documented precision bounds.

```rust
pub enum AlgebraicValue {
    Integer(i64),      // Exact
    Decimal(Decimal),  // Exact, for money
    Float(f64),        // Approximate, for analytics
}
```

---

## 3. Set Size Limits

### Problem
`SemilatticeUnion` grows unboundedly:
```rust
// Branch A: tags = {"a", "b", "c", ...}  (1000 items)
// Branch B: tags = {"x", "y", "z", ...}  (1000 items)
// Merge: tags = {"a", "b", ..., "x", "y", ...}  (2000 items!)
```

### Impact
Memory and storage grow without bound.

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Bounded sets** | Reject if size > limit. May lose data. |
| **Probabilistic sets** | Bloom filter / HyperLogLog. Approximate. |
| **Garbage collection** | Periodic cleanup of old entries. Needs policy. |
| **Tombstones** | Track deletions. More complex, but accurate. |

### Recommendation
Implement optional size bounds with clear semantics:
```rust
pub struct BoundedSet<T> {
    items: HashSet<T>,
    max_size: usize,
    overflow_policy: OverflowPolicy,  // Reject, LRU, Error
}
```

---

## 4. Tombstones and Deletion

### Problem
`SemilatticeUnion` only adds, never removes:
```rust
// Add "premium" tag
// Later: remove "premium" tag
// Union always keeps it!
```

### Impact
Deleted items reappear on merge.

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **No deletion** | Accept that union is add-only. Simple but limiting. |
| **Tombstones** | Mark deleted items. Need garbage collection. |
| **Observed-Remove Set** | CRDT that tracks add/remove. More complex. |
| **Epoch-based** | Deletion after certain epoch. Time-based cleanup. |

### Recommendation
Implement OR-Set (Observed-Remove Set) for sets that need deletion:
```rust
pub struct ORSet<T> {
    // Each add gets a unique tag
    elements: HashMap<T, HashSet<UniqueTag>>,
    tombstones: HashMap<T, HashSet<UniqueTag>>,
}

// Add: elements[x].insert(new_tag())
// Remove: tombstones[x] = elements[x].clone()
// Lookup: elements[x] - tombstones[x] is non-empty
```

---

## 5. Clock Drift

### Problem
Vector clocks are logical, not physical. But what if we need real-time ordering?

### Impact
"Latest" may not mean "most recent wall-clock time".

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Hybrid logical clocks** | Combine physical + logical. Better ordering. |
| **NTP synchronization** | Keep physical clocks close. Needs infrastructure. |
| **Accept logical ordering** | Sufficient for most use cases. |

### Recommendation
For Rhizo: Logical ordering is sufficient. Add HLC later if needed.

---

## 6. Network Partition Divergence

### Problem
Long partition → lots of concurrent updates → big merge.

### Example
```
Node A: 1000 counter increments over 1 hour
Node B: 1000 counter increments over 1 hour
Partition heals: Need to merge 2000 updates
```

### Impact
- Merge may be expensive
- State may be surprising to users

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Accept it** | Algebraically correct, just big. |
| **Batch updates** | Coalesce: "counter += 1000" instead of 1000× "counter += 1". |
| **Conflict notification** | Alert users when merge is large. |

### Recommendation
Batch updates locally before sending:
```rust
// Instead of sending 1000 messages:
// counter += 1, counter += 1, ...

// Send one message:
// counter += 1000
```

---

## 7. Schema Evolution

### Problem
What if algebraic classification changes?
```rust
// v1: "balance" is AbelianAdd (increment only)
// v2: "balance" is GenericOverwrite (can set directly)
```

### Impact
Old nodes expect algebraic, new nodes expect overwrite.

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Immutable classification** | Once set, never change. Limiting. |
| **Versioned schemas** | Track schema version with data. Complex. |
| **Migration period** | Coordinate upgrade. Requires downtime. |

### Recommendation
Include schema version in transaction:
```rust
pub struct DistributedTransaction {
    operations: Vec<Operation>,
    vector_clock: VectorClock,
    schema_version: u64,  // NEW
}

// Reject transactions with mismatched schema versions
```

---

## 8. Node Identity

### Problem
Vector clocks require stable node IDs. What if nodes join/leave dynamically?

### Impact
- New node: Need to allocate clock entry
- Leaving node: Clock entries never reclaimed

### Solutions

| Approach | Trade-off |
|----------|-----------|
| **Fixed membership** | Static set of nodes. Simple but inflexible. |
| **Dotted version vectors** | Handle dynamic membership. More complex. |
| **Garbage collection** | Remove entries for long-dead nodes. Needs protocol. |

### Recommendation
Start with fixed membership. Add dynamic membership later if needed.

---

## Summary: What Rhizo Should Support Initially

| Feature | Support Level | Notes |
|---------|---------------|-------|
| Integer ADD | Full | With checked overflow |
| Float ADD | Partial | Document precision limits |
| MAX/MIN | Full | No edge cases |
| Set UNION | Full | With optional size bound |
| Set with deletion | Future | OR-Set implementation |
| Dynamic membership | Future | Start with fixed nodes |

---

## Test Cases for Edge Cases

```rust
#[test] fn test_integer_overflow_checked();
#[test] fn test_float_precision_bounds();
#[test] fn test_set_size_limit();
#[test] fn test_large_partition_merge();
#[test] fn test_concurrent_same_key();
```

These edge cases don't break the theory—they require careful implementation.

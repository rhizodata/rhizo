# Proof: Vector Clocks Capture Causality

> **Theorem:** Vector clocks accurately track the "happened-before" relation in distributed systems, enabling correct merge decisions.

---

## Definitions

### Definition 1: Events
Let $E$ be the set of all events in the system. Events include:
- Local operations (writes, commits)
- Message sends
- Message receives

### Definition 2: Happened-Before ($\to$)
The happened-before relation is the smallest relation satisfying:
1. **Local order:** If $a$ and $b$ are events at the same node and $a$ occurs before $b$, then $a \to b$
2. **Message order:** If $a$ is the send of a message and $b$ is the receive, then $a \to b$
3. **Transitivity:** If $a \to b$ and $b \to c$, then $a \to c$

### Definition 3: Concurrent ($\|$)
Events $a$ and $b$ are **concurrent** if neither happened before the other:
$$a \| b \iff \neg(a \to b) \land \neg(b \to a)$$

### Definition 4: Vector Clock
A vector clock $V$ for $N$ nodes is:
$$V = [v_1, v_2, \ldots, v_N]$$
where $v_i$ is the logical time at node $i$.

---

## Vector Clock Rules

### Rule 1: Increment on Local Event
When node $i$ performs a local event:
$$V_i[i] := V_i[i] + 1$$

### Rule 2: Merge on Message Receive
When node $i$ receives a message with clock $V_m$:
$$V_i[j] := \max(V_i[j], V_m[j]) \quad \forall j$$
$$V_i[i] := V_i[i] + 1$$

---

## Theorem 1: Vector Clocks Characterize Happened-Before

**Statement:** For events $a$ at node $i$ and $b$ at node $j$:
$$a \to b \iff V(a) < V(b)$$

Where $V(a) < V(b)$ means:
$$\forall k: V(a)[k] \leq V(b)[k] \land \exists k: V(a)[k] < V(b)[k]$$

**Proof:**

**($\Rightarrow$) If $a \to b$, then $V(a) < V(b)$:**

By induction on the derivation of $a \to b$:

*Base case 1 (local order):*
$a$ and $b$ at same node $i$, $a$ before $b$.
- By Rule 1, each event increments $V[i]$
- So $V(a)[i] < V(b)[i]$
- Other entries unchanged: $V(a)[j] = V(b)[j]$ for $j \neq i$
- Therefore $V(a) < V(b)$ ✓

*Base case 2 (message):*
$a$ is send at node $i$, $b$ is receive at node $j$.
- By Rule 2, $V(b)[k] \geq V(a)[k]$ for all $k$
- And $V(b)[j] > V(a)[j]$ (incremented on receive)
- Therefore $V(a) < V(b)$ ✓

*Inductive case (transitivity):*
$a \to c$ via $a \to b$ and $b \to c$.
- By induction: $V(a) < V(b)$ and $V(b) < V(c)$
- $<$ is transitive on vectors
- Therefore $V(a) < V(c)$ ✓

**($\Leftarrow$) If $V(a) < V(b)$, then $a \to b$:**

Proof by contrapositive. Assume $\neg(a \to b)$.

Case 1: $a = b$
Then $V(a) = V(b)$, so $\neg(V(a) < V(b))$ ✓

Case 2: $a$ and $b$ at same node, $b$ before $a$
Then $b \to a$, so $V(b) < V(a)$, so $\neg(V(a) < V(b))$ ✓

Case 3: $a$ and $b$ at different nodes, concurrent
- No causal path from $a$ to $b$
- $V(a)[i] \geq $ timestamp when $a$ occurred at node $i$
- No message from $i$ reached $j$ before $b$
- So $V(b)[i] < V(a)[i]$ (or equal)
- Therefore $\neg(V(a) < V(b))$ ✓

∎

---

## Theorem 2: Concurrent Detection

**Statement:** Events are concurrent iff their vector clocks are incomparable:
$$a \| b \iff \neg(V(a) < V(b)) \land \neg(V(b) < V(a))$$

**Proof:**
Direct consequence of Theorem 1:
- $a \| b$ means $\neg(a \to b) \land \neg(b \to a)$
- By Theorem 1: $\neg(V(a) < V(b)) \land \neg(V(b) < V(a))$ ∎

---

## Theorem 3: Merge is Safe When Concurrent

**Statement:** If events $a$ and $b$ are concurrent and their operations are algebraic, merging them produces a consistent result.

**Proof:**

1. By Theorem 2, we can detect concurrency: $V(a) \| V(b)$

2. Concurrent means neither causally depends on the other.

3. By convergence proof (Theorem 1 in convergence_proof.md), algebraic operations can be applied in any order.

4. Merge: Apply both operations, order doesn't matter.

5. Result is consistent and deterministic. ∎

---

## Theorem 4: Causal Delivery Preserves Order

**Statement:** If operations are delivered in causal order and messages are reliable, then:
- Operations that happened-before are seen in that order
- Concurrent operations can be merged algebraically

**Proof:**

1. **Causal delivery:** Node $j$ only processes message $m$ from node $i$ if it has processed all messages that $i$ had processed before sending $m$.

2. Implementation: Node $j$ delays message $m$ with clock $V_m$ until:
   $$\forall k \neq i: V_j[k] \geq V_m[k]$$

3. This ensures: If $a \to b$, then $a$ is processed before $b$ at all nodes.

4. Concurrent operations ($a \| b$) may be processed in different orders at different nodes.

5. By algebraic convergence, different orders yield same result. ∎

---

## Application to Rhizo

### When to Use Vector Clocks

| Situation | Vector Clock Check | Action |
|-----------|-------------------|--------|
| Local write | Increment | Apply locally |
| Receive update | Merge clocks | Check relation |
| $V_{local} < V_{remote}$ | Remote is newer | Apply remote |
| $V_{remote} < V_{local}$ | Local is newer | Ignore remote |
| $V_{local} \| V_{remote}$ | Concurrent! | Algebraic merge |

### Merge Decision Tree

```
On receiving update U with clock V_u:

    if V_u < V_local:
        # We already have this or newer
        discard U

    elif V_local < V_u:
        # U is strictly newer
        apply U
        V_local := merge(V_local, V_u)

    else:  # Concurrent
        if U.operation.is_algebraic():
            # Safe to merge automatically
            local_state := algebraic_merge(local_state, U)
            V_local := merge(V_local, V_u)
        else:
            # Conflict! Need coordination
            report_conflict(U)
```

---

## Complexity Analysis

| Operation | Time | Space |
|-----------|------|-------|
| Clock increment | O(1) | - |
| Clock merge | O(N) | - |
| Comparison | O(N) | - |
| Storage per event | O(N) | N integers |

Where N = number of nodes.

For large N, can use:
- **Compressed clocks:** Only store non-zero entries
- **Interval tree clocks:** More space-efficient variant
- **Dotted version vectors:** Handles dynamic membership

---

## Conclusion

Vector clocks provide:
1. **Accurate causality tracking** (Theorem 1)
2. **Concurrent detection** (Theorem 2)
3. **Safe merge decisions** (Theorem 3)
4. **Causal ordering** (Theorem 4)

Combined with algebraic operations, this enables coordination-free distributed transactions with strong consistency guarantees.

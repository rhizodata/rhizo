# Sandbox

Experimental code, mathematical proofs, and research prototypes.

## Contents

### coordination_free/

Mathematical foundations for coordination-free distributed transactions.

```
coordination_free/
├── DESIGN.md          # Architecture and design decisions
├── ROADMAP.md         # Implementation roadmap
└── proofs/
    ├── causality_proof.md      # Vector clock causality guarantees
    ├── convergence_proof.md    # Algebraic convergence proof
    ├── edge_cases.md           # Edge case analysis
    └── energy_efficiency_proof.md  # Energy model derivation
```

**Status**: Core concepts implemented in `rhizo_core::distributed`. Proofs validate claims in [TECHNICAL_FOUNDATIONS.md](../docs/TECHNICAL_FOUNDATIONS.md).

### poac/

Prototype implementation of POAC (Probabilistic Optimistic Algebraic Consistency).

```
poac/
├── algebraic_classifier.py   # Operation classification by algebraic properties
├── bloom_write_set.py        # Bloom filter for O(1) conflict detection
├── escrow_manager.py         # Hot spot scalability via escrow
├── speculative_executor.py   # Speculative execution engine
├── experiment_harness.py     # Benchmarking framework
└── metrics.py                # Performance measurement
```

**Status**: Research prototype. See [poac_paper.md](../papers/poac_paper.md) for theory.

## Purpose

This directory contains:
1. **Mathematical proofs** backing performance claims
2. **Research prototypes** exploring future optimizations
3. **Design documents** capturing architectural decisions

Code here is experimental and not part of the stable API.

## Running Experiments

```bash
# POAC experiments (requires sandbox/poac/requirements.txt)
cd sandbox/poac
pip install -r requirements.txt
python experiment_harness.py
```

## Related

- [papers/](../papers/) - Formal write-ups of these ideas
- [benchmarks/](../benchmarks/) - Production benchmarks
- [docs/TECHNICAL_FOUNDATIONS.md](../docs/TECHNICAL_FOUNDATIONS.md) - Verified claims

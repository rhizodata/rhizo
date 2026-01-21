"""
Experiment 01: Baseline Convergence Measurements

Goal: Measure actual convergence rounds in Rhizo's coordination-free system
      across different node counts to establish baseline data.

Hypothesis: Convergence rounds = ceil(log2(N)) + 1 for N nodes.

This experiment:
1. Uses existing Rhizo simulation infrastructure
2. Measures rounds to convergence for N = 2, 4, 8, 16, 32
3. Compares to theoretical lower bound
4. Identifies if we're achieving optimal or if there's room to improve
"""

import sys
import math
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional

# Add rhizo to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import Rhizo's distributed simulation infrastructure
from _rhizo import (
    PySimulatedCluster,
    PySimulationBuilder,
    PyAlgebraicTransaction,
    PyAlgebraicOperation,
    PyAlgebraicValue,
    PyOpType,
)


@dataclass
class ConvergenceResult:
    """Result of a convergence experiment."""
    num_nodes: int
    num_operations: int
    measured_rounds: Optional[int]
    theoretical_bound: int
    convergence_time_ms: float
    messages_sent: int
    converged: bool

    @property
    def is_optimal(self) -> bool:
        return self.converged and self.measured_rounds == self.theoretical_bound

    @property
    def gap(self) -> Optional[int]:
        if self.measured_rounds is None:
            return None
        return self.measured_rounds - self.theoretical_bound

    def __str__(self):
        if not self.converged:
            status = "FAILED TO CONVERGE"
        elif self.is_optimal:
            status = "OPTIMAL"
        else:
            status = f"GAP={self.gap:+d}"

        rounds_str = str(self.measured_rounds) if self.measured_rounds else "N/A"
        return (
            f"N={self.num_nodes:3d} | ops={self.num_operations:4d} | "
            f"rounds={rounds_str:>3} | bound={self.theoretical_bound} | "
            f"msgs={self.messages_sent:5d} | {self.convergence_time_ms:6.2f}ms | {status}"
        )


def theoretical_lower_bound(num_nodes: int) -> int:
    """
    Calculate the theoretical lower bound on convergence rounds.

    Derivation:
    - In a gossip protocol, information spreads exponentially
    - After k rounds, at most 2^k nodes can have received the information
    - For all N nodes to have all information: 2^k >= N
    - Therefore: k >= log2(N)
    - Plus 1 round for quiescence detection (confirming everyone has converged)

    Lower bound: ceil(log2(N)) + 1
    """
    if num_nodes <= 1:
        return 1
    return math.ceil(math.log2(num_nodes)) + 1


def run_convergence_experiment(
    num_nodes: int,
    num_operations_per_node: int = 10,
    max_rounds: int = 100,
    use_reordering: bool = False
) -> ConvergenceResult:
    """
    Run a single convergence experiment.

    Args:
        num_nodes: Number of simulated nodes
        num_operations_per_node: Operations each node performs
        max_rounds: Maximum rounds before giving up
        use_reordering: Whether to enable message reordering

    Returns:
        ConvergenceResult with measured vs theoretical rounds
    """
    # Create cluster
    cluster = PySimulatedCluster(num_nodes)

    start_time = time.perf_counter()

    # Each node commits operations locally
    for node_id in range(num_nodes):
        for op_idx in range(num_operations_per_node):
            tx = PyAlgebraicTransaction()
            # Use ADD operation - algebraic, guaranteed to converge
            tx.add_operation(PyAlgebraicOperation(
                f"counter_{node_id}",  # Unique key per node initially
                PyOpType("add"),
                PyAlgebraicValue.integer(1)
            ))
            cluster.commit_on_node(node_id, tx)

    # Also add a shared counter that all nodes increment
    # This tests convergence of concurrent operations on same key
    for node_id in range(num_nodes):
        tx = PyAlgebraicTransaction()
        tx.add_operation(PyAlgebraicOperation(
            "shared_counter",
            PyOpType("add"),
            PyAlgebraicValue.integer(node_id + 1)  # Each node adds its ID + 1
        ))
        cluster.commit_on_node(node_id, tx)

    # Propagate until convergence
    cluster.propagate_all()

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    # Get stats
    stats = cluster.get_stats()
    converged = cluster.verify_convergence()

    bound = theoretical_lower_bound(num_nodes)

    return ConvergenceResult(
        num_nodes=num_nodes,
        num_operations=num_operations_per_node,
        measured_rounds=stats.rounds_to_converge,
        theoretical_bound=bound,
        convergence_time_ms=elapsed_ms,
        messages_sent=stats.messages_sent,
        converged=converged
    )


def run_experiment_suite() -> List[ConvergenceResult]:
    """Run convergence experiments across different configurations."""

    results = []

    # Test different node counts
    node_counts = [2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32]

    # Test different operation counts
    operation_counts = [1, 5, 10, 50]

    print("=" * 90)
    print("CONVERGENCE BASELINE EXPERIMENT")
    print("=" * 90)
    print()
    print("Theoretical bound: ceil(log2(N)) + 1 rounds")
    print("Testing if Rhizo achieves this bound...")
    print()

    for num_ops in operation_counts:
        print(f"\n{'='*90}")
        print(f"Operations per node: {num_ops}")
        print(f"{'='*90}")
        print()
        print(f"{'N':>5} | {'ops':>4} | {'rounds':>6} | {'bound':>5} | {'msgs':>7} | {'time':>8} | status")
        print("-" * 90)

        for n in node_counts:
            result = run_convergence_experiment(n, num_ops)
            results.append(result)
            print(result)

    return results


def analyze_results(results: List[ConvergenceResult]) -> Dict:
    """Analyze experiment results."""

    analysis = {
        "total_experiments": len(results),
        "converged_count": sum(1 for r in results if r.converged),
        "optimal_count": sum(1 for r in results if r.is_optimal),
        "by_node_count": {},
        "by_operation_count": {},
        "max_gap": 0,
        "avg_gap": 0,
    }

    # Calculate gaps
    gaps = [r.gap for r in results if r.gap is not None]
    if gaps:
        analysis["max_gap"] = max(gaps)
        analysis["avg_gap"] = sum(gaps) / len(gaps)

    # Group by node count
    for r in results:
        if r.num_nodes not in analysis["by_node_count"]:
            analysis["by_node_count"][r.num_nodes] = []
        analysis["by_node_count"][r.num_nodes].append(r)

    # Group by operation count
    for r in results:
        if r.num_operations not in analysis["by_operation_count"]:
            analysis["by_operation_count"][r.num_operations] = []
        analysis["by_operation_count"][r.num_operations].append(r)

    return analysis


def print_analysis(analysis: Dict):
    """Print analysis summary."""

    print("\n" + "=" * 90)
    print("ANALYSIS SUMMARY")
    print("=" * 90)

    total = analysis["total_experiments"]
    converged = analysis["converged_count"]
    optimal = analysis["optimal_count"]

    print(f"\nTotal experiments: {total}")
    print(f"Converged: {converged} ({100*converged/total:.1f}%)")
    print(f"Achieved optimal bound: {optimal} ({100*optimal/total:.1f}%)")
    print(f"Max gap from bound: {analysis['max_gap']}")
    print(f"Avg gap from bound: {analysis['avg_gap']:.2f}")

    print("\n--- Rounds by Node Count (averaged across operation counts) ---")
    print(f"{'N':>5} | {'bound':>5} | {'avg_rounds':>10} | {'gap':>6} | status")
    print("-" * 50)

    for n in sorted(analysis["by_node_count"].keys()):
        results = analysis["by_node_count"][n]
        rounds = [r.measured_rounds for r in results if r.measured_rounds is not None]
        if rounds:
            avg_rounds = sum(rounds) / len(rounds)
            bound = theoretical_lower_bound(n)
            gap = avg_rounds - bound
            status = "OPTIMAL" if gap == 0 else f"+{gap:.1f}"
            print(f"{n:5d} | {bound:5d} | {avg_rounds:10.1f} | {gap:+6.1f} | {status}")

    # Check if rounds depend on operation count
    print("\n--- Does Operation Count Affect Rounds? ---")
    for ops in sorted(analysis["by_operation_count"].keys()):
        results = analysis["by_operation_count"][ops]
        rounds = [r.measured_rounds for r in results if r.measured_rounds is not None]
        if rounds:
            avg = sum(rounds) / len(rounds)
            print(f"  {ops:3d} ops/node -> avg rounds = {avg:.1f}")


def verify_shared_counter(num_nodes: int) -> bool:
    """
    Verify that shared counter converges correctly.

    Expected: shared_counter = sum(1..N) = N*(N+1)/2
    """
    cluster = PySimulatedCluster(num_nodes)

    # Each node adds its (node_id + 1)
    for node_id in range(num_nodes):
        tx = PyAlgebraicTransaction()
        tx.add_operation(PyAlgebraicOperation(
            "shared",
            PyOpType("add"),
            PyAlgebraicValue.integer(node_id + 1)
        ))
        cluster.commit_on_node(node_id, tx)

    cluster.propagate_all()

    expected = num_nodes * (num_nodes + 1) // 2

    # Check all nodes have the expected value
    for node_id in range(num_nodes):
        actual = cluster.get_node_state(node_id, "shared")
        if actual is None:
            print(f"  Node {node_id}: None (expected {expected})")
            return False
        # Extract integer value
        actual_val = actual.value if hasattr(actual, 'value') else actual
        if actual_val != expected:
            print(f"  Node {node_id}: {actual_val} (expected {expected})")
            return False

    return True


def main():
    """Main entry point."""

    print("\n" + "=" * 90)
    print("RHIZO CONVERGENCE THEORY - EXPERIMENT 01: BASELINE MEASUREMENTS")
    print("=" * 90)
    print()
    print("Goal: Measure actual convergence rounds vs theoretical bound")
    print("Bound formula: ceil(log2(N)) + 1")
    print()

    # Print theoretical bounds for reference
    print("Theoretical bounds by node count:")
    for n in [2, 4, 8, 16, 32, 64]:
        print(f"  N={n:3d} -> bound = ceil(log2({n})) + 1 = {theoretical_lower_bound(n)}")
    print()

    # Quick sanity check: verify convergence is correct
    print("Sanity check: verifying algebraic convergence correctness...")
    for n in [2, 5, 10]:
        correct = verify_shared_counter(n)
        status = "PASS" if correct else "FAIL"
        expected = n * (n + 1) // 2
        print(f"  N={n}: shared_counter should be {expected}... {status}")
    print()

    # Run experiments
    results = run_experiment_suite()

    # Analyze
    analysis = analyze_results(results)
    print_analysis(analysis)

    # Conclusion
    print("\n" + "=" * 90)
    print("FINDINGS")
    print("=" * 90)

    if analysis["optimal_count"] == analysis["total_experiments"]:
        print("""
RESULT: Rhizo achieves OPTIMAL convergence!

All experiments achieved the theoretical lower bound of ceil(log2(N)) + 1 rounds.

This is the best possible - no gossip protocol can converge faster because:
1. Information spreads at most exponentially (2^k nodes after k rounds)
2. All N nodes must receive all operations
3. One additional round needed for quiescence detection

NEXT STEP: Formalize this as a theorem with proof.
        """)
    else:
        suboptimal = analysis["total_experiments"] - analysis["optimal_count"]
        print(f"""
RESULT: {suboptimal} experiments did not achieve optimal bound.

Max gap from bound: {analysis["max_gap"]} rounds
Avg gap from bound: {analysis["avg_gap"]:.2f} rounds

INVESTIGATION NEEDED:
- Why are some configurations suboptimal?
- Is this due to implementation or fundamental limit?
- Can the gap be closed?
        """)

    return results


if __name__ == "__main__":
    main()

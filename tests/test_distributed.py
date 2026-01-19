"""Tests for distributed systems primitives (coordination-free transactions)."""

import pytest
from _rhizo import PyNodeId, PyVectorClock, PyCausalOrder


class TestNodeId:
    """Tests for PyNodeId."""

    def test_create_node_id(self):
        """Test creating a node ID."""
        node = PyNodeId("test-node")
        assert str(node) == "test-node"

    def test_node_id_repr(self):
        """Test node ID representation."""
        node = PyNodeId("my-node")
        assert "my-node" in repr(node)

    def test_node_id_equality(self):
        """Test node ID equality."""
        node1 = PyNodeId("node-a")
        node2 = PyNodeId("node-a")
        node3 = PyNodeId("node-b")

        assert node1 == node2
        assert node1 != node3

    def test_node_id_hashable(self):
        """Test that node IDs can be used in sets/dicts."""
        node1 = PyNodeId("node-a")
        node2 = PyNodeId("node-a")
        node3 = PyNodeId("node-b")

        # Same ID should have same hash
        assert hash(node1) == hash(node2)

        # Can be used in set
        node_set = {node1, node2, node3}
        assert len(node_set) == 2  # node1 and node2 are duplicates


class TestVectorClock:
    """Tests for PyVectorClock."""

    def test_create_empty_clock(self):
        """Test creating an empty vector clock."""
        clock = PyVectorClock()
        assert clock.is_empty()
        assert clock.node_count() == 0

    def test_tick_increments(self):
        """Test that tick increments the clock."""
        node = PyNodeId("node-1")
        clock = PyVectorClock()

        assert clock.get(node) == 0

        clock.tick(node)
        assert clock.get(node) == 1

        clock.tick(node)
        assert clock.get(node) == 2

    def test_tick_only_affects_one_node(self):
        """Test that tick only affects the specified node."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")
        clock = PyVectorClock()

        clock.tick(node_a)
        clock.tick(node_a)
        clock.tick(node_b)

        assert clock.get(node_a) == 2
        assert clock.get(node_b) == 1

    def test_with_node_constructor(self):
        """Test with_node static constructor."""
        node = PyNodeId("node-1")
        clock = PyVectorClock.with_node(node, 5)

        assert clock.get(node) == 5
        assert not clock.is_empty()

    def test_set_time(self):
        """Test setting time directly."""
        node = PyNodeId("node-1")
        clock = PyVectorClock()

        clock.set(node, 10)
        assert clock.get(node) == 10

    def test_merge_takes_max(self):
        """Test that merge takes component-wise maximum."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")
        node_c = PyNodeId("c")

        clock1 = PyVectorClock()
        clock1.set(node_a, 3)
        clock1.set(node_b, 1)

        clock2 = PyVectorClock()
        clock2.set(node_a, 2)
        clock2.set(node_b, 4)
        clock2.set(node_c, 5)

        clock1.merge(clock2)

        assert clock1.get(node_a) == 3  # max(3, 2)
        assert clock1.get(node_b) == 4  # max(1, 4)
        assert clock1.get(node_c) == 5  # max(0, 5)

    def test_happened_before_simple(self):
        """Test happened_before for simple case."""
        node = PyNodeId("node-1")

        clock1 = PyVectorClock.with_node(node, 1)
        clock2 = PyVectorClock.with_node(node, 2)

        assert clock1.happened_before(clock2)
        assert not clock2.happened_before(clock1)
        assert not clock1.happened_before(clock1)

    def test_happened_after(self):
        """Test happened_after."""
        node = PyNodeId("node-1")

        clock1 = PyVectorClock.with_node(node, 1)
        clock2 = PyVectorClock.with_node(node, 2)

        assert clock2.happened_after(clock1)
        assert not clock1.happened_after(clock2)

    def test_concurrent_different_nodes(self):
        """Test concurrent detection for different nodes."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        clock_a = PyVectorClock()
        clock_a.tick(node_a)

        clock_b = PyVectorClock()
        clock_b.tick(node_b)

        # These are concurrent
        assert clock_a.concurrent_with(clock_b)
        assert clock_b.concurrent_with(clock_a)
        assert not clock_a.happened_before(clock_b)
        assert not clock_b.happened_before(clock_a)

    def test_concurrent_crossed_updates(self):
        """Test concurrent detection for crossed updates."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        clock1 = PyVectorClock()
        clock1.set(node_a, 2)
        clock1.set(node_b, 1)

        clock2 = PyVectorClock()
        clock2.set(node_a, 1)
        clock2.set(node_b, 2)

        # Neither dominates
        assert clock1.concurrent_with(clock2)
        assert clock2.concurrent_with(clock1)

    def test_compare_all_cases(self):
        """Test compare returns correct CausalOrder."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        # Equal
        clock1 = PyVectorClock.with_node(node_a, 1)
        clock2 = PyVectorClock.with_node(node_a, 1)
        assert clock1.compare(clock2).order == "equal"

        # Before
        clock3 = PyVectorClock.with_node(node_a, 2)
        assert clock1.compare(clock3).order == "before"

        # After
        assert clock3.compare(clock1).order == "after"

        # Concurrent
        clock4 = PyVectorClock.with_node(node_b, 1)
        assert clock1.compare(clock4).order == "concurrent"

    def test_message_passing_scenario(self):
        """Test realistic message passing scenario."""
        node_a = PyNodeId("sf")
        node_b = PyNodeId("tokyo")

        # Node A does local work
        clock_a = PyVectorClock()
        clock_a.tick(node_a)
        clock_a.tick(node_a)

        # Node B does local work
        clock_b = PyVectorClock()
        clock_b.tick(node_b)

        # At this point, concurrent
        assert clock_a.concurrent_with(clock_b)

        # A sends message to B (B receives and merges)
        clock_b.merge(clock_a)
        clock_b.tick(node_b)

        # Now B is after A
        assert clock_a.happened_before(clock_b)
        assert clock_b.get(node_a) == 2
        assert clock_b.get(node_b) == 2

    def test_sum(self):
        """Test sum of all clock components."""
        clock = PyVectorClock()
        clock.set(PyNodeId("a"), 10)
        clock.set(PyNodeId("b"), 20)
        clock.set(PyNodeId("c"), 30)

        assert clock.sum() == 60

    def test_max_static(self):
        """Test static max method."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        clock1 = PyVectorClock()
        clock1.set(node_a, 3)
        clock1.set(node_b, 1)

        clock2 = PyVectorClock()
        clock2.set(node_a, 2)
        clock2.set(node_b, 4)

        merged = PyVectorClock.max(clock1, clock2)

        # Original unchanged
        assert clock1.get(node_a) == 3
        assert clock1.get(node_b) == 1

        # Merged has max
        assert merged.get(node_a) == 3
        assert merged.get(node_b) == 4

    def test_ticked_returns_copy(self):
        """Test ticked returns a new clock."""
        node = PyNodeId("node-1")
        clock1 = PyVectorClock.with_node(node, 1)
        clock2 = clock1.ticked(node)

        # Original unchanged
        assert clock1.get(node) == 1
        # Copy incremented
        assert clock2.get(node) == 2

    def test_json_serialization(self):
        """Test JSON serialization roundtrip."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        clock = PyVectorClock()
        clock.set(node_a, 42)
        clock.set(node_b, 17)

        json_str = clock.to_json()
        restored = PyVectorClock.from_json(json_str)

        assert clock == restored
        assert restored.get(node_a) == 42
        assert restored.get(node_b) == 17

    def test_equality(self):
        """Test clock equality."""
        node = PyNodeId("node-1")

        clock1 = PyVectorClock.with_node(node, 5)
        clock2 = PyVectorClock.with_node(node, 5)
        clock3 = PyVectorClock.with_node(node, 6)

        assert clock1 == clock2
        assert clock1 != clock3


class TestCausalOrder:
    """Tests for PyCausalOrder."""

    def test_needs_merge(self):
        """Test needs_merge for different orderings."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        clock1 = PyVectorClock.with_node(node_a, 1)
        clock2 = PyVectorClock.with_node(node_b, 1)

        # Concurrent needs merge
        order = clock1.compare(clock2)
        assert order.needs_merge()

        # Sequential doesn't need merge
        clock3 = PyVectorClock.with_node(node_a, 2)
        order2 = clock1.compare(clock3)
        assert not order2.needs_merge()

    def test_should_apply(self):
        """Test should_apply logic."""
        node = PyNodeId("node-1")

        clock1 = PyVectorClock.with_node(node, 1)
        clock2 = PyVectorClock.with_node(node, 2)

        # clock1 is before clock2, so from clock1's perspective, apply clock2
        order = clock1.compare(clock2)
        assert order.should_apply()  # Other is newer

        # clock2 is after clock1, so from clock2's perspective, don't apply clock1
        order2 = clock2.compare(clock1)
        assert not order2.should_apply()  # We're newer

    def test_order_string_values(self):
        """Test that order attribute has expected values."""
        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        clock1 = PyVectorClock.with_node(node_a, 1)
        clock2 = PyVectorClock.with_node(node_a, 2)
        clock3 = PyVectorClock.with_node(node_b, 1)
        clock4 = PyVectorClock.with_node(node_a, 1)

        assert clock1.compare(clock2).order == "before"
        assert clock2.compare(clock1).order == "after"
        assert clock1.compare(clock3).order == "concurrent"
        assert clock1.compare(clock4).order == "equal"


class TestDistributedScenarios:
    """Integration tests for distributed scenarios."""

    def test_three_node_convergence(self):
        """Test that three nodes can track causality correctly."""
        node_sf = PyNodeId("san-francisco")
        node_ny = PyNodeId("new-york")
        node_tokyo = PyNodeId("tokyo")

        # Each node starts independently
        clock_sf = PyVectorClock()
        clock_sf.tick(node_sf)

        clock_ny = PyVectorClock()
        clock_ny.tick(node_ny)

        clock_tokyo = PyVectorClock()
        clock_tokyo.tick(node_tokyo)

        # All are concurrent
        assert clock_sf.concurrent_with(clock_ny)
        assert clock_ny.concurrent_with(clock_tokyo)
        assert clock_sf.concurrent_with(clock_tokyo)

        # NY receives from both SF and Tokyo
        clock_ny.merge(clock_sf)
        clock_ny.merge(clock_tokyo)
        clock_ny.tick(node_ny)

        # Now NY is after both
        assert clock_sf.happened_before(clock_ny)
        assert clock_tokyo.happened_before(clock_ny)

    def test_algebraic_merge_with_causality(self):
        """Test combining vector clocks with algebraic merge."""
        from _rhizo import PyOpType, PyAlgebraicValue, algebraic_merge

        node_a = PyNodeId("a")
        node_b = PyNodeId("b")

        # Node A: increment counter
        clock_a = PyVectorClock()
        clock_a.tick(node_a)
        delta_a = PyAlgebraicValue.integer(5)

        # Node B: increment counter (concurrent)
        clock_b = PyVectorClock()
        clock_b.tick(node_b)
        delta_b = PyAlgebraicValue.integer(3)

        # Detect concurrent updates
        order = clock_a.compare(clock_b)
        assert order.order == "concurrent"
        assert order.needs_merge()

        # Merge using algebraic properties
        op_type = PyOpType("add")
        merged = algebraic_merge(op_type, delta_a, delta_b)

        # Result: 5 + 3 = 8
        assert str(merged) == "8"
        assert merged.is_numeric()

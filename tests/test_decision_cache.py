"""
Unit tests for DecisionCache class.

Tests verify thread-safe operations, non-blocking reads with timeout,
and proper storage/retrieval of zone states and control decisions.
"""

import threading
import time
from datetime import datetime

import pytest

from eco_loop_building_agents.decision_cache import DecisionCache
from eco_loop_building_agents.models import ZoneState, ControlDecision


class TestDecisionCache:
    """Test suite for DecisionCache class."""
    
    def test_write_and_read_zone_state(self):
        """Test basic zone state write and read operations."""
        cache = DecisionCache()
        
        zone_state = ZoneState(
            zone_id="Zone1",
            temperature=22.5,
            humidity=0.45,
            occupancy=3,
            pmv=0.2,
            timestamp=datetime.now()
        )
        
        cache.write_zone_state(zone_state)
        states = cache.read_zone_states()
        
        assert "Zone1" in states
        assert states["Zone1"].temperature == 22.5
        assert states["Zone1"].humidity == 0.45
        assert states["Zone1"].occupancy == 3
        assert states["Zone1"].pmv == 0.2
    
    def test_write_and_read_decision(self):
        """Test basic control decision write and read operations."""
        cache = DecisionCache()
        
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="ai"
        )
        
        cache.write_decision(decision)
        result = cache.read_decision("Zone1", timeout_ms=10)
        
        assert result is not None
        assert result.heating_setpoint == 20.0
        assert result.cooling_setpoint == 24.0
        assert result.lighting_fraction == 0.8
        assert result.source == "ai"
    
    def test_read_decision_timeout(self):
        """Test that read_decision returns None when no decision exists and timeout expires."""
        cache = DecisionCache()
        
        start_time = time.time()
        result = cache.read_decision("NonExistentZone", timeout_ms=50)
        elapsed_ms = (time.time() - start_time) * 1000
        
        assert result is None
        assert elapsed_ms >= 50  # Should wait at least the timeout period
        assert elapsed_ms < 100  # Should not wait significantly longer
    
    def test_read_decision_returns_immediately_if_exists(self):
        """Test that read_decision returns immediately if decision exists."""
        cache = DecisionCache()
        
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="ai"
        )
        cache.write_decision(decision)
        
        start_time = time.time()
        result = cache.read_decision("Zone1", timeout_ms=100)
        elapsed_ms = (time.time() - start_time) * 1000
        
        assert result is not None
        assert elapsed_ms < 10  # Should return almost immediately
    
    def test_multiple_zones(self):
        """Test handling of multiple zones simultaneously."""
        cache = DecisionCache()
        
        # Write states for multiple zones
        for i in range(1, 4):
            zone_state = ZoneState(
                zone_id=f"Zone{i}",
                temperature=20.0 + i,
                humidity=0.4 + i * 0.05,
                occupancy=i,
                pmv=0.1 * i,
                timestamp=datetime.now()
            )
            cache.write_zone_state(zone_state)
        
        states = cache.read_zone_states()
        assert len(states) == 3
        assert all(f"Zone{i}" in states for i in range(1, 4))
    
    def test_overwrite_existing_state(self):
        """Test that writing to the same zone_id overwrites previous state."""
        cache = DecisionCache()
        
        # Write initial state
        zone_state1 = ZoneState(
            zone_id="Zone1",
            temperature=20.0,
            humidity=0.4,
            occupancy=2,
            pmv=0.1,
            timestamp=datetime.now()
        )
        cache.write_zone_state(zone_state1)
        
        # Overwrite with new state
        zone_state2 = ZoneState(
            zone_id="Zone1",
            temperature=25.0,
            humidity=0.5,
            occupancy=5,
            pmv=0.3,
            timestamp=datetime.now()
        )
        cache.write_zone_state(zone_state2)
        
        states = cache.read_zone_states()
        assert states["Zone1"].temperature == 25.0
        assert states["Zone1"].occupancy == 5
    
    def test_read_zone_states_returns_copy(self):
        """Test that read_zone_states returns a copy, not the internal dict."""
        cache = DecisionCache()
        
        zone_state = ZoneState(
            zone_id="Zone1",
            temperature=22.5,
            humidity=0.45,
            occupancy=3,
            pmv=0.2,
            timestamp=datetime.now()
        )
        cache.write_zone_state(zone_state)
        
        states1 = cache.read_zone_states()
        states2 = cache.read_zone_states()
        
        # Modifying the returned dict should not affect the cache
        states1["Zone2"] = ZoneState(
            zone_id="Zone2",
            temperature=23.0,
            humidity=0.5,
            occupancy=1,
            pmv=0.0,
            timestamp=datetime.now()
        )
        
        # states2 should not have Zone2
        assert "Zone2" not in states2
        assert len(states2) == 1
    
    def test_concurrent_writes_zone_states(self):
        """Test thread safety of concurrent zone state writes."""
        cache = DecisionCache()
        num_threads = 10
        writes_per_thread = 100
        
        def write_states(thread_id):
            for i in range(writes_per_thread):
                zone_state = ZoneState(
                    zone_id=f"Zone{thread_id}",
                    temperature=20.0 + i * 0.1,
                    humidity=0.4,
                    occupancy=thread_id,
                    pmv=0.0,
                    timestamp=datetime.now()
                )
                cache.write_zone_state(zone_state)
        
        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=write_states, args=(i,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        states = cache.read_zone_states()
        assert len(states) == num_threads
        # Each zone should have the last written value (from iteration writes_per_thread-1)
        for i in range(num_threads):
            assert f"Zone{i}" in states
            assert states[f"Zone{i}"].occupancy == i
    
    def test_concurrent_reads_and_writes(self):
        """Test thread safety when reading and writing simultaneously."""
        cache = DecisionCache()
        num_writers = 5
        num_readers = 5
        operations_per_thread = 50
        
        results = {"read_count": 0, "write_count": 0}
        lock = threading.Lock()
        
        def writer(thread_id):
            for i in range(operations_per_thread):
                decision = ControlDecision(
                    zone_id=f"Zone{thread_id}",
                    heating_setpoint=20.0,
                    cooling_setpoint=24.0,
                    lighting_fraction=0.5,
                    timestamp=datetime.now(),
                    source="ai"
                )
                cache.write_decision(decision)
                with lock:
                    results["write_count"] += 1
                time.sleep(0.001)  # Small delay to interleave operations
        
        def reader():
            for _ in range(operations_per_thread):
                # Try to read from all zones
                for i in range(num_writers):
                    cache.read_decision(f"Zone{i}", timeout_ms=5)
                with lock:
                    results["read_count"] += 1
                time.sleep(0.001)
        
        threads = []
        
        # Start writers
        for i in range(num_writers):
            thread = threading.Thread(target=writer, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Start readers
        for _ in range(num_readers):
            thread = threading.Thread(target=reader)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify all operations completed
        assert results["write_count"] == num_writers * operations_per_thread
        assert results["read_count"] == num_readers * operations_per_thread
    
    def test_stale_decision_behavior(self):
        """Test that stale decisions are retained until new ones arrive."""
        cache = DecisionCache()
        
        # Write initial decision
        decision1 = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
            source="ai"
        )
        cache.write_decision(decision1)
        
        # Read it back multiple times - should always return the same decision
        result1 = cache.read_decision("Zone1", timeout_ms=10)
        time.sleep(0.1)  # Simulate time passing
        result2 = cache.read_decision("Zone1", timeout_ms=10)
        
        assert result1 is not None
        assert result2 is not None
        assert result1.heating_setpoint == result2.heating_setpoint
        
        # Write new decision - should overwrite
        decision2 = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=21.0,
            cooling_setpoint=25.0,
            lighting_fraction=0.9,
            timestamp=datetime(2024, 1, 1, 11, 0, 0),
            source="ai"
        )
        cache.write_decision(decision2)
        
        result3 = cache.read_decision("Zone1", timeout_ms=10)
        assert result3.heating_setpoint == 21.0
    
    def test_clear_zone_states(self):
        """Test clearing zone states."""
        cache = DecisionCache()
        
        zone_state = ZoneState(
            zone_id="Zone1",
            temperature=22.5,
            humidity=0.45,
            occupancy=3,
            pmv=0.2,
            timestamp=datetime.now()
        )
        cache.write_zone_state(zone_state)
        
        cache.clear_zone_states()
        states = cache.read_zone_states()
        
        assert len(states) == 0
    
    def test_clear_decisions(self):
        """Test clearing decisions."""
        cache = DecisionCache()
        
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="ai"
        )
        cache.write_decision(decision)
        
        cache.clear_decisions()
        result = cache.read_decision("Zone1", timeout_ms=10)
        
        assert result is None
    
    def test_clear_all(self):
        """Test clearing both zone states and decisions."""
        cache = DecisionCache()
        
        zone_state = ZoneState(
            zone_id="Zone1",
            temperature=22.5,
            humidity=0.45,
            occupancy=3,
            pmv=0.2,
            timestamp=datetime.now()
        )
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="ai"
        )
        
        cache.write_zone_state(zone_state)
        cache.write_decision(decision)
        cache.clear_all()
        
        states = cache.read_zone_states()
        result = cache.read_decision("Zone1", timeout_ms=10)
        
        assert len(states) == 0
        assert result is None
    
    def test_read_all_decisions(self):
        """Test reading all decisions at once."""
        cache = DecisionCache()
        
        # Write decisions for multiple zones
        for i in range(1, 4):
            decision = ControlDecision(
                zone_id=f"Zone{i}",
                heating_setpoint=20.0 + i,
                cooling_setpoint=24.0 + i,
                lighting_fraction=0.5 + i * 0.1,
                timestamp=datetime.now(),
                source="ai"
            )
            cache.write_decision(decision)
        
        decisions = cache.read_all_decisions()
        
        assert len(decisions) == 3
        assert all(f"Zone{i}" in decisions for i in range(1, 4))
        assert decisions["Zone1"].heating_setpoint == 21.0
        assert decisions["Zone2"].heating_setpoint == 22.0
        assert decisions["Zone3"].heating_setpoint == 23.0


class TestDecisionCacheTimeout:
    """Test suite specifically for timeout behavior."""
    
    def test_timeout_precision(self):
        """Test that timeout is reasonably precise (within acceptable bounds)."""
        cache = DecisionCache()
        timeout_ms = 100
        
        start_time = time.time()
        result = cache.read_decision("NonExistent", timeout_ms=timeout_ms)
        elapsed_ms = (time.time() - start_time) * 1000
        
        assert result is None
        # Allow 20% tolerance for timing precision
        assert timeout_ms <= elapsed_ms <= timeout_ms * 1.2
    
    def test_decision_becomes_available_during_timeout(self):
        """Test that read_decision returns as soon as decision becomes available."""
        cache = DecisionCache()
        
        result_holder = {"result": None, "elapsed_ms": 0}
        
        def reader():
            start_time = time.time()
            result = cache.read_decision("Zone1", timeout_ms=500)
            elapsed_ms = (time.time() - start_time) * 1000
            result_holder["result"] = result
            result_holder["elapsed_ms"] = elapsed_ms
        
        def writer():
            time.sleep(0.1)  # Wait 100ms before writing
            decision = ControlDecision(
                zone_id="Zone1",
                heating_setpoint=20.0,
                cooling_setpoint=24.0,
                lighting_fraction=0.8,
                timestamp=datetime.now(),
                source="ai"
            )
            cache.write_decision(decision)
        
        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)
        
        reader_thread.start()
        writer_thread.start()
        
        reader_thread.join()
        writer_thread.join()
        
        # Reader should get the decision and return in approximately 100ms
        assert result_holder["result"] is not None
        assert 100 <= result_holder["elapsed_ms"] <= 200  # Should be around 100ms, not full 500ms timeout


class TestDecisionCacheEdgeCases:
    """Test suite for edge cases and error conditions."""
    
    def test_empty_cache_reads(self):
        """Test reading from empty cache."""
        cache = DecisionCache()
        
        states = cache.read_zone_states()
        decisions = cache.read_all_decisions()
        decision = cache.read_decision("Zone1", timeout_ms=10)
        
        assert states == {}
        assert decisions == {}
        assert decision is None
    
    def test_zero_timeout(self):
        """Test behavior with zero timeout."""
        cache = DecisionCache()
        
        result = cache.read_decision("NonExistent", timeout_ms=0)
        assert result is None
    
    def test_very_long_timeout(self):
        """Test that very long timeout still returns immediately when decision exists."""
        cache = DecisionCache()
        
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="ai"
        )
        cache.write_decision(decision)
        
        start_time = time.time()
        result = cache.read_decision("Zone1", timeout_ms=10000)
        elapsed_ms = (time.time() - start_time) * 1000
        
        assert result is not None
        assert elapsed_ms < 50  # Should return almost immediately

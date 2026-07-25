"""
Integration tests for StructuredLogger in realistic simulation scenarios.

Tests verify logger behavior in multi-threaded contexts mimicking actual
EnergyPlus callback and orchestration loop interaction patterns.
"""

import json
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from eco_loop_building_agents.structured_logger import StructuredLogger
from eco_loop_building_agents.models import ZoneState, ControlDecision


class TestStructuredLoggerIntegration:
    """Integration tests for StructuredLogger."""
    
    def test_simulated_decision_cycle_workflow(self):
        """Test complete decision cycle logging workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                # Simulate simulation start
                logger.log_simulation_start(
                    idf_path="./models/test.idf",
                    epw_path="./weather/test.epw",
                    decision_interval_hours=1
                )
                
                # Simulate multiple decision cycles
                for cycle in range(3):
                    sim_time = datetime(2024, 7, 15, 10 + cycle, 0)
                    
                    # Zone state update (from EnergyPlus callback)
                    zone_states = {
                        "Zone1": ZoneState(
                            zone_id="Zone1",
                            temperature=22.0 + cycle * 0.5,
                            humidity=0.45,
                            occupancy=5,
                            pmv=0.1 + cycle * 0.1,
                            timestamp=sim_time
                        )
                    }
                    
                    # Decision cycle start
                    logger.log_decision_cycle_start(sim_time, zone_states)
                    
                    # LLM request
                    logger.log_llm_request(
                        prompt_length=1024,
                        timeout=30.0,
                        zone_count=1
                    )
                    
                    # LLM response
                    logger.log_llm_response(
                        success=True,
                        response_time_ms=2000 + cycle * 500
                    )
                    
                    # Decision validation
                    decision = ControlDecision(
                        zone_id="Zone1",
                        heating_setpoint=20.0,
                        cooling_setpoint=24.0,
                        lighting_fraction=1.0,
                        timestamp=sim_time,
                        source="ai"
                    )
                    logger.log_decision_validated(decision, modified=False)
                    
                    # Energy metrics
                    logger.log_energy_metrics(
                        simulation_time=sim_time,
                        hvac_energy_kwh=100.0 + cycle * 25.0,
                        lighting_energy_kwh=40.0 + cycle * 10.0,
                        total_energy_kwh=140.0 + cycle * 35.0
                    )
                
                # Simulate simulation end
                logger.log_simulation_end(
                    total_duration_seconds=3600.0,
                    decision_cycles_completed=3
                )
            
            # Verify log file structure
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            # Expected: 1 start + (3 cycles * 5 events) + 1 end = 17 entries
            assert len(lines) == 17
            
            # Verify first entry is simulation start
            first_entry = json.loads(lines[0])
            assert first_entry["event"] == "simulation_start"
            
            # Verify last entry is simulation end
            last_entry = json.loads(lines[-1])
            assert last_entry["event"] == "simulation_end"
            assert last_entry["decision_cycles_completed"] == 3
            
            # Verify decision cycle structure
            decision_cycle_events = []
            for line in lines[1:-1]:  # Exclude start/end
                entry = json.loads(line)
                decision_cycle_events.append(entry["event"])
            
            # Each cycle should have: decision_cycle_start, llm_request, 
            # llm_response, decision_validated, energy_metrics
            expected_pattern = [
                "decision_cycle_start",
                "llm_request",
                "llm_response",
                "decision_validated",
                "energy_metrics"
            ]
            
            for i in range(3):
                cycle_events = decision_cycle_events[i * 5:(i + 1) * 5]
                assert cycle_events == expected_pattern
    
    def test_concurrent_logging_from_simulated_threads(self):
        """Test logger handles concurrent writes from simulated EnergyPlus and orchestration threads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(tmpdir, log_level="INFO")
            
            def energyplus_callback_thread():
                """Simulate EnergyPlus callback thread writing zone states."""
                for i in range(20):
                    zone_state = ZoneState(
                        zone_id="Zone1",
                        temperature=22.0 + i * 0.1,
                        humidity=0.45,
                        occupancy=5,
                        pmv=0.2,
                        timestamp=datetime(2024, 7, 15, 10, i)
                    )
                    logger.log_decision_cycle_start(
                        datetime(2024, 7, 15, 10, i),
                        {"Zone1": zone_state}
                    )
                    time.sleep(0.001)  # Simulate callback timing
            
            def orchestration_loop_thread():
                """Simulate orchestration loop thread making LLM requests."""
                for i in range(20):
                    logger.log_llm_request(
                        prompt_length=1024,
                        timeout=30.0,
                        zone_count=1
                    )
                    logger.log_llm_response(
                        success=True,
                        response_time_ms=2500.0 + i * 10
                    )
                    time.sleep(0.001)  # Simulate decision cycle timing
            
            def safety_governor_thread():
                """Simulate safety governor thread validating decisions."""
                for i in range(20):
                    decision = ControlDecision(
                        zone_id="Zone1",
                        heating_setpoint=20.0,
                        cooling_setpoint=24.0,
                        lighting_fraction=1.0,
                        timestamp=datetime(2024, 7, 15, 10, i),
                        source="ai"
                    )
                    logger.log_decision_validated(decision, modified=False)
                    time.sleep(0.001)
            
            # Run all threads concurrently
            threads = [
                threading.Thread(target=energyplus_callback_thread),
                threading.Thread(target=orchestration_loop_thread),
                threading.Thread(target=safety_governor_thread)
            ]
            
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            logger.close()
            
            # Verify all entries are valid JSON and from expected components
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            # Should have 80 total entries:
            # - 20 decision_cycle_start (energyplus thread)
            # - 20 llm_request + 20 llm_response (orchestration thread)
            # - 20 decision_validated (safety governor thread)
            assert len(lines) == 80
            
            components = set()
            events = set()
            
            for line in lines:
                entry = json.loads(line)  # Should not raise
                components.add(entry["component"])
                events.add(entry["event"])
            
            # Verify expected components logged
            assert components == {"orchestration_loop", "llm_client", "safety_governor"}
            
            # Verify expected events occurred
            assert "decision_cycle_start" in events
            assert "llm_request" in events
            assert "llm_response" in events
            assert "decision_validated" in events
    
    def test_fallback_recovery_workflow(self):
        """Test logging during LLM failure and fallback recovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                # Normal operation
                logger.log_health_check(success=True, response_time_ms=150.0)
                
                # First failure
                logger.log_llm_response(
                    success=False,
                    response_time_ms=0,
                    error_message="Connection timeout"
                )
                
                # Second failure
                logger.log_llm_response(
                    success=False,
                    response_time_ms=0,
                    error_message="Connection timeout"
                )
                
                # Third failure triggers fallback
                logger.log_llm_response(
                    success=False,
                    response_time_ms=0,
                    error_message="Connection timeout"
                )
                
                logger.log_fallback_activation(
                    trigger_reason="3 consecutive LLM failures",
                    consecutive_failures=3
                )
                
                # Fallback control decisions
                for i in range(3):
                    decision = ControlDecision(
                        zone_id="Zone1",
                        heating_setpoint=21.0,
                        cooling_setpoint=24.0,
                        lighting_fraction=0.8,
                        timestamp=datetime(2024, 7, 15, 11, i),
                        source="fallback"
                    )
                    logger.log_decision_validated(decision, modified=False)
                
                # Recovery
                logger.log_health_check(success=True, response_time_ms=180.0)
                logger.log_fallback_deactivation()
                
                # Normal operation resumes
                logger.log_llm_response(success=True, response_time_ms=2200.0)
            
            # Verify workflow captured in logs
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            events = [json.loads(line)["event"] for line in lines]
            
            # Verify key events in sequence
            assert "health_check" in events
            assert "fallback_activated" in events
            assert "fallback_deactivated" in events
            
            # Count failures
            failure_count = sum(
                1 for line in lines
                if json.loads(line).get("event") == "llm_response"
                and json.loads(line).get("success") is False
            )
            assert failure_count == 3
            
            # Verify fallback decisions
            fallback_decisions = [
                json.loads(line)
                for line in lines
                if json.loads(line).get("event") == "decision_validated"
                and json.loads(line).get("source") == "fallback"
            ]
            assert len(fallback_decisions) == 3
    
    def test_pmv_violation_tracking(self):
        """Test tracking of PMV violations over simulation run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                # Simulate zone with varying PMV
                for hour in range(24):
                    sim_time = datetime(2024, 7, 15, hour, 0)
                    
                    # PMV varies sinusoidally (some violations)
                    import math
                    pmv = 0.6 * math.sin(hour * math.pi / 12)
                    
                    zone_state = ZoneState(
                        zone_id="Zone1",
                        temperature=22.0 + pmv * 2,
                        humidity=0.45,
                        occupancy=5 if 9 <= hour < 17 else 0,
                        pmv=pmv,
                        timestamp=sim_time
                    )
                    
                    # Log zone state
                    logger.log_decision_cycle_start(
                        sim_time,
                        {"Zone1": zone_state}
                    )
                    
                    # Log violation if outside ASHRAE 55 band
                    if pmv < -0.5 or pmv > 0.5:
                        logger.log_pmv_violation(
                            zone_id="Zone1",
                            pmv=pmv,
                            timestamp=sim_time
                        )
            
            # Count violations
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            violations = [
                json.loads(line)
                for line in lines
                if json.loads(line).get("event") == "pmv_violation"
            ]
            
            # Should have some violations (when sin(x) > 0.833 or < -0.833)
            assert len(violations) > 0
            
            # Verify violation structure
            for violation in violations:
                assert "zone" in violation
                assert "pmv" in violation
                assert abs(violation["pmv"]) > 0.5  # Outside ASHRAE band

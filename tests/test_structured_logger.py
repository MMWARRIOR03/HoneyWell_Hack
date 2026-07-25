"""
Unit tests for StructuredLogger class.

Tests cover JSON-lines logging, timestamp-based file creation, thread safety,
and specialized logging methods for decision cycles, exceptions, and PMV violations.
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from eco_loop_building_agents.structured_logger import StructuredLogger, LogLevel
from eco_loop_building_agents.models import ZoneState, ControlDecision


class TestStructuredLoggerBasics:
    """Test basic StructuredLogger initialization and configuration."""
    
    def test_logger_initialization(self):
        """Test logger creates directory and file with timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(tmpdir, log_level="INFO")
            
            # Check log directory exists
            assert Path(tmpdir).exists()
            
            # Check log file created with timestamp format
            log_files = list(Path(tmpdir).glob("run_*.jsonl"))
            assert len(log_files) == 1
            assert logger.log_file_path.exists()
            
            logger.close()
    
    def test_logger_creates_missing_directory(self):
        """Test logger creates log directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "nested" / "logs"
            assert not log_dir.exists()
            
            logger = StructuredLogger(str(log_dir), log_level="INFO")
            assert log_dir.exists()
            
            logger.close()
    
    def test_invalid_log_level_raises_error(self):
        """Test that invalid log level raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="log_level must be one of"):
                StructuredLogger(tmpdir, log_level="INVALID")
    
    def test_context_manager(self):
        """Test logger works as context manager and closes file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.info("test_component", "test_event")
            
            # File should be closed after context exit
            assert logger._file_handle.closed


class TestLogLevelFiltering:
    """Test log level filtering functionality."""
    
    def test_info_level_filters_debug(self):
        """Test INFO level filters out DEBUG messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.debug("test", "debug_event")
                logger.info("test", "info_event")
            
            # Read log file
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            assert len(lines) == 1
            log_entry = json.loads(lines[0])
            assert log_entry["level"] == "INFO"
            assert log_entry["event"] == "info_event"
    
    def test_warning_level_filters_info_and_debug(self):
        """Test WARNING level filters out INFO and DEBUG."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="WARNING") as logger:
                logger.debug("test", "debug_event")
                logger.info("test", "info_event")
                logger.warning("test", "warning_event")
                logger.error("test", "error_event")
            
            # Read log file
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            assert len(lines) == 2
            assert json.loads(lines[0])["level"] == "WARNING"
            assert json.loads(lines[1])["level"] == "ERROR"
    
    def test_debug_level_logs_all(self):
        """Test DEBUG level logs all messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="DEBUG") as logger:
                logger.debug("test", "debug_event")
                logger.info("test", "info_event")
                logger.warning("test", "warning_event")
            
            # Read log file
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            assert len(lines) == 3


class TestJSONLinesFormat:
    """Test JSON-lines format compliance."""
    
    def test_each_log_entry_is_valid_json(self):
        """Test that each line is a valid JSON object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.info("component1", "event1", field1="value1")
                logger.info("component2", "event2", field2="value2")
            
            # Read and parse each line
            with open(logger.log_file_path) as f:
                for line in f:
                    log_entry = json.loads(line)  # Should not raise
                    assert isinstance(log_entry, dict)
    
    def test_log_entry_contains_required_fields(self):
        """Test log entries have required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.info("test_component", "test_event", extra="data")
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            # Check required fields
            assert "timestamp" in log_entry
            assert "level" in log_entry
            assert "component" in log_entry
            assert "event" in log_entry
            assert log_entry["level"] == "INFO"
            assert log_entry["component"] == "test_component"
            assert log_entry["event"] == "test_event"
            assert log_entry["extra"] == "data"
    
    def test_timestamp_format_is_iso8601(self):
        """Test timestamp is ISO 8601 format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.info("test", "event")
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            # Parse timestamp - should not raise
            timestamp_str = log_entry["timestamp"]
            assert timestamp_str.endswith("Z")
            datetime.fromisoformat(timestamp_str[:-1])  # Remove Z for parsing


class TestDecisionCycleLogging:
    """Test decision cycle logging methods."""
    
    def test_log_decision_cycle_start(self):
        """Test logging decision cycle start with zone states."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                zone_states = {
                    "Zone1": ZoneState(
                        zone_id="Zone1",
                        temperature=22.5,
                        humidity=0.45,
                        occupancy=5,
                        pmv=0.2,
                        timestamp=datetime(2024, 7, 15, 10, 0)
                    ),
                    "Zone2": ZoneState(
                        zone_id="Zone2",
                        temperature=21.0,
                        humidity=0.50,
                        occupancy=0,
                        pmv=-0.1,
                        timestamp=datetime(2024, 7, 15, 10, 0)
                    )
                }
                
                logger.log_decision_cycle_start(
                    datetime(2024, 7, 15, 10, 0),
                    zone_states
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "decision_cycle_start"
            assert log_entry["component"] == "orchestration_loop"
            assert "zone_states" in log_entry
            assert "Zone1" in log_entry["zone_states"]
            assert log_entry["zone_states"]["Zone1"]["temperature"] == 22.5
            assert log_entry["zone_states"]["Zone1"]["pmv"] == 0.2
    
    def test_log_llm_request(self):
        """Test logging LLM request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_llm_request(
                    prompt_length=1024,
                    timeout=30.0,
                    zone_count=2
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "llm_request"
            assert log_entry["component"] == "llm_client"
            assert log_entry["prompt_length"] == 1024
            assert log_entry["timeout"] == 30.0
            assert log_entry["zone_count"] == 2
    
    def test_log_llm_response_success(self):
        """Test logging successful LLM response."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_llm_response(
                    success=True,
                    response_time_ms=3245.5
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "llm_response"
            assert log_entry["level"] == "INFO"
            assert log_entry["success"] is True
            assert log_entry["response_time_ms"] == 3245.5
    
    def test_log_llm_response_failure(self):
        """Test logging failed LLM response."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_llm_response(
                    success=False,
                    response_time_ms=0,
                    error_message="Connection timeout"
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "llm_response"
            assert log_entry["level"] == "ERROR"
            assert log_entry["success"] is False
            assert log_entry["error_message"] == "Connection timeout"


class TestDecisionValidationLogging:
    """Test decision validation logging."""
    
    def test_log_decision_validated_unmodified(self):
        """Test logging validated decision that wasn't modified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                decision = ControlDecision(
                    zone_id="Zone1",
                    heating_setpoint=20.0,
                    cooling_setpoint=24.0,
                    lighting_fraction=1.0,
                    timestamp=datetime(2024, 7, 15, 10, 0),
                    source="ai"
                )
                
                logger.log_decision_validated(decision, modified=False)
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "decision_validated"
            assert log_entry["component"] == "safety_governor"
            assert log_entry["level"] == "INFO"
            assert log_entry["zone"] == "Zone1"
            assert log_entry["heating_setpoint"] == 20.0
            assert log_entry["cooling_setpoint"] == 24.0
            assert log_entry["modified"] is False
    
    def test_log_decision_validated_modified(self):
        """Test logging validated decision that was modified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                decision = ControlDecision(
                    zone_id="Zone1",
                    heating_setpoint=18.0,
                    cooling_setpoint=28.0,
                    lighting_fraction=0.8,
                    timestamp=datetime(2024, 7, 15, 10, 0),
                    source="ai"
                )
                
                logger.log_decision_validated(decision, modified=True)
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["level"] == "WARNING"
            assert log_entry["modified"] is True


class TestPMVViolationLogging:
    """Test PMV violation logging."""
    
    def test_log_pmv_violation(self):
        """Test logging PMV comfort violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_pmv_violation(
                    zone_id="Zone1",
                    pmv=0.8,
                    timestamp=datetime(2024, 7, 15, 14, 30)
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "pmv_violation"
            assert log_entry["component"] == "safety_governor"
            assert log_entry["level"] == "WARNING"
            assert log_entry["zone"] == "Zone1"
            assert log_entry["pmv"] == 0.8


class TestFallbackLogging:
    """Test fallback activation/deactivation logging."""
    
    def test_log_fallback_activation(self):
        """Test logging fallback activation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_fallback_activation(
                    trigger_reason="LLM timeout",
                    consecutive_failures=3
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "fallback_activated"
            assert log_entry["level"] == "WARNING"
            assert log_entry["trigger_reason"] == "LLM timeout"
            assert log_entry["consecutive_failures"] == 3
    
    def test_log_fallback_deactivation(self):
        """Test logging fallback deactivation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_fallback_deactivation()
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "fallback_deactivated"
            assert log_entry["level"] == "INFO"


class TestExceptionLogging:
    """Test exception logging."""
    
    def test_log_exception_basic(self):
        """Test logging exception with basic details."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_exception(
                    component="llm_client",
                    exception_type="ConnectionError",
                    message="Connection refused",
                    stack_trace="Traceback...\nLine 123"
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "exception"
            assert log_entry["component"] == "llm_client"
            assert log_entry["level"] == "ERROR"
            assert log_entry["exception_type"] == "ConnectionError"
            assert log_entry["message"] == "Connection refused"
            assert "Traceback" in log_entry["stack_trace"]
    
    def test_log_exception_with_context(self):
        """Test logging exception with additional context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_exception(
                    component="safety_governor",
                    exception_type="ValueError",
                    message="Invalid setpoint",
                    stack_trace="Traceback...",
                    context={
                        "zone_id": "Zone1",
                        "heating_setpoint": 25.0,
                        "cooling_setpoint": 22.0
                    }
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert "context" in log_entry
            assert log_entry["context"]["zone_id"] == "Zone1"


class TestHealthCheckLogging:
    """Test health check logging."""
    
    def test_log_health_check_success(self):
        """Test logging successful health check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_health_check(
                    success=True,
                    response_time_ms=150.5
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "health_check"
            assert log_entry["level"] == "INFO"
            assert log_entry["success"] is True
            assert log_entry["response_time_ms"] == 150.5
    
    def test_log_health_check_failure(self):
        """Test logging failed health check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_health_check(
                    success=False,
                    error_message="Endpoint unreachable"
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["level"] == "WARNING"
            assert log_entry["success"] is False
            assert log_entry["error_message"] == "Endpoint unreachable"


class TestEnergyMetricsLogging:
    """Test energy metrics logging."""
    
    def test_log_energy_metrics(self):
        """Test logging cumulative energy metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_energy_metrics(
                    simulation_time=datetime(2024, 7, 15, 12, 0),
                    hvac_energy_kwh=125.5,
                    lighting_energy_kwh=45.2,
                    total_energy_kwh=170.7
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "energy_metrics"
            assert log_entry["hvac_energy_kwh"] == 125.5
            assert log_entry["lighting_energy_kwh"] == 45.2
            assert log_entry["total_energy_kwh"] == 170.7


class TestSimulationLifecycleLogging:
    """Test simulation lifecycle logging."""
    
    def test_log_simulation_start(self):
        """Test logging simulation start."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_simulation_start(
                    idf_path="./models/baseline.idf",
                    epw_path="./weather/Delhi.epw",
                    decision_interval_hours=1
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "simulation_start"
            assert log_entry["idf_path"] == "./models/baseline.idf"
            assert log_entry["decision_interval_hours"] == 1
    
    def test_log_simulation_end(self):
        """Test logging simulation end."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="INFO") as logger:
                logger.log_simulation_end(
                    total_duration_seconds=3600.5,
                    decision_cycles_completed=24
                )
            
            with open(logger.log_file_path) as f:
                log_entry = json.loads(f.readline())
            
            assert log_entry["event"] == "simulation_end"
            assert log_entry["total_duration_seconds"] == 3600.5
            assert log_entry["decision_cycles_completed"] == 24


class TestThreadSafety:
    """Test thread-safe concurrent logging."""
    
    def test_concurrent_writes_from_multiple_threads(self):
        """Test that concurrent writes don't corrupt log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(tmpdir, log_level="INFO")
            
            def write_logs(thread_id, count):
                for i in range(count):
                    logger.info(
                        f"thread_{thread_id}",
                        f"event_{i}",
                        thread_id=thread_id,
                        iteration=i
                    )
            
            # Create multiple threads writing concurrently
            threads = []
            num_threads = 10
            logs_per_thread = 20
            
            for i in range(num_threads):
                t = threading.Thread(target=write_logs, args=(i, logs_per_thread))
                threads.append(t)
                t.start()
            
            # Wait for all threads to complete
            for t in threads:
                t.join()
            
            logger.close()
            
            # Verify all logs were written and are valid JSON
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            assert len(lines) == num_threads * logs_per_thread
            
            # Verify each line is valid JSON
            for line in lines:
                log_entry = json.loads(line)
                assert "timestamp" in log_entry
                assert "thread_id" in log_entry
                assert "iteration" in log_entry
    
    def test_concurrent_writes_with_different_methods(self):
        """Test concurrent calls to different logging methods."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(tmpdir, log_level="DEBUG")
            
            def worker1():
                for _ in range(10):
                    logger.info("worker1", "info_event", data="test")
                    time.sleep(0.001)
            
            def worker2():
                for _ in range(10):
                    logger.warning("worker2", "warning_event", data="test")
                    time.sleep(0.001)
            
            def worker3():
                for _ in range(10):
                    logger.error("worker3", "error_event", data="test")
                    time.sleep(0.001)
            
            threads = [
                threading.Thread(target=worker1),
                threading.Thread(target=worker2),
                threading.Thread(target=worker3)
            ]
            
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            logger.close()
            
            # Verify all logs are valid JSON
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            assert len(lines) == 30
            
            for line in lines:
                log_entry = json.loads(line)
                assert log_entry["component"] in ["worker1", "worker2", "worker3"]


class TestGenericLoggingMethods:
    """Test generic logging methods (debug, info, warning, error, critical)."""
    
    def test_all_log_levels(self):
        """Test all generic log level methods."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(tmpdir, log_level="DEBUG") as logger:
                logger.debug("comp", "debug_event", key="value")
                logger.info("comp", "info_event", key="value")
                logger.warning("comp", "warning_event", key="value")
                logger.error("comp", "error_event", key="value")
                logger.critical("comp", "critical_event", key="value")
            
            with open(logger.log_file_path) as f:
                lines = f.readlines()
            
            assert len(lines) == 5
            
            levels = [json.loads(line)["level"] for line in lines]
            assert levels == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

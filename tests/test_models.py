"""
Unit tests for core data models.

Tests validation logic, dataclass initialization, and error handling
for ZoneState, ControlDecision, SafetyConfig, and LLMConfig.
"""

import pytest
from datetime import datetime
from eco_loop_building_agents.models import (
    ZoneState,
    ControlDecision,
    SafetyConfig,
    LLMConfig,
    SystemHealthState,
    LLMResponse,
    FaultConfig,
    SimulationConfig,
    LoggingConfig,
    SystemConfig,
)


class TestZoneState:
    """Tests for ZoneState dataclass."""
    
    def test_valid_zone_state(self):
        """Test creation of valid ZoneState."""
        state = ZoneState(
            zone_id="Zone1",
            temperature=22.5,
            humidity=0.5,
            occupancy=3,
            pmv=0.2,
            timestamp=datetime.now(),
        )
        assert state.zone_id == "Zone1"
        assert state.temperature == 22.5
        assert state.humidity == 0.5
        assert state.occupancy == 3
        assert state.pmv == 0.2
    
    def test_invalid_humidity_below_zero(self):
        """Test that humidity below 0 raises ValueError."""
        with pytest.raises(ValueError, match="Humidity must be between 0 and 1"):
            ZoneState(
                zone_id="Zone1",
                temperature=22.5,
                humidity=-0.1,
                occupancy=3,
                pmv=0.2,
                timestamp=datetime.now(),
            )
    
    def test_invalid_humidity_above_one(self):
        """Test that humidity above 1 raises ValueError."""
        with pytest.raises(ValueError, match="Humidity must be between 0 and 1"):
            ZoneState(
                zone_id="Zone1",
                temperature=22.5,
                humidity=1.5,
                occupancy=3,
                pmv=0.2,
                timestamp=datetime.now(),
            )
    
    def test_invalid_negative_occupancy(self):
        """Test that negative occupancy raises ValueError."""
        with pytest.raises(ValueError, match="Occupancy cannot be negative"):
            ZoneState(
                zone_id="Zone1",
                temperature=22.5,
                humidity=0.5,
                occupancy=-1,
                pmv=0.2,
                timestamp=datetime.now(),
            )


class TestControlDecision:
    """Tests for ControlDecision dataclass."""
    
    def test_valid_control_decision(self):
        """Test creation of valid ControlDecision."""
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="ai",
        )
        assert decision.zone_id == "Zone1"
        assert decision.heating_setpoint == 20.0
        assert decision.cooling_setpoint == 24.0
        assert decision.lighting_fraction == 0.8
        assert decision.source == "ai"
    
    def test_invalid_lighting_fraction_below_zero(self):
        """Test that lighting fraction below 0 raises ValueError."""
        with pytest.raises(ValueError, match="Lighting fraction must be between 0 and 1"):
            ControlDecision(
                zone_id="Zone1",
                heating_setpoint=20.0,
                cooling_setpoint=24.0,
                lighting_fraction=-0.1,
                timestamp=datetime.now(),
                source="ai",
            )
    
    def test_invalid_lighting_fraction_above_one(self):
        """Test that lighting fraction above 1 raises ValueError."""
        with pytest.raises(ValueError, match="Lighting fraction must be between 0 and 1"):
            ControlDecision(
                zone_id="Zone1",
                heating_setpoint=20.0,
                cooling_setpoint=24.0,
                lighting_fraction=1.5,
                timestamp=datetime.now(),
                source="ai",
            )
    
    def test_heating_not_less_than_cooling(self):
        """Test that heating >= cooling raises ValueError."""
        with pytest.raises(ValueError, match="Heating setpoint .* must be less than cooling setpoint"):
            ControlDecision(
                zone_id="Zone1",
                heating_setpoint=24.0,
                cooling_setpoint=24.0,
                lighting_fraction=0.8,
                timestamp=datetime.now(),
                source="ai",
            )
    
    def test_invalid_source(self):
        """Test that invalid source raises ValueError."""
        with pytest.raises(ValueError, match="Source must be 'ai' or 'fallback'"):
            ControlDecision(
                zone_id="Zone1",
                heating_setpoint=20.0,
                cooling_setpoint=24.0,
                lighting_fraction=0.8,
                timestamp=datetime.now(),
                source="invalid",
            )
    
    def test_fallback_source(self):
        """Test that 'fallback' is a valid source."""
        decision = ControlDecision(
            zone_id="Zone1",
            heating_setpoint=20.0,
            cooling_setpoint=24.0,
            lighting_fraction=0.8,
            timestamp=datetime.now(),
            source="fallback",
        )
        assert decision.source == "fallback"


class TestSafetyConfig:
    """Tests for SafetyConfig dataclass."""
    
    def test_valid_safety_config(self):
        """Test creation of valid SafetyConfig with defaults."""
        config = SafetyConfig()
        assert config.min_heating_setpoint == 18.0
        assert config.max_heating_setpoint == 22.0
        assert config.min_cooling_setpoint == 22.0
        assert config.max_cooling_setpoint == 28.0
        assert config.min_deadband == 2.0
        assert config.pmv_min == -0.5
        assert config.pmv_max == 0.5
    
    def test_custom_safety_config(self):
        """Test creation of SafetyConfig with custom values."""
        config = SafetyConfig(
            min_heating_setpoint=16.0,
            max_heating_setpoint=20.0,
            min_cooling_setpoint=24.0,
            max_cooling_setpoint=30.0,
            min_deadband=3.0,
            pmv_min=-0.7,
            pmv_max=0.7,
        )
        assert config.min_heating_setpoint == 16.0
        assert config.max_cooling_setpoint == 30.0
    
    def test_invalid_heating_range(self):
        """Test that min_heating >= max_heating raises ValueError."""
        with pytest.raises(ValueError, match="min_heating_setpoint must be less than max_heating_setpoint"):
            SafetyConfig(
                min_heating_setpoint=22.0,
                max_heating_setpoint=20.0,
            )
    
    def test_invalid_cooling_range(self):
        """Test that min_cooling >= max_cooling raises ValueError."""
        with pytest.raises(ValueError, match="min_cooling_setpoint must be less than max_cooling_setpoint"):
            SafetyConfig(
                min_cooling_setpoint=28.0,
                max_cooling_setpoint=24.0,
            )
    
    def test_overlapping_heating_cooling_ranges(self):
        """Test that max_heating > min_cooling raises ValueError."""
        with pytest.raises(ValueError, match="max_heating_setpoint must not exceed min_cooling_setpoint"):
            SafetyConfig(
                min_heating_setpoint=18.0,
                max_heating_setpoint=25.0,
                min_cooling_setpoint=22.0,
                max_cooling_setpoint=28.0,
            )
    
    def test_negative_deadband(self):
        """Test that negative deadband raises ValueError."""
        with pytest.raises(ValueError, match="min_deadband must be non-negative"):
            SafetyConfig(min_deadband=-1.0)
    
    def test_invalid_pmv_range(self):
        """Test that pmv_min >= pmv_max raises ValueError."""
        with pytest.raises(ValueError, match="pmv_min must be less than pmv_max"):
            SafetyConfig(pmv_min=0.5, pmv_max=-0.5)


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""
    
    def test_valid_llm_config(self):
        """Test creation of valid LLMConfig with defaults."""
        config = LLMConfig()
        assert config.endpoint_url == "http://localhost:11434"
        assert config.model_name == "qwen2.5:7b-instruct"
        assert config.timeout_seconds == 30.0
        assert config.max_retries == 3
        assert config.backoff_base == 2.0
        assert config.health_check_timeout == 5.0
    
    def test_custom_llm_config(self):
        """Test creation of LLMConfig with custom values."""
        config = LLMConfig(
            endpoint_url="https://colab-endpoint.com",
            model_name="custom-model",
            timeout_seconds=60.0,
            max_retries=5,
            backoff_base=3.0,
            health_check_timeout=10.0,
        )
        assert config.endpoint_url == "https://colab-endpoint.com"
        assert config.model_name == "custom-model"
        assert config.timeout_seconds == 60.0
    
    def test_invalid_timeout(self):
        """Test that non-positive timeout raises ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            LLMConfig(timeout_seconds=0)
    
    def test_invalid_max_retries(self):
        """Test that negative max_retries raises ValueError."""
        with pytest.raises(ValueError, match="max_retries must be non-negative"):
            LLMConfig(max_retries=-1)
    
    def test_invalid_backoff_base(self):
        """Test that backoff_base <= 1 raises ValueError."""
        with pytest.raises(ValueError, match="backoff_base must be greater than 1"):
            LLMConfig(backoff_base=1.0)
    
    def test_invalid_health_check_timeout(self):
        """Test that non-positive health check timeout raises ValueError."""
        with pytest.raises(ValueError, match="health_check_timeout must be positive"):
            LLMConfig(health_check_timeout=-5.0)
    
    def test_empty_endpoint_url(self):
        """Test that empty endpoint URL raises ValueError."""
        with pytest.raises(ValueError, match="endpoint_url cannot be empty"):
            LLMConfig(endpoint_url="")
    
    def test_empty_model_name(self):
        """Test that empty model name raises ValueError."""
        with pytest.raises(ValueError, match="model_name cannot be empty"):
            LLMConfig(model_name="")


class TestSystemHealthState:
    """Tests for SystemHealthState enum."""
    
    def test_health_states_exist(self):
        """Test that all expected health states are defined."""
        assert SystemHealthState.HEALTHY.value == "healthy"
        assert SystemHealthState.DEGRADED.value == "degraded"
        assert SystemHealthState.FALLBACK.value == "fallback"


class TestLLMResponse:
    """Tests for LLMResponse dataclass."""
    
    def test_successful_response(self):
        """Test creation of successful LLMResponse."""
        response = LLMResponse(
            success=True,
            decision={"zone1": {"heating": 20.0, "cooling": 24.0}},
            response_time_ms=1250.5,
        )
        assert response.success is True
        assert response.decision is not None
        assert response.error_message is None
        assert response.response_time_ms == 1250.5
    
    def test_failed_response(self):
        """Test creation of failed LLMResponse."""
        response = LLMResponse(
            success=False,
            error_message="Connection timeout",
            response_time_ms=30000.0,
        )
        assert response.success is False
        assert response.decision is None
        assert response.error_message == "Connection timeout"
    
    def test_invalid_successful_response_without_decision(self):
        """Test that successful response without decision raises ValueError."""
        with pytest.raises(ValueError, match="Successful response must include decision data"):
            LLMResponse(success=True, decision=None)
    
    def test_invalid_failed_response_without_error(self):
        """Test that failed response without error message raises ValueError."""
        with pytest.raises(ValueError, match="Failed response must include error message"):
            LLMResponse(success=False, error_message=None)


class TestFaultConfig:
    """Tests for FaultConfig dataclass."""
    
    def test_valid_fault_config(self):
        """Test creation of valid FaultConfig."""
        config = FaultConfig(
            enabled=True,
            fault_type="timeout",
            fault_rate=0.2,
            fault_duration_seconds=120.0,
        )
        assert config.enabled is True
        assert config.fault_type == "timeout"
        assert config.fault_rate == 0.2
    
    def test_invalid_fault_type(self):
        """Test that invalid fault type raises ValueError."""
        with pytest.raises(ValueError, match="fault_type must be one of"):
            FaultConfig(fault_type="invalid_type")
    
    def test_invalid_fault_rate_below_zero(self):
        """Test that fault rate below 0 raises ValueError."""
        with pytest.raises(ValueError, match="fault_rate must be between 0 and 1"):
            FaultConfig(fault_rate=-0.1)
    
    def test_invalid_fault_rate_above_one(self):
        """Test that fault rate above 1 raises ValueError."""
        with pytest.raises(ValueError, match="fault_rate must be between 0 and 1"):
            FaultConfig(fault_rate=1.5)
    
    def test_negative_fault_duration(self):
        """Test that negative fault duration raises ValueError."""
        with pytest.raises(ValueError, match="fault_duration_seconds must be non-negative"):
            FaultConfig(fault_duration_seconds=-10.0)


class TestSimulationConfig:
    """Tests for SimulationConfig dataclass."""
    
    def test_valid_simulation_config(self):
        """Test creation of valid SimulationConfig."""
        config = SimulationConfig(
            idf_path="./models/test.idf",
            epw_path="./weather/test.epw",
            decision_interval_hours=2,
        )
        assert config.idf_path == "./models/test.idf"
        assert config.epw_path == "./weather/test.epw"
        assert config.decision_interval_hours == 2
    
    def test_invalid_decision_interval(self):
        """Test that non-positive decision interval raises ValueError."""
        with pytest.raises(ValueError, match="decision_interval_hours must be positive"):
            SimulationConfig(decision_interval_hours=0)


class TestLoggingConfig:
    """Tests for LoggingConfig dataclass."""
    
    def test_valid_logging_config(self):
        """Test creation of valid LoggingConfig."""
        config = LoggingConfig(
            log_dir="./custom_logs",
            log_level="DEBUG",
            json_format=False,
        )
        assert config.log_dir == "./custom_logs"
        assert config.log_level == "DEBUG"
        assert config.json_format is False
    
    def test_invalid_log_level(self):
        """Test that invalid log level raises ValueError."""
        with pytest.raises(ValueError, match="log_level must be one of"):
            LoggingConfig(log_level="INVALID")


class TestSystemConfig:
    """Tests for SystemConfig dataclass."""
    
    def test_valid_system_config(self):
        """Test creation of valid SystemConfig."""
        config = SystemConfig(
            llm=LLMConfig(),
            safety=SafetyConfig(),
            simulation=SimulationConfig(),
            logging=LoggingConfig(),
            fault_injection=FaultConfig(),
        )
        assert config.llm is not None
        assert config.safety is not None
        assert config.simulation is not None
        assert config.logging is not None
        assert config.fault_injection is not None
    
    def test_system_config_without_fault_injection(self):
        """Test SystemConfig without fault injection (optional)."""
        config = SystemConfig(
            llm=LLMConfig(),
            safety=SafetyConfig(),
            simulation=SimulationConfig(),
            logging=LoggingConfig(),
        )
        assert config.fault_injection is None

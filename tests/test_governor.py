"""
Unit tests for SafetyGovernor class.

Tests validate:
- Setpoint validation and clamping logic
- Deadband enforcement
- PMV violation detection and logging
- Health state management and transitions
- Fallback activation and recovery
- Integration with BaselineController
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, call

from eco_loop_building_agents.governor import SafetyGovernor
from eco_loop_building_agents.models import (
    ZoneState,
    ControlDecision,
    SafetyConfig,
    SystemHealthState,
    LLMResponse
)
from eco_loop_building_agents.baseline_controller import BaselineController
from eco_loop_building_agents.structured_logger import StructuredLogger


@pytest.fixture
def safety_config():
    """Create default safety configuration."""
    return SafetyConfig(
        min_heating_setpoint=18.0,
        max_heating_setpoint=22.0,
        min_cooling_setpoint=22.0,
        max_cooling_setpoint=28.0,
        min_deadband=2.0,
        pmv_min=-0.5,
        pmv_max=0.5
    )


@pytest.fixture
def mock_logger():
    """Create mock structured logger."""
    logger = Mock(spec=StructuredLogger)
    return logger


@pytest.fixture
def baseline_controller(safety_config):
    """Create baseline controller."""
    return BaselineController(safety_config)


@pytest.fixture
def governor(safety_config, baseline_controller, mock_logger):
    """Create SafetyGovernor instance."""
    return SafetyGovernor(safety_config, baseline_controller, mock_logger)


@pytest.fixture
def sample_zone_state():
    """Create sample zone state."""
    return ZoneState(
        zone_id="Zone1",
        temperature=23.0,
        humidity=0.5,
        occupancy=2,
        pmv=0.2,
        timestamp=datetime(2024, 7, 15, 10, 0, 0)
    )


@pytest.fixture
def sample_zone_states(sample_zone_state):
    """Create dictionary of sample zone states."""
    return {"Zone1": sample_zone_state}


class TestSetpointValidation:
    """Test setpoint validation and clamping logic."""
    
    def test_validate_setpoints_within_bounds(self, governor):
        """Test that valid setpoints are not modified."""
        heating = 20.0
        cooling = 24.0
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        assert validated_heating == 20.0
        assert validated_cooling == 24.0
        assert was_modified is False
    
    def test_clamp_heating_below_minimum(self, governor):
        """Test clamping heating setpoint below minimum."""
        heating = 15.0  # Below min of 18.0
        cooling = 24.0
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        assert validated_heating == 18.0  # Clamped to min
        assert validated_cooling == 24.0
        assert was_modified is True
    
    def test_clamp_heating_above_maximum(self, governor):
        """Test clamping heating setpoint above maximum."""
        heating = 25.0  # Above max of 22.0
        cooling = 28.0
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        assert validated_heating == 22.0  # Clamped to max
        assert validated_cooling == 28.0
        assert was_modified is True
    
    def test_clamp_cooling_below_minimum(self, governor):
        """Test clamping cooling setpoint below minimum."""
        heating = 20.0
        cooling = 20.0  # Below min of 22.0
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        # Cooling clamped to 22.0, but then deadband enforcement widens
        assert validated_cooling >= 22.0
        assert validated_cooling - validated_heating >= 2.0  # Min deadband
        assert was_modified is True
    
    def test_clamp_cooling_above_maximum(self, governor):
        """Test clamping cooling setpoint above maximum."""
        heating = 20.0
        cooling = 30.0  # Above max of 28.0
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        assert validated_cooling == 28.0  # Clamped to max
        assert validated_heating == 20.0
        assert was_modified is True
    
    def test_enforce_minimum_deadband(self, governor):
        """Test deadband enforcement when setpoints too close."""
        heating = 23.0
        cooling = 24.0  # Only 1.0°C gap, below min_deadband of 2.0
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        # Should maintain minimum deadband of 2.0
        assert validated_cooling - validated_heating >= 2.0
        assert was_modified is True
        
        # After clamping heating to max (22.0), cooling should be adjusted to maintain deadband
        assert validated_heating == 22.0  # Clamped to max_heating_setpoint
        assert validated_cooling >= 24.0  # Adjusted to maintain deadband
    
    def test_deadband_enforcement_with_bounds_constraint(self, governor):
        """Test deadband enforcement when widening would exceed bounds."""
        heating = 21.5
        cooling = 22.5  # 1.0°C gap, needs widening but near bounds
        
        validated_heating, validated_cooling, was_modified = governor._validate_setpoints(
            heating, cooling
        )
        
        # Should maintain deadband while respecting bounds
        assert validated_cooling - validated_heating >= 2.0
        assert validated_heating >= 18.0
        assert validated_cooling <= 28.0
        assert was_modified is True


class TestPMVViolationDetection:
    """Test PMV violation detection and logging."""
    
    def test_no_pmv_violation(self, governor, mock_logger):
        """Test that acceptable PMV values don't trigger violations."""
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=23.0,
                humidity=0.5,
                occupancy=2,
                pmv=0.3,  # Within -0.5 to 0.5 band
                timestamp=datetime(2024, 7, 15, 10, 0, 0)
            )
        }
        
        governor._check_pmv_violations(zone_states)
        
        # Should not log any violations
        mock_logger.log_pmv_violation.assert_not_called()
    
    def test_pmv_violation_above_maximum(self, governor, mock_logger):
        """Test logging of PMV violation above maximum."""
        timestamp = datetime(2024, 7, 15, 10, 0, 0)
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=28.0,
                humidity=0.6,
                occupancy=2,
                pmv=0.8,  # Above max of 0.5
                timestamp=timestamp
            )
        }
        
        governor._check_pmv_violations(zone_states)
        
        # Should log violation
        mock_logger.log_pmv_violation.assert_called_once_with(
            zone_id="Zone1",
            pmv=0.8,
            timestamp=timestamp
        )
    
    def test_pmv_violation_below_minimum(self, governor, mock_logger):
        """Test logging of PMV violation below minimum."""
        timestamp = datetime(2024, 7, 15, 10, 0, 0)
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=18.0,
                humidity=0.4,
                occupancy=2,
                pmv=-0.8,  # Below min of -0.5
                timestamp=timestamp
            )
        }
        
        governor._check_pmv_violations(zone_states)
        
        # Should log violation
        mock_logger.log_pmv_violation.assert_called_once_with(
            zone_id="Zone1",
            pmv=-0.8,
            timestamp=timestamp
        )
    
    def test_multiple_pmv_violations(self, governor, mock_logger):
        """Test logging multiple PMV violations across zones."""
        timestamp = datetime(2024, 7, 15, 10, 0, 0)
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=18.0,
                humidity=0.4,
                occupancy=2,
                pmv=-0.7,
                timestamp=timestamp
            ),
            "Zone2": ZoneState(
                zone_id="Zone2",
                temperature=28.0,
                humidity=0.6,
                occupancy=2,
                pmv=0.9,
                timestamp=timestamp
            )
        }
        
        governor._check_pmv_violations(zone_states)
        
        # Should log both violations
        assert mock_logger.log_pmv_violation.call_count == 2


class TestHealthStateManagement:
    """Test system health state transitions."""
    
    def test_initial_state_is_healthy(self, governor):
        """Test that governor starts in HEALTHY state."""
        assert governor.get_health_state() == SystemHealthState.HEALTHY
        assert governor.get_failure_count() == 0
    
    def test_first_failure_transitions_to_degraded(self, governor):
        """Test transition to DEGRADED on first failure."""
        governor._update_health_state(llm_success=False)
        
        assert governor.get_health_state() == SystemHealthState.DEGRADED
        assert governor.get_failure_count() == 1
    
    def test_recovery_from_degraded_to_healthy(self, governor):
        """Test recovery from DEGRADED to HEALTHY on success."""
        # Move to degraded
        governor._update_health_state(llm_success=False)
        assert governor.get_health_state() == SystemHealthState.DEGRADED
        
        # Recover
        governor._update_health_state(llm_success=True)
        
        assert governor.get_health_state() == SystemHealthState.HEALTHY
        assert governor.get_failure_count() == 0
    
    def test_fallback_activation_after_three_failures(self, governor, mock_logger):
        """Test transition to FALLBACK after 3 consecutive failures."""
        # First failure -> DEGRADED
        governor._update_health_state(llm_success=False)
        assert governor.get_health_state() == SystemHealthState.DEGRADED
        
        # Second failure -> Still DEGRADED
        governor._update_health_state(llm_success=False)
        assert governor.get_health_state() == SystemHealthState.DEGRADED
        
        # Third failure -> FALLBACK
        governor._update_health_state(llm_success=False)
        
        assert governor.get_health_state() == SystemHealthState.FALLBACK
        assert governor.get_failure_count() == 3
        
        # Should log fallback activation
        mock_logger.log_fallback_activation.assert_called_once_with(
            trigger_reason="consecutive_llm_failures",
            consecutive_failures=3
        )
    
    def test_recovery_from_fallback_to_degraded(self, governor, mock_logger):
        """Test recovery from FALLBACK to DEGRADED on success."""
        # Move to fallback
        for _ in range(3):
            governor._update_health_state(llm_success=False)
        assert governor.get_health_state() == SystemHealthState.FALLBACK
        
        # Recover
        governor._update_health_state(llm_success=True)
        
        assert governor.get_health_state() == SystemHealthState.DEGRADED
        assert governor.get_failure_count() == 0
        
        # Should log fallback deactivation
        mock_logger.log_fallback_deactivation.assert_called_once()
    
    def test_remain_in_fallback_during_continued_failures(self, governor):
        """Test that system remains in FALLBACK during continued failures."""
        # Move to fallback
        for _ in range(3):
            governor._update_health_state(llm_success=False)
        
        # Additional failures should keep it in fallback
        governor._update_health_state(llm_success=False)
        governor._update_health_state(llm_success=False)
        
        assert governor.get_health_state() == SystemHealthState.FALLBACK
        assert governor.get_failure_count() == 5


class TestValidateAndApply:
    """Test the main validate_and_apply method."""
    
    def test_successful_llm_response_validation(self, governor, sample_zone_states, mock_logger):
        """Test validation of successful LLM response."""
        llm_response = LLMResponse(
            success=True,
            decision={
                "Zone1": {
                    "heating_setpoint": 20.0,
                    "cooling_setpoint": 24.0,
                    "lighting_fraction": 0.8
                }
            },
            response_time_ms=100.0
        )
        
        decisions = governor.validate_and_apply(llm_response, sample_zone_states)
        
        assert len(decisions) == 1
        assert "Zone1" in decisions
        
        decision = decisions["Zone1"]
        assert decision.heating_setpoint == 20.0
        assert decision.cooling_setpoint == 24.0
        assert decision.lighting_fraction == 0.8
        assert decision.source == "ai"
        
        # Should log decision validation
        mock_logger.log_decision_validated.assert_called_once()
    
    def test_llm_response_with_invalid_setpoints(self, governor, sample_zone_states, mock_logger):
        """Test that invalid setpoints are clamped."""
        llm_response = LLMResponse(
            success=True,
            decision={
                "Zone1": {
                    "heating_setpoint": 15.0,  # Below min of 18.0
                    "cooling_setpoint": 30.0,   # Above max of 28.0
                    "lighting_fraction": 0.8
                }
            },
            response_time_ms=100.0
        )
        
        decisions = governor.validate_and_apply(llm_response, sample_zone_states)
        
        decision = decisions["Zone1"]
        assert decision.heating_setpoint == 18.0  # Clamped
        assert decision.cooling_setpoint == 28.0  # Clamped
        
        # Should log with modified=True
        call_args = mock_logger.log_decision_validated.call_args
        assert call_args[1]["modified"] is True
    
    def test_failed_llm_response_activates_fallback(self, governor, sample_zone_states, mock_logger):
        """Test that failed LLM response activates fallback controller."""
        llm_response = LLMResponse(
            success=False,
            error_message="Connection timeout",
            response_time_ms=30000.0
        )
        
        decisions = governor.validate_and_apply(llm_response, sample_zone_states)
        
        assert len(decisions) == 1
        assert "Zone1" in decisions
        
        decision = decisions["Zone1"]
        assert decision.source == "fallback"
        
        # Verify fallback logic was used (occupied hours at 10 AM)
        assert decision.heating_setpoint == 21.0
        assert decision.cooling_setpoint == 24.0
    
    def test_missing_zone_decision_uses_fallback(self, governor, mock_logger):
        """Test that missing zone in LLM response uses fallback."""
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=23.0,
                humidity=0.5,
                occupancy=2,
                pmv=0.2,
                timestamp=datetime(2024, 7, 15, 10, 0, 0)
            ),
            "Zone2": ZoneState(
                zone_id="Zone2",
                temperature=24.0,
                humidity=0.5,
                occupancy=1,
                pmv=0.1,
                timestamp=datetime(2024, 7, 15, 10, 0, 0)
            )
        }
        
        # LLM only provides decision for Zone1
        llm_response = LLMResponse(
            success=True,
            decision={
                "Zone1": {
                    "heating_setpoint": 20.0,
                    "cooling_setpoint": 24.0,
                    "lighting_fraction": 0.8
                }
            },
            response_time_ms=100.0
        )
        
        decisions = governor.validate_and_apply(llm_response, zone_states)
        
        # Zone1 should use AI decision
        assert decisions["Zone1"].source == "ai"
        
        # Zone2 should use fallback
        assert decisions["Zone2"].source == "fallback"
    
    def test_pmv_violations_are_checked(self, governor, mock_logger):
        """Test that PMV violations are checked during validation."""
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=23.0,
                humidity=0.5,
                occupancy=2,
                pmv=0.8,  # Violation
                timestamp=datetime(2024, 7, 15, 10, 0, 0)
            )
        }
        
        llm_response = LLMResponse(
            success=True,
            decision={
                "Zone1": {
                    "heating_setpoint": 20.0,
                    "cooling_setpoint": 24.0,
                    "lighting_fraction": 0.8
                }
            },
            response_time_ms=100.0
        )
        
        governor.validate_and_apply(llm_response, zone_states)
        
        # Should log PMV violation
        mock_logger.log_pmv_violation.assert_called_once()
    
    def test_lighting_fraction_clamping(self, governor, sample_zone_states, mock_logger):
        """Test that lighting fraction is clamped to 0-1 range."""
        llm_response = LLMResponse(
            success=True,
            decision={
                "Zone1": {
                    "heating_setpoint": 20.0,
                    "cooling_setpoint": 24.0,
                    "lighting_fraction": 1.5  # Above max of 1.0
                }
            },
            response_time_ms=100.0
        )
        
        decisions = governor.validate_and_apply(llm_response, sample_zone_states)
        
        decision = decisions["Zone1"]
        assert decision.lighting_fraction == 1.0  # Clamped
        
        # Should log with modified=True
        call_args = mock_logger.log_decision_validated.call_args
        assert call_args[1]["modified"] is True


class TestFallbackIntegration:
    """Test integration with BaselineController."""
    
    def test_fallback_during_llm_failure(self, governor, sample_zone_states):
        """Test that fallback controller is used during LLM failures."""
        # Trigger fallback state
        for _ in range(3):
            governor._update_health_state(llm_success=False)
        
        llm_response = LLMResponse(
            success=False,
            error_message="Endpoint unavailable",
            response_time_ms=5000.0
        )
        
        decisions = governor.validate_and_apply(llm_response, sample_zone_states)
        
        decision = decisions["Zone1"]
        assert decision.source == "fallback"
        
        # Verify baseline controller logic (10 AM = occupied)
        assert decision.heating_setpoint == 21.0
        assert decision.cooling_setpoint == 24.0
        assert decision.lighting_fraction == 1.0
    
    def test_fallback_outside_occupied_hours(self, governor):
        """Test fallback controller outside occupied hours."""
        zone_states = {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=20.0,
                humidity=0.5,
                occupancy=0,
                pmv=0.0,
                timestamp=datetime(2024, 7, 15, 22, 0, 0)  # 10 PM = unoccupied
            )
        }
        
        # Trigger fallback
        for _ in range(3):
            governor._update_health_state(llm_success=False)
        
        llm_response = LLMResponse(
            success=False,
            error_message="Timeout",
            response_time_ms=30000.0
        )
        
        decisions = governor.validate_and_apply(llm_response, zone_states)
        
        decision = decisions["Zone1"]
        assert decision.source == "fallback"
        
        # Verify unoccupied setpoints
        assert decision.heating_setpoint == 18.0
        assert decision.cooling_setpoint == 28.0
        assert decision.lighting_fraction == 0.0


class TestResetHealthState:
    """Test manual health state reset."""
    
    def test_reset_from_degraded(self, governor):
        """Test reset from DEGRADED state."""
        governor._update_health_state(llm_success=False)
        assert governor.get_health_state() == SystemHealthState.DEGRADED
        
        governor.reset_health_state()
        
        assert governor.get_health_state() == SystemHealthState.HEALTHY
        assert governor.get_failure_count() == 0
    
    def test_reset_from_fallback(self, governor):
        """Test reset from FALLBACK state."""
        for _ in range(3):
            governor._update_health_state(llm_success=False)
        assert governor.get_health_state() == SystemHealthState.FALLBACK
        
        governor.reset_health_state()
        
        assert governor.get_health_state() == SystemHealthState.HEALTHY
        assert governor.get_failure_count() == 0

"""
Unit tests for BaselineController.

Tests the rule-based HVAC control logic including:
- Time-of-day schedule detection
- Occupied vs unoccupied setpoint logic
- Multi-zone control decision generation
"""

import pytest
from datetime import datetime

from eco_loop_building_agents.baseline_controller import BaselineController
from eco_loop_building_agents.models import ZoneState, ControlDecision, SafetyConfig


class TestBaselineController:
    """Test suite for BaselineController class."""
    
    @pytest.fixture
    def controller(self):
        """Create a baseline controller with default safety config."""
        config = SafetyConfig()
        return BaselineController(config)
    
    @pytest.fixture
    def sample_zone_states(self):
        """Create sample zone states for testing."""
        timestamp = datetime(2024, 7, 15, 10, 0, 0)  # 10 AM
        return {
            "Zone1": ZoneState(
                zone_id="Zone1",
                temperature=22.0,
                humidity=0.5,
                occupancy=5,
                pmv=0.1,
                timestamp=timestamp
            ),
            "Zone2": ZoneState(
                zone_id="Zone2",
                temperature=23.5,
                humidity=0.45,
                occupancy=3,
                pmv=-0.2,
                timestamp=timestamp
            )
        }
    
    def test_occupied_period_detection_morning(self, controller):
        """Test that 9 AM is correctly identified as occupied."""
        time = datetime(2024, 7, 15, 9, 0, 0)  # 9:00 AM
        assert controller._is_occupied_period(time) is True
    
    def test_occupied_period_detection_afternoon(self, controller):
        """Test that 2 PM is correctly identified as occupied."""
        time = datetime(2024, 7, 15, 14, 30, 0)  # 2:30 PM
        assert controller._is_occupied_period(time) is True
    
    def test_occupied_period_detection_boundary(self, controller):
        """Test that 5 PM (17:00) is the first hour of unoccupied period."""
        time = datetime(2024, 7, 15, 17, 0, 0)  # 5:00 PM
        assert controller._is_occupied_period(time) is False
    
    def test_unoccupied_period_detection_early_morning(self, controller):
        """Test that 6 AM is correctly identified as unoccupied."""
        time = datetime(2024, 7, 15, 6, 0, 0)  # 6:00 AM
        assert controller._is_occupied_period(time) is False
    
    def test_unoccupied_period_detection_evening(self, controller):
        """Test that 8 PM is correctly identified as unoccupied."""
        time = datetime(2024, 7, 15, 20, 0, 0)  # 8:00 PM
        assert controller._is_occupied_period(time) is False
    
    def test_unoccupied_period_detection_midnight(self, controller):
        """Test that midnight is correctly identified as unoccupied."""
        time = datetime(2024, 7, 15, 0, 0, 0)  # Midnight
        assert controller._is_occupied_period(time) is False
    
    def test_occupied_setpoints(self, controller, sample_zone_states):
        """Test that occupied period returns correct setpoints (21°C heating, 24°C cooling, 100% lighting)."""
        occupied_time = datetime(2024, 7, 15, 10, 0, 0)  # 10 AM
        decisions = controller.get_control_decision(sample_zone_states, occupied_time)
        
        # Check that decisions were generated for all zones
        assert len(decisions) == 2
        assert "Zone1" in decisions
        assert "Zone2" in decisions
        
        # Verify occupied setpoints
        for zone_id, decision in decisions.items():
            assert decision.heating_setpoint == 21.0
            assert decision.cooling_setpoint == 24.0
            assert decision.lighting_fraction == 1.0
            assert decision.source == "fallback"
            assert decision.timestamp == occupied_time
            assert decision.zone_id == zone_id
    
    def test_unoccupied_setpoints(self, controller, sample_zone_states):
        """Test that unoccupied period returns correct setpoints (18°C heating, 28°C cooling, 0% lighting)."""
        unoccupied_time = datetime(2024, 7, 15, 22, 0, 0)  # 10 PM
        decisions = controller.get_control_decision(sample_zone_states, unoccupied_time)
        
        # Check that decisions were generated for all zones
        assert len(decisions) == 2
        
        # Verify unoccupied setpoints
        for zone_id, decision in decisions.items():
            assert decision.heating_setpoint == 18.0
            assert decision.cooling_setpoint == 28.0
            assert decision.lighting_fraction == 0.0
            assert decision.source == "fallback"
            assert decision.timestamp == unoccupied_time
            assert decision.zone_id == zone_id
    
    def test_single_zone_control(self, controller):
        """Test control decision generation for a single zone."""
        timestamp = datetime(2024, 7, 15, 12, 0, 0)  # Noon (occupied)
        zone_states = {
            "SingleZone": ZoneState(
                zone_id="SingleZone",
                temperature=21.5,
                humidity=0.5,
                occupancy=10,
                pmv=0.0,
                timestamp=timestamp
            )
        }
        
        decisions = controller.get_control_decision(zone_states, timestamp)
        
        assert len(decisions) == 1
        assert "SingleZone" in decisions
        assert decisions["SingleZone"].heating_setpoint == 21.0
        assert decisions["SingleZone"].cooling_setpoint == 24.0
    
    def test_empty_zones_dict(self, controller):
        """Test that controller handles empty zone states gracefully."""
        timestamp = datetime(2024, 7, 15, 12, 0, 0)
        zone_states = {}
        
        decisions = controller.get_control_decision(zone_states, timestamp)
        
        assert len(decisions) == 0
        assert isinstance(decisions, dict)
    
    def test_deadband_maintained(self, controller, sample_zone_states):
        """Test that cooling setpoint is always higher than heating setpoint."""
        occupied_time = datetime(2024, 7, 15, 10, 0, 0)
        unoccupied_time = datetime(2024, 7, 15, 22, 0, 0)
        
        occupied_decisions = controller.get_control_decision(sample_zone_states, occupied_time)
        unoccupied_decisions = controller.get_control_decision(sample_zone_states, unoccupied_time)
        
        # Check occupied period deadband
        for decision in occupied_decisions.values():
            assert decision.cooling_setpoint > decision.heating_setpoint
            assert decision.cooling_setpoint - decision.heating_setpoint >= 2.0
        
        # Check unoccupied period deadband
        for decision in unoccupied_decisions.values():
            assert decision.cooling_setpoint > decision.heating_setpoint
            assert decision.cooling_setpoint - decision.heating_setpoint >= 2.0
    
    def test_consistency_across_zones(self, controller, sample_zone_states):
        """Test that all zones receive the same setpoints at the same time."""
        timestamp = datetime(2024, 7, 15, 14, 0, 0)  # 2 PM (occupied)
        decisions = controller.get_control_decision(sample_zone_states, timestamp)
        
        # Extract setpoints from all decisions
        heating_setpoints = [d.heating_setpoint for d in decisions.values()]
        cooling_setpoints = [d.cooling_setpoint for d in decisions.values()]
        lighting_fractions = [d.lighting_fraction for d in decisions.values()]
        
        # All zones should have identical setpoints
        assert len(set(heating_setpoints)) == 1
        assert len(set(cooling_setpoints)) == 1
        assert len(set(lighting_fractions)) == 1
    
    def test_boundary_transition_8am_to_9am(self, controller, sample_zone_states):
        """Test setpoint transition at 8 AM to 9 AM boundary."""
        time_8am = datetime(2024, 7, 15, 8, 59, 0)  # 8:59 AM (unoccupied)
        time_9am = datetime(2024, 7, 15, 9, 0, 0)   # 9:00 AM (occupied)
        
        decisions_8am = controller.get_control_decision(sample_zone_states, time_8am)
        decisions_9am = controller.get_control_decision(sample_zone_states, time_9am)
        
        # 8:59 AM should be unoccupied
        assert decisions_8am["Zone1"].heating_setpoint == 18.0
        assert decisions_8am["Zone1"].lighting_fraction == 0.0
        
        # 9:00 AM should be occupied
        assert decisions_9am["Zone1"].heating_setpoint == 21.0
        assert decisions_9am["Zone1"].lighting_fraction == 1.0
    
    def test_boundary_transition_4pm_to_5pm(self, controller, sample_zone_states):
        """Test setpoint transition at 4 PM to 5 PM boundary."""
        time_4pm = datetime(2024, 7, 15, 16, 59, 0)  # 4:59 PM (occupied)
        time_5pm = datetime(2024, 7, 15, 17, 0, 0)   # 5:00 PM (unoccupied)
        
        decisions_4pm = controller.get_control_decision(sample_zone_states, time_4pm)
        decisions_5pm = controller.get_control_decision(sample_zone_states, time_5pm)
        
        # 4:59 PM should be occupied
        assert decisions_4pm["Zone1"].heating_setpoint == 21.0
        assert decisions_4pm["Zone1"].lighting_fraction == 1.0
        
        # 5:00 PM should be unoccupied
        assert decisions_5pm["Zone1"].heating_setpoint == 18.0
        assert decisions_5pm["Zone1"].lighting_fraction == 0.0

"""
Baseline Controller for rule-based HVAC control.

This module implements a simple time-of-day based controller that serves two purposes:
1. Fallback control when AI system fails
2. Baseline comparison for measuring AI performance

The controller uses fixed schedules without any learning or adaptation.
"""

from datetime import datetime
from typing import Dict

from .models import ZoneState, ControlDecision, SafetyConfig


class BaselineController:
    """
    Rule-based HVAC controller using time-of-day schedules.
    
    Implements simple threshold-based control logic for fallback scenarios
    and fair comparison against AI-driven control.
    
    Control Logic:
    - Occupied Hours (9 AM - 5 PM):
      * Heating: 21°C
      * Cooling: 24°C
      * Lighting: 100%
    
    - Unoccupied Hours:
      * Heating: 18°C (setback)
      * Cooling: 28°C (setup)
      * Lighting: 0%
    """
    
    # Occupied hours schedule (9 AM to 5 PM)
    OCCUPIED_START_HOUR = 9
    OCCUPIED_END_HOUR = 17
    
    # Setpoints for occupied period
    OCCUPIED_HEATING_SETPOINT = 21.0  # °C
    OCCUPIED_COOLING_SETPOINT = 24.0  # °C
    OCCUPIED_LIGHTING_FRACTION = 1.0  # 100%
    
    # Setpoints for unoccupied period
    UNOCCUPIED_HEATING_SETPOINT = 18.0  # °C (setback)
    UNOCCUPIED_COOLING_SETPOINT = 28.0  # °C (setup)
    UNOCCUPIED_LIGHTING_FRACTION = 0.0  # 0%
    
    def __init__(self, config: SafetyConfig):
        """
        Initialize baseline controller with safety configuration.
        
        Args:
            config: Safety bounds configuration (used for validation)
        """
        self.config = config
    
    def get_control_decision(
        self,
        zone_states: Dict[str, ZoneState],
        simulation_time: datetime
    ) -> Dict[str, ControlDecision]:
        """
        Generate rule-based control decisions for all zones.
        
        Uses simple time-of-day logic to determine setpoints:
        - 9 AM to 5 PM: Occupied setpoints with full lighting
        - All other times: Unoccupied setpoints with lighting off
        
        Args:
            zone_states: Current state of all thermal zones
            simulation_time: Current simulation timestamp
            
        Returns:
            Dictionary mapping zone_id to ControlDecision objects
            All decisions have source="fallback"
        """
        # Determine if building is in occupied period
        is_occupied = self._is_occupied_period(simulation_time)
        
        # Select appropriate setpoints based on occupancy schedule
        if is_occupied:
            heating_setpoint = self.OCCUPIED_HEATING_SETPOINT
            cooling_setpoint = self.OCCUPIED_COOLING_SETPOINT
            lighting_fraction = self.OCCUPIED_LIGHTING_FRACTION
        else:
            heating_setpoint = self.UNOCCUPIED_HEATING_SETPOINT
            cooling_setpoint = self.UNOCCUPIED_COOLING_SETPOINT
            lighting_fraction = self.UNOCCUPIED_LIGHTING_FRACTION
        
        # Create control decisions for all zones
        decisions = {}
        for zone_id in zone_states.keys():
            decisions[zone_id] = ControlDecision(
                zone_id=zone_id,
                heating_setpoint=heating_setpoint,
                cooling_setpoint=cooling_setpoint,
                lighting_fraction=lighting_fraction,
                timestamp=simulation_time,
                source="fallback"
            )
        
        return decisions
    
    def _is_occupied_period(self, simulation_time: datetime) -> bool:
        """
        Determine if the current time falls within occupied hours.
        
        Occupied period is defined as 9 AM (09:00) to 5 PM (17:00).
        
        Args:
            simulation_time: Current simulation timestamp
            
        Returns:
            True if within occupied hours, False otherwise
        """
        hour = simulation_time.hour
        return self.OCCUPIED_START_HOUR <= hour < self.OCCUPIED_END_HOUR

"""
Safety Governor for validating control decisions and managing fallback control.

This module implements the SafetyGovernor class which serves as the central
fault-tolerance mechanism ensuring occupant comfort is never compromised
regardless of AI system state. It validates all control decisions against
safety bounds, enforces deadband requirements, and manages automatic fallback
to rule-based control during LLM failures.
"""

from typing import Dict, Optional, Tuple
from datetime import datetime

from .models import (
    ZoneState,
    ControlDecision,
    SafetyConfig,
    SystemHealthState,
    LLMResponse
)
from .baseline_controller import BaselineController
from .structured_logger import StructuredLogger


class SafetyGovernor:
    """
    Validates control decisions and manages fallback control.
    
    The Safety Governor acts as the central fault-tolerance mechanism in the
    Eco-Loop Building Agents system. It ensures that all control decisions
    meet safety requirements and automatically activates rule-based fallback
    control when the AI system fails.
    
    Key Responsibilities:
    - Validate heating/cooling setpoints against configurable min/max bounds
    - Ensure heating setpoint < cooling setpoint (deadband validation)
    - Clamp invalid values to nearest valid bound
    - Activate Baseline Controller when LLM fails
    - Monitor PMV values and log comfort violations
    - Track system health state (Healthy/Degraded/Fallback)
    
    Health State Transitions:
    - HEALTHY: AI control working normally
    - DEGRADED: AI experiencing issues but retrying
    - FALLBACK: Rule-based control active (3+ consecutive failures)
    
    Attributes:
        config: Safety bounds configuration
        fallback: Baseline controller for rule-based control
        logger: Structured logger for events
        health_state: Current system health state
        _failure_count: Counter for consecutive LLM failures
    """
    
    # Threshold for fallback activation
    FALLBACK_THRESHOLD = 3  # Consecutive failures before fallback
    
    def __init__(
        self,
        config: SafetyConfig,
        baseline_controller: BaselineController,
        logger: StructuredLogger
    ):
        """
        Initialize Safety Governor with configuration and dependencies.
        
        Args:
            config: Safety bounds configuration
            baseline_controller: Rule-based controller for fallback
            logger: Structured logger for events and violations
        """
        self.config = config
        self.fallback = baseline_controller
        self.logger = logger
        self.health_state = SystemHealthState.HEALTHY
        self._failure_count = 0
    
    def validate_and_apply(
        self,
        llm_response: LLMResponse,
        zone_states: Dict[str, ZoneState]
    ) -> Dict[str, ControlDecision]:
        """
        Validate LLM decision or activate fallback control.
        
        This is the main entry point for the Safety Governor. It processes
        LLM responses, validates decisions against safety bounds, and
        activates fallback control if needed.
        
        Process:
        1. Check if LLM response was successful
        2. If successful, validate and clamp setpoints
        3. If failed or invalid, activate fallback controller
        4. Monitor PMV violations
        5. Update health state
        
        Args:
            llm_response: Response from LLM client (may indicate failure)
            zone_states: Current state of all thermal zones
            
        Returns:
            Dictionary mapping zone_id to validated ControlDecision objects.
            All decisions are guaranteed to meet safety requirements.
        """
        # Update health state based on LLM success/failure
        self._update_health_state(llm_response.success)
        
        # Check PMV violations regardless of control source
        self._check_pmv_violations(zone_states)
        
        # Determine control source based on health state
        if self.health_state == SystemHealthState.FALLBACK or not llm_response.success:
            # Use fallback controller
            simulation_time = next(iter(zone_states.values())).timestamp
            decisions = self.fallback.get_control_decision(zone_states, simulation_time)
            
            # Log all fallback decisions
            for decision in decisions.values():
                self.logger.log_decision_validated(
                    decision=decision,
                    modified=False  # Fallback decisions are not modified
                )
            
            return decisions
        
        # Validate AI decisions
        decisions = {}
        for zone_id, zone_state in zone_states.items():
            # Extract LLM decision for this zone
            if llm_response.decision and zone_id in llm_response.decision:
                llm_zone_decision = llm_response.decision[zone_id]
                heating = llm_zone_decision.get("heating_setpoint")
                cooling = llm_zone_decision.get("cooling_setpoint")
                lighting = llm_zone_decision.get("lighting_fraction", 1.0)
            else:
                # Missing zone decision - use fallback
                fallback_decisions = self.fallback.get_control_decision(
                    zone_states,
                    zone_state.timestamp
                )
                decisions[zone_id] = fallback_decisions[zone_id]
                self.logger.log_decision_validated(
                    decision=fallback_decisions[zone_id],
                    modified=False
                )
                continue
            
            # Validate and clamp setpoints
            validated_heating, validated_cooling, was_modified = self._validate_setpoints(
                heating,
                cooling
            )
            
            # Clamp lighting fraction to 0-1 range
            validated_lighting = max(0.0, min(1.0, lighting))
            if validated_lighting != lighting:
                was_modified = True
            
            # Create validated decision
            decision = ControlDecision(
                zone_id=zone_id,
                heating_setpoint=validated_heating,
                cooling_setpoint=validated_cooling,
                lighting_fraction=validated_lighting,
                timestamp=zone_state.timestamp,
                source="ai"
            )
            
            decisions[zone_id] = decision
            
            # Log validation result
            self.logger.log_decision_validated(
                decision=decision,
                modified=was_modified
            )
        
        return decisions
    
    def _validate_setpoints(
        self,
        heating: float,
        cooling: float
    ) -> Tuple[float, float, bool]:
        """
        Validate and clamp setpoints to safety bounds.
        
        Implements the validation logic specified in the design document:
        1. Clamp heating setpoint to [min_heating, max_heating]
        2. Clamp cooling setpoint to [min_cooling, max_cooling]
        3. Ensure deadband: cooling - heating >= min_deadband
        
        If deadband is violated after clamping, widen by adjusting both
        setpoints around the midpoint.
        
        Args:
            heating: Requested heating setpoint in °C
            cooling: Requested cooling setpoint in °C
            
        Returns:
            Tuple of (clamped_heating, clamped_cooling, was_modified)
            was_modified is True if any value was changed
        """
        original_heating = heating
        original_cooling = cooling
        
        # Step 1: Clamp heating setpoint to bounds
        heating = max(
            self.config.min_heating_setpoint,
            min(heating, self.config.max_heating_setpoint)
        )
        
        # Step 2: Clamp cooling setpoint to bounds
        cooling = max(
            self.config.min_cooling_setpoint,
            min(cooling, self.config.max_cooling_setpoint)
        )
        
        # Step 3: Enforce minimum deadband
        if cooling - heating < self.config.min_deadband:
            # Calculate midpoint and widen deadband around it
            midpoint = (heating + cooling) / 2.0
            heating = midpoint - self.config.min_deadband / 2.0
            cooling = midpoint + self.config.min_deadband / 2.0
            
            # Re-clamp to ensure we didn't exceed bounds while widening
            heating = max(
                self.config.min_heating_setpoint,
                min(heating, self.config.max_heating_setpoint)
            )
            cooling = max(
                self.config.min_cooling_setpoint,
                min(cooling, self.config.max_cooling_setpoint)
            )
            
            # Final check: if we still can't maintain deadband after clamping,
            # prioritize the deadband by adjusting the constraint that has more room
            if cooling - heating < self.config.min_deadband:
                # Check which side has more adjustment room
                heating_room = heating - self.config.min_heating_setpoint
                cooling_room = self.config.max_cooling_setpoint - cooling
                
                if cooling_room >= heating_room:
                    # More room on cooling side
                    cooling = heating + self.config.min_deadband
                    cooling = min(cooling, self.config.max_cooling_setpoint)
                else:
                    # More room on heating side
                    heating = cooling - self.config.min_deadband
                    heating = max(heating, self.config.min_heating_setpoint)
        
        # Determine if modifications were made
        was_modified = (heating != original_heating) or (cooling != original_cooling)
        
        return heating, cooling, was_modified
    
    def _check_pmv_violations(self, zone_states: Dict[str, ZoneState]) -> None:
        """
        Monitor PMV values and log violations outside ASHRAE 55 band.
        
        ASHRAE 55 defines acceptable thermal comfort as PMV between -0.5 and +0.5.
        This method checks all zones and logs warnings for any violations.
        
        Args:
            zone_states: Current state of all thermal zones
        """
        for zone_id, zone_state in zone_states.items():
            if zone_state.pmv < self.config.pmv_min or zone_state.pmv > self.config.pmv_max:
                self.logger.log_pmv_violation(
                    zone_id=zone_id,
                    pmv=zone_state.pmv,
                    timestamp=zone_state.timestamp
                )
    
    def _update_health_state(self, llm_success: bool) -> None:
        """
        Update system health state based on LLM success/failure.
        
        Health state transitions:
        - HEALTHY -> DEGRADED: First LLM failure
        - DEGRADED -> FALLBACK: 3rd consecutive failure
        - DEGRADED -> HEALTHY: Successful LLM response
        - FALLBACK -> DEGRADED: Successful LLM response (recovery path)
        - FALLBACK -> FALLBACK: Continue in fallback during failures
        
        Args:
            llm_success: Whether the LLM request succeeded
        """
        if llm_success:
            # Successful LLM response - reset failure count
            if self.health_state == SystemHealthState.FALLBACK:
                # Recovering from fallback - transition to degraded first
                self.health_state = SystemHealthState.DEGRADED
                self.logger.log_fallback_deactivation()
            elif self.health_state == SystemHealthState.DEGRADED:
                # Recovering from degraded - return to healthy
                self.health_state = SystemHealthState.HEALTHY
            
            self._failure_count = 0
        
        else:
            # LLM failure - increment counter and potentially transition
            self._failure_count += 1
            
            if self.health_state == SystemHealthState.HEALTHY:
                # First failure - transition to degraded
                self.health_state = SystemHealthState.DEGRADED
            
            elif self.health_state == SystemHealthState.DEGRADED:
                # Check if we've hit fallback threshold
                if self._failure_count >= self.FALLBACK_THRESHOLD:
                    self.health_state = SystemHealthState.FALLBACK
                    self.logger.log_fallback_activation(
                        trigger_reason="consecutive_llm_failures",
                        consecutive_failures=self._failure_count
                    )
            
            # If already in FALLBACK, remain there until success
    
    def get_health_state(self) -> SystemHealthState:
        """
        Get current system health state.
        
        Returns:
            Current health state (HEALTHY, DEGRADED, or FALLBACK)
        """
        return self.health_state
    
    def get_failure_count(self) -> int:
        """
        Get count of consecutive LLM failures.
        
        Returns:
            Number of consecutive failures
        """
        return self._failure_count
    
    def reset_health_state(self) -> None:
        """
        Reset health state to HEALTHY and clear failure count.
        
        This method is primarily for testing and manual recovery scenarios.
        """
        self.health_state = SystemHealthState.HEALTHY
        self._failure_count = 0

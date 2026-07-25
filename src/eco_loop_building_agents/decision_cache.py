"""
Decision Cache for thread-safe, non-blocking storage of zone states and control decisions.

This module provides the DecisionCache class that enables safe concurrent access between
the EnergyPlus callback thread and the orchestration loop thread. The cache ensures that
EnergyPlus callbacks never block, which is critical for simulation stability.
"""

import threading
import time
from typing import Dict, Optional

from eco_loop_building_agents.models import ZoneState, ControlDecision


class DecisionCache:
    """
    Thread-safe cache for zone states and control decisions.
    
    The DecisionCache provides non-blocking, thread-safe storage for zone states
    (written by EnergyPlus callbacks) and control decisions (written by the
    orchestration loop). It uses threading.RLock for reentrant locking to prevent
    deadlocks while allowing the same thread to acquire the lock multiple times.
    
    Key Design Principles:
    - Non-blocking reads with timeout to prevent EnergyPlus callback blocking
    - Reentrant locks (RLock) to allow same-thread recursive locking
    - Stale decisions are retained until new ones arrive (fail-safe behavior)
    - Thread-safe writes ensure consistent state updates
    
    Attributes:
        _zone_states: Dictionary mapping zone_id to ZoneState objects
        _decisions: Dictionary mapping zone_id to ControlDecision objects
        _lock: Reentrant lock for thread-safe access
    """
    
    def __init__(self):
        """Initialize the decision cache with empty storage and a reentrant lock."""
        self._zone_states: Dict[str, ZoneState] = {}
        self._decisions: Dict[str, ControlDecision] = {}
        self._lock = threading.RLock()  # Reentrant lock for same-thread safety
    
    def write_zone_state(self, zone_state: ZoneState) -> None:
        """
        Write zone state to cache in a thread-safe manner.
        
        This method is typically called from EnergyPlus callback threads to update
        the current state of thermal zones. The write operation is protected by
        a reentrant lock to ensure consistency.
        
        Args:
            zone_state: ZoneState object containing current zone measurements
            
        Thread Safety:
            Safe to call from multiple threads concurrently. Acquires lock
            before writing and releases after completion.
        """
        with self._lock:
            self._zone_states[zone_state.zone_id] = zone_state
    
    def read_zone_states(self) -> Dict[str, ZoneState]:
        """
        Read all zone states from cache in a thread-safe manner.
        
        Returns a shallow copy of the zone states dictionary to prevent external
        modification of internal state. The copy operation is protected by the lock
        to ensure a consistent snapshot of all zones.
        
        Returns:
            Dictionary mapping zone_id to ZoneState objects. Returns empty dict
            if no zone states have been written yet.
            
        Thread Safety:
            Safe to call from multiple threads concurrently. Acquires lock
            before reading and releases after completion.
        """
        with self._lock:
            return dict(self._zone_states)  # Return shallow copy
    
    def write_decision(self, decision: ControlDecision) -> None:
        """
        Write control decision to cache in a thread-safe manner.
        
        This method is typically called from the orchestration loop (after validation
        by the Safety Governor) to store control decisions that will be applied by
        EnergyPlus callbacks. The write operation is protected by a reentrant lock.
        
        Args:
            decision: ControlDecision object containing setpoints and lighting levels
            
        Thread Safety:
            Safe to call from multiple threads concurrently. Acquires lock
            before writing and releases after completion.
        """
        with self._lock:
            self._decisions[decision.zone_id] = decision
    
    def read_decision(self, zone_id: str, timeout_ms: int = 10) -> Optional[ControlDecision]:
        """
        Read control decision with non-blocking timeout.
        
        This method implements non-blocking reads with a timeout mechanism to prevent
        EnergyPlus callbacks from blocking indefinitely. If no decision exists for
        the specified zone, the method will wait up to timeout_ms milliseconds before
        returning None.
        
        The timeout is implemented using polling with small sleep intervals to check
        if a decision becomes available. This approach ensures the EnergyPlus simulation
        thread is never blocked for extended periods.
        
        Stale Decision Behavior:
        If a decision exists (even if old), it is returned immediately without waiting.
        This fail-safe behavior ensures HVAC control continues even if new decisions
        are delayed.
        
        Args:
            zone_id: Unique identifier for the thermal zone
            timeout_ms: Maximum time to wait for a decision in milliseconds.
                       Default is 10ms to minimize callback blocking.
                       
        Returns:
            ControlDecision object if available, None if timeout expires without
            finding a decision for the specified zone.
            
        Thread Safety:
            Safe to call from multiple threads concurrently. Uses lock polling
            with timeout to prevent indefinite blocking.
            
        Performance:
            Polls every 1ms if no decision is available. Total overhead is
            minimal (max timeout_ms milliseconds) to prevent simulation delays.
        """
        start_time = time.time()
        timeout_seconds = timeout_ms / 1000.0
        
        while True:
            # Attempt to read decision with lock
            with self._lock:
                if zone_id in self._decisions:
                    return self._decisions[zone_id]
            
            # Check if timeout has expired
            elapsed = time.time() - start_time
            if elapsed >= timeout_seconds:
                return None
            
            # Small sleep to prevent busy-waiting and allow other threads to run
            # Sleep for 1ms or remaining time, whichever is smaller
            remaining = timeout_seconds - elapsed
            time.sleep(min(0.001, remaining))
    
    def read_all_decisions(self) -> Dict[str, ControlDecision]:
        """
        Read all control decisions from cache in a thread-safe manner.
        
        Returns a shallow copy of the decisions dictionary to prevent external
        modification of internal state. The copy operation is protected by the lock
        to ensure a consistent snapshot of all decisions.
        
        Returns:
            Dictionary mapping zone_id to ControlDecision objects. Returns empty
            dict if no decisions have been written yet.
            
        Thread Safety:
            Safe to call from multiple threads concurrently. Acquires lock
            before reading and releases after completion.
        """
        with self._lock:
            return dict(self._decisions)  # Return shallow copy
    
    def clear_zone_states(self) -> None:
        """
        Clear all zone states from cache (useful for testing or reset scenarios).
        
        Thread Safety:
            Safe to call from multiple threads concurrently.
        """
        with self._lock:
            self._zone_states.clear()
    
    def clear_decisions(self) -> None:
        """
        Clear all decisions from cache (useful for testing or reset scenarios).
        
        Thread Safety:
            Safe to call from multiple threads concurrently.
        """
        with self._lock:
            self._decisions.clear()
    
    def clear_all(self) -> None:
        """
        Clear both zone states and decisions from cache.
        
        Thread Safety:
            Safe to call from multiple threads concurrently.
        """
        with self._lock:
            self._zone_states.clear()
            self._decisions.clear()

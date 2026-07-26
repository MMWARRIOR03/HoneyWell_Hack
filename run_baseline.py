#!/usr/bin/env python3
"""
Baseline Runner for rule-based HVAC control simulation.

This script executes EnergyPlus simulations using the BaselineController instead
of AI-driven control. It provides a fair comparison baseline by:
- Using the same IDF and EPW files as the AI system
- Logging in the same JSON-lines format with source="baseline"
- Applying rule-based control decisions through the same infrastructure

The BaselineRunner demonstrates that the system architecture supports both
AI and rule-based control through component substitution.

Usage:
    python run_baseline.py [--config CONFIG_PATH] [--idf IDF_PATH] [--epw EPW_PATH]
    
Example:
    python run_baseline.py --config config.yaml
    python run_baseline.py --idf ./models/baseline.idf --epw ./weather/IND_New.Delhi.421820_ISHRAE.epw
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Import EnergyPlus API
try:
    from pyenergyplus.api import EnergyPlusAPI
except ImportError:
    print("Error: pyenergyplus not installed. Install with: pip install pyenergyplus")
    sys.exit(1)

# Import system components
from src.eco_loop_building_agents.decision_cache import DecisionCache
from src.eco_loop_building_agents.baseline_controller import BaselineController
from src.eco_loop_building_agents.structured_logger import StructuredLogger
from src.eco_loop_building_agents.ep_bridge import EnergyPlusBridge
from src.eco_loop_building_agents.config_manager import ConfigurationManager
from src.eco_loop_building_agents.models import SystemConfig


class BaselineRunner:
    """
    Standalone runner for baseline simulations using rule-based control.
    
    The BaselineRunner executes EnergyPlus simulations with the BaselineController
    providing all control decisions. It reuses the same infrastructure components
    (DecisionCache, EnergyPlusBridge, StructuredLogger) as the AI system but
    substitutes rule-based control for LLM decisions.
    
    Key Features:
    - Uses same IDF/EPW files as AI system for fair comparison
    - Logs in same JSON-lines format (source="baseline")
    - Executes hourly decision cycles synchronized with simulation
    - No LLM or MCP server required
    
    Architecture:
    - DecisionCache: Shared storage for zone states and control decisions
    - EnergyPlusBridge: Interface to EnergyPlus callbacks and actuators
    - BaselineController: Rule-based control logic
    - StructuredLogger: JSON-lines logging
    
    Attributes:
        config: Complete system configuration
        cache: Thread-safe cache for zone states and decisions
        controller: Rule-based controller for all control decisions
        bridge: EnergyPlus integration bridge
        logger: Structured logger for all events
        _decision_callback: EnergyPlus callback that drives simulated-time decisions
        _simulation_active: Flag to control decision loop
        _decision_cycle_count: Counter for completed decision cycles
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize baseline runner with configuration loading.
        
        Args:
            config_path: Path to config.yaml file
            
        Raises:
            ValueError: If configuration is invalid
            FileNotFoundError: If config file not found
        """
        # Load configuration
        config_manager = ConfigurationManager(config_path)
        self.config = config_manager.load()
        
        # Print configuration for visibility
        print(config_manager.pretty_print(self.config))
        
        # Initialize structured logger with baseline-specific log directory
        baseline_log_dir = str(Path(self.config.logging.log_dir) / "baseline")
        self.logger = StructuredLogger(
            log_dir=baseline_log_dir,
            log_level=self.config.logging.log_level
        )
        
        self.logger.info(
            component="baseline_runner",
            event="initialization_start",
            config_path=config_path,
            note="Baseline simulation using rule-based control"
        )
        
        # Initialize decision cache (thread-safe storage)
        self.cache = DecisionCache()
        self.logger.info(
            component="baseline_runner",
            event="component_initialized",
            component_name="DecisionCache"
        )
        
        # Initialize baseline controller
        self.controller = BaselineController(self.config.safety)
        self.logger.info(
            component="baseline_runner",
            event="component_initialized",
            component_name="BaselineController",
            control_type="rule_based_time_of_day"
        )
        
        # Initialize EnergyPlus bridge
        self.bridge = EnergyPlusBridge(
            decision_cache=self.cache,
            logger=self.logger
        )
        self.logger.info(
            component="baseline_runner",
            event="component_initialized",
            component_name="EnergyPlusBridge"
        )
        
        # Initialize state tracking
        self._simulation_active = False
        self._decision_cycle_count = 0
        self._simulation_start_time: Optional[datetime] = None
        self._last_decision_interval: Optional[tuple[int, int, int]] = None
        
        self.logger.info(
            component="baseline_runner",
            event="initialization_complete",
            all_components_ready=True
        )
    
    def run_simulation(
        self,
        idf_path: Optional[str] = None,
        epw_path: Optional[str] = None
    ) -> None:
        """
        Execute EnergyPlus simulation with rule-based control.
        
        This method:
        1. Validates IDF and EPW file paths
        2. Creates EnergyPlus API instance
        3. Registers callbacks via EnergyPlusBridge
        4. Registers the simulated-time decision callback
        5. Runs EnergyPlus simulation
        6. Waits for completion and logs results
        
        Args:
            idf_path: Path to IDF building model (overrides config if provided)
            epw_path: Path to EPW weather file (overrides config if provided)
            
        Raises:
            FileNotFoundError: If IDF or EPW file doesn't exist
            RuntimeError: If simulation fails
        """
        # Use config paths if not provided as arguments
        idf_path = idf_path or self.config.simulation.idf_path
        epw_path = epw_path or self.config.simulation.epw_path
        
        # Validate paths exist
        idf_file = Path(idf_path)
        epw_file = Path(epw_path)
        
        if not idf_file.exists():
            raise FileNotFoundError(f"IDF file not found: {idf_path}")
        if not epw_file.exists():
            raise FileNotFoundError(f"EPW file not found: {epw_path}")
        
        # Log simulation start
        self._simulation_start_time = datetime.now()
        self.logger.log_simulation_start(
            idf_path=str(idf_file.absolute()),
            epw_path=str(epw_file.absolute()),
            decision_interval_hours=self.config.simulation.decision_interval_hours
        )
        
        print(f"\n{'='*60}")
        print("Starting Baseline Simulation")
        print(f"{'='*60}")
        print(f"IDF File: {idf_file.name}")
        print(f"EPW File: {epw_file.name}")
        print(f"Log Directory: {self.logger.log_dir}")
        print(f"Log File: {self.logger.log_file_path.name}")
        print(f"{'='*60}\n")
        
        try:
            # Create EnergyPlus API instance
            api = EnergyPlusAPI()
            state = api.state_manager.new_state()
            
            # Register EnergyPlus callbacks
            self.bridge.register_callbacks(api, state)

            # Schedule decisions from EnergyPlus simulation time, not wall-clock
            # time.  A full annual simulation can finish in about a minute, so a
            # background thread that checks datetime.now() only sees one hour.
            api.runtime.callback_end_zone_timestep_after_zone_reporting(
                state,
                self._decision_callback
            )

            self._simulation_active = True
            
            self.logger.info(
                component="baseline_runner",
                event="decision_callback_registered",
                callback_type="end_zone_timestep_after_zone_reporting",
                interval_hours=self.config.simulation.decision_interval_hours
            )
            
            # Run EnergyPlus simulation (blocking call)
            print("Running EnergyPlus simulation...")
            print("(This may take several minutes depending on simulation length)\n")
            
            return_code = api.runtime.run_energyplus(
                state,
                [
                    "-w", str(epw_file.absolute()),
                    "-d", str(Path(self.logger.log_dir) / "energyplus_output"),
                    str(idf_file.absolute())
                ]
            )
            
            # Simulation completed
            self._simulation_active = False
            
            if return_code == 0:
                print("\n" + "="*60)
                print("Simulation completed successfully!")
                print("="*60)
                self.logger.info(
                    component="baseline_runner",
                    event="simulation_completed",
                    return_code=return_code
                )
            else:
                print(f"\nWarning: EnergyPlus returned code {return_code}")
                self.logger.warning(
                    component="baseline_runner",
                    event="simulation_completed_with_warnings",
                    return_code=return_code
                )
            
        except Exception as e:
            self._simulation_active = False
            self.logger.log_exception(
                component="baseline_runner",
                exception_type=type(e).__name__,
                message=str(e),
                stack_trace="",  # Would include full traceback in production
                context={"phase": "simulation_execution"}
            )
            raise RuntimeError(f"Baseline simulation failed: {str(e)}") from e
        
        finally:
            # Log simulation end
            self._log_simulation_end()
            
            # Print summary
            self._print_summary()
    
    def _decision_callback(self, state) -> None:
        """
        Execute a decision cycle at each configured EnergyPlus time interval.

        The callback runs at the end of every zone timestep, after the bridge
        has cached the zone state.  It uses EnergyPlus's simulated calendar,
        rather than wall-clock time, so fast annual runs still receive one
        decision per simulated hour.
        
        Args:
            state: EnergyPlus state object for accessing simulation time
            
        Exceptions are contained so a controller failure cannot terminate the
        EnergyPlus simulation.
        """
        try:
            if not self._simulation_active or not self.bridge.is_initialized():
                return

            exchange = self.bridge._api.exchange
            if exchange.warmup_flag(state):
                return

            interval_hours = self.config.simulation.decision_interval_hours
            calendar_year = exchange.calendar_year(state)
            simulation_time = datetime(
                calendar_year,
                exchange.month(state),
                exchange.day_of_month(state),
                exchange.hour(state),
            )
            interval_key = (
                calendar_year,
                exchange.day_of_year(state),
                exchange.hour(state) // interval_hours,
            )
            if interval_key == self._last_decision_interval:
                return

            zone_states = self.cache.read_zone_states()
            if not zone_states:
                return

            self._execute_decision_cycle(simulation_time, zone_states)
            self._last_decision_interval = interval_key

        except Exception as e:
            self.logger.log_exception(
                component="baseline_runner",
                exception_type=type(e).__name__,
                message=str(e),
                stack_trace="",
                context={"location": "decision_callback"}
            )
    
    def _execute_decision_cycle(
        self,
        simulation_time: datetime,
        zone_states: Dict
    ) -> None:
        """
        Execute one hourly decision cycle using rule-based control.
        
        This method:
        1. Logs the decision cycle start with zone states
        2. Requests control decisions from BaselineController
        3. Writes decisions to cache for EnergyPlus callbacks
        4. Logs energy metrics
        
        Args:
            simulation_time: Current simulation timestamp
            zone_states: Current state of all zones
        """
        # Increment cycle counter
        self._decision_cycle_count += 1
        
        # Log decision cycle start
        self.logger.log_decision_cycle_start(
            simulation_time=simulation_time,
            zone_states=zone_states
        )
        
        self.logger.info(
            component="baseline_runner",
            event="decision_cycle_executing",
            simulation_time=simulation_time.isoformat(),
            cycle_count=self._decision_cycle_count,
            zone_count=len(zone_states),
            control_type="rule_based"
        )
        
        try:
            # Get control decisions from baseline controller
            decisions = self.controller.get_control_decision(
                zone_states=zone_states,
                simulation_time=simulation_time
            )
            
            # Log each decision
            for zone_id, decision in decisions.items():
                self.logger.log_decision_validated(
                    decision=decision,
                    modified=False  # Baseline decisions are never modified
                )
            
            # Write decisions to cache for EnergyPlus callbacks
            for decision in decisions.values():
                self.cache.write_decision(decision)
            
            self.logger.info(
                component="baseline_runner",
                event="decision_cycle_complete",
                simulation_time=simulation_time.isoformat(),
                cycle_count=self._decision_cycle_count,
                decisions_count=len(decisions)
            )
            
            # Log energy metrics (these would be read from EnergyPlus in production)
            # For baseline, we just log placeholder metrics
            self._log_energy_metrics(simulation_time)
            
        except Exception as e:
            self.logger.log_exception(
                component="baseline_runner",
                exception_type=type(e).__name__,
                message=str(e),
                stack_trace="",
                context={
                    "simulation_time": simulation_time.isoformat(),
                    "cycle_count": self._decision_cycle_count
                }
            )
    
    def _log_energy_metrics(self, simulation_time: datetime) -> None:
        """
        Log energy metrics placeholder.
        
        In production, this would read actual meter values from EnergyPlus.
        For now, we log placeholder values to maintain log format consistency.
        
        Args:
            simulation_time: Current simulation timestamp
        """
        # Placeholder metrics - in production, read from EnergyPlus meters
        self.logger.log_energy_metrics(
            simulation_time=simulation_time,
            hvac_energy_kwh=0.0,
            lighting_energy_kwh=0.0,
            total_energy_kwh=0.0
        )
    
    def _log_simulation_end(self) -> None:
        """Log simulation completion statistics."""
        if self._simulation_start_time:
            total_duration = (datetime.now() - self._simulation_start_time).total_seconds()
        else:
            total_duration = 0.0
        
        self.logger.log_simulation_end(
            total_duration_seconds=total_duration,
            decision_cycles_completed=self._decision_cycle_count
        )
    
    def _print_summary(self) -> None:
        """Print simulation summary to console."""
        if self._simulation_start_time:
            duration = (datetime.now() - self._simulation_start_time).total_seconds()
            duration_str = f"{duration:.1f}s"
        else:
            duration_str = "unknown"
        
        print("\n" + "="*60)
        print("Baseline Simulation Summary")
        print("="*60)
        print(f"Decision Cycles: {self._decision_cycle_count}")
        print(f"Duration: {duration_str}")
        print(f"Log File: {self.logger.log_file_path}")
        print("="*60 + "\n")
    
    def shutdown(self) -> None:
        """
        Gracefully shutdown baseline runner and close resources.
        
        Ensures all components are properly closed and logs are flushed.
        """
        self.logger.info(
            component="baseline_runner",
            event="shutdown_start"
        )
        
        try:
            # Stop decision loop
            self._simulation_active = False
            
            # Log final statistics
            self._log_simulation_end()
            
            # Close logger (flushes all pending writes)
            self.logger.close()
            
        except Exception as e:
            print(f"Error during shutdown: {e}")
            try:
                self.logger.close()
            except:
                pass
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures proper shutdown."""
        self.shutdown()
        return False


def main():
    """Main entry point for baseline runner script."""
    parser = argparse.ArgumentParser(
        description="Run EnergyPlus simulation with rule-based baseline control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default config.yaml
  python run_baseline.py
  
  # Run with custom config file
  python run_baseline.py --config my_config.yaml
  
  # Override IDF and EPW files
  python run_baseline.py --idf ./models/office.idf --epw ./weather/chicago.epw
  
  # Use environment variables
  IDF_PATH=./models/office.idf EPW_PATH=./weather/chicago.epw python run_baseline.py
        """
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)"
    )
    
    parser.add_argument(
        "--idf",
        type=str,
        help="Path to IDF building model file (overrides config)"
    )
    
    parser.add_argument(
        "--epw",
        type=str,
        help="Path to EPW weather file (overrides config)"
    )
    
    args = parser.parse_args()
    
    try:
        # Create and run baseline runner
        with BaselineRunner(config_path=args.config) as runner:
            runner.run_simulation(
                idf_path=args.idf,
                epw_path=args.epw
            )
            
        print("\nBaseline simulation completed successfully!")
        return 0
        
    except KeyboardInterrupt:
        print("\n\nSimulation interrupted by user")
        return 1
        
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

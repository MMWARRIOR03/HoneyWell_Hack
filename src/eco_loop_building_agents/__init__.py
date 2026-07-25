"""
Eco-Loop Building Agents: AI-driven building energy optimization system.

This package provides a real-time closed-loop control system between EnergyPlus
building energy simulation and an open-source LLM for autonomous HVAC optimization.
"""

__version__ = "0.1.0"
__author__ = "SynapseEnergy Team"

from .models import (
    ZoneState,
    ControlDecision,
    SafetyConfig,
    LLMConfig,
    SystemHealthState,
    LLMResponse,
    FaultConfig
)
from .structured_logger import StructuredLogger
from .governor import SafetyGovernor
from .baseline_controller import BaselineController
from .mcp_server import BuildingControlMCPServer
from .orchestration_loop import OrchestrationLoop
from .dashboard import ComparisonDashboard, SimulationResults
from .fault_injection import FaultInjector, FaultInjectionWrapper, with_fault_injection

__all__ = [
    "ZoneState",
    "ControlDecision",
    "SafetyConfig",
    "LLMConfig",
    "SystemHealthState",
    "LLMResponse",
    "FaultConfig",
    "StructuredLogger",
    "SafetyGovernor",
    "BaselineController",
    "BuildingControlMCPServer",
    "OrchestrationLoop",
    "ComparisonDashboard",
    "SimulationResults",
    "FaultInjector",
    "FaultInjectionWrapper",
    "with_fault_injection",
]

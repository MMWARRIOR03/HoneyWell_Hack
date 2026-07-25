"""
Eco-Loop Building Agents: AI-driven building energy optimization system.

This package provides a real-time closed-loop control system between EnergyPlus
building energy simulation and an open-source LLM for autonomous HVAC optimization.
"""

__version__ = "0.1.0"
__author__ = "SynapseEnergy Team"

from .models import ZoneState, ControlDecision, SafetyConfig, LLMConfig

__all__ = [
    "ZoneState",
    "ControlDecision",
    "SafetyConfig",
    "LLMConfig",
]

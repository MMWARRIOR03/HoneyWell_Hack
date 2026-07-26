"""
Comparison Dashboard for visualizing baseline vs AI performance.

This module provides the ComparisonDashboard class for generating visual
comparisons and quantitative metrics between baseline rule-based control
and AI-driven control for building energy optimization.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import csv

import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for headless rendering
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure


@dataclass
class SimulationResults:
    """
    Parsed results from simulation log files.
    
    Attributes:
        timestamps: List of simulation timestamps
        energy_hvac: HVAC energy consumption timeseries (kWh)
        energy_lighting: Lighting energy consumption timeseries (kWh)
        energy_total: Total energy consumption timeseries (kWh)
        pmv_values: PMV timeseries per zone (zone_id -> list of PMV values)
        pmv_timestamps: PMV measurement timestamps per zone
        fallback_events: Timestamps when fallback control was activated
        source: Source of results ("baseline" or "ai")
    """
    timestamps: List[datetime]
    energy_hvac: List[float]
    energy_lighting: List[float]
    energy_total: List[float]
    pmv_values: Dict[str, List[float]]
    pmv_timestamps: Dict[str, List[datetime]]
    fallback_events: List[datetime]
    source: str
    
    def __post_init__(self):
        """Validate simulation results data consistency."""
        if len(self.timestamps) != len(self.energy_total):
            raise ValueError("Timestamps and energy_total must have same length")
        if len(self.energy_hvac) != len(self.energy_lighting):
            raise ValueError("energy_hvac and energy_lighting must have same length")


class ComparisonDashboard:
    """
    Generate visualizations comparing baseline and AI runs.
    
    The ComparisonDashboard parses JSON-lines log files from both baseline
    and AI-driven simulation runs, then generates matplotlib charts and
    summary statistics to demonstrate energy savings and comfort maintenance.
    
    Key Features:
    - Parse JSON-lines logs with structured event data
    - Generate cumulative energy consumption charts with carbon intensity overlay
    - Generate PMV scatter plots with ASHRAE 55 comfort band highlighting
    - Calculate summary statistics (total energy, avg PMV, violations, % savings)
    - Export charts as 300 DPI PNG files for presentation quality
    - Export summary tables as CSV with UTF-8 encoding
    
    Attributes:
        output_dir: Directory path for chart and table exports
    """
    
    def __init__(self, output_dir: str):
        """
        Initialize the comparison dashboard.
        
        Args:
            output_dir: Directory path for output files (charts and tables)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def parse_logs(self, log_path: str) -> SimulationResults:
        """
        Parse JSON-lines log file into structured results.
        
        Reads a JSON-lines format log file and extracts:
        - Energy metrics (HVAC, lighting, total)
        - PMV values per zone
        - Fallback activation events
        - Simulation timestamps
        
        Args:
            log_path: Path to JSON-lines log file
            
        Returns:
            SimulationResults object with parsed data
            
        Raises:
            FileNotFoundError: If log file doesn't exist
            ValueError: If log file has invalid format
        """
        log_file = Path(log_path)
        if not log_file.exists():
            raise FileNotFoundError(f"Log file not found: {log_path}")
        
        timestamps = []
        energy_hvac = []
        energy_lighting = []
        energy_total = []
        simulation_year: Optional[int] = None
        pmv_values: Dict[str, List[float]] = {}
        pmv_timestamps: Dict[str, List[datetime]] = {}
        fallback_events = []
        
        # Determine source from file path or content
        source = "baseline" if "baseline" in str(log_path) else "ai"
        
        with open(log_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping invalid JSON at line {line_num}: {e}")
                    continue
                
                event = entry.get("event")
                
                # Parse energy metrics
                if event == "energy_metrics":
                    sim_time_str = entry.get("simulation_time")
                    if sim_time_str:
                        simulation_time = datetime.fromisoformat(
                            sim_time_str.replace('Z', '')
                        )
                        # EnergyPlus can emit one reporting record after a
                        # one-year run rolls into the following calendar year.
                        # Keep the first run-period year for a coherent chart.
                        if simulation_year is None:
                            simulation_year = simulation_time.year
                        elif simulation_time.year != simulation_year:
                            continue
                        hvac_kwh = float(entry.get("hvac_energy_kwh", 0.0))
                        lighting_kwh = float(entry.get("lighting_energy_kwh", 0.0))
                        reported_total_kwh = float(entry.get("total_energy_kwh", 0.0))
                        # Historical logs wrote a placeholder zero for total
                        # energy even when component meters were populated.
                        # Reconstruct it so comparisons remain meaningful.
                        total_kwh = (
                            reported_total_kwh
                            if reported_total_kwh > 0.0
                            else hvac_kwh + lighting_kwh
                        )
                        timestamps.append(simulation_time)
                        energy_hvac.append(hvac_kwh)
                        energy_lighting.append(lighting_kwh)
                        energy_total.append(total_kwh)
                
                # Parse PMV violations and zone states
                elif event == "pmv_violation":
                    zone = entry.get("zone", "Unknown")
                    pmv = entry.get("pmv")
                    violation_time_str = entry.get("violation_time")
                    
                    if pmv is not None and violation_time_str:
                        violation_time = datetime.fromisoformat(
                            violation_time_str.replace('Z', '')
                        )
                        if simulation_year is None:
                            simulation_year = violation_time.year
                        elif violation_time.year != simulation_year:
                            continue
                        if zone not in pmv_values:
                            pmv_values[zone] = []
                            pmv_timestamps[zone] = []
                        
                        pmv_values[zone].append(pmv)
                        pmv_timestamps[zone].append(violation_time)
                
                # Parse zone states from decision cycles for additional PMV data
                elif event == "decision_cycle_start":
                    zone_states = entry.get("zone_states", {})
                    sim_time_str = entry.get("simulation_time")
                    
                    if sim_time_str:
                        sim_time = datetime.fromisoformat(sim_time_str.replace('Z', ''))
                        if simulation_year is None:
                            simulation_year = sim_time.year
                        elif sim_time.year != simulation_year:
                            continue
                        
                        for zone_id, state in zone_states.items():
                            pmv = state.get("pmv")
                            
                            if pmv is not None:
                                if zone_id not in pmv_values:
                                    pmv_values[zone_id] = []
                                    pmv_timestamps[zone_id] = []
                                
                                pmv_values[zone_id].append(pmv)
                                pmv_timestamps[zone_id].append(sim_time)
                
                # Parse fallback activation events
                elif event == "fallback_activated":
                    timestamp_str = entry.get("timestamp")
                    if timestamp_str:
                        fallback_events.append(
                            datetime.fromisoformat(timestamp_str.replace('Z', ''))
                        )
        
        return SimulationResults(
            timestamps=timestamps,
            energy_hvac=energy_hvac,
            energy_lighting=energy_lighting,
            energy_total=energy_total,
            pmv_values=pmv_values,
            pmv_timestamps=pmv_timestamps,
            fallback_events=fallback_events,
            source=source
        )
    
    def _get_grid_carbon_intensity(self, timestamp: datetime) -> float:
        """
        Get grid carbon intensity for a given timestamp.
        
        Simplified model based on time of day:
        - Peak hours (12:00-18:00): Higher carbon intensity
        - Off-peak hours: Lower carbon intensity
        
        Args:
            timestamp: Simulation timestamp
            
        Returns:
            Carbon intensity in gCO2/kWh
        """
        hour = timestamp.hour
        
        # Peak hours (12:00-18:00): 600 gCO2/kWh
        # Off-peak hours: 400 gCO2/kWh
        if 12 <= hour < 18:
            return 600.0
        else:
            return 400.0
    
    def generate_energy_chart(
        self,
        baseline: SimulationResults,
        ai: SimulationResults,
        output_path: str
    ) -> None:
        """
        Generate cumulative energy consumption chart with carbon intensity overlay.
        
        Creates a line chart comparing baseline and AI energy consumption over time,
        with grid carbon intensity displayed as a filled area chart overlay.
        
        Args:
            baseline: Parsed baseline simulation results
            ai: Parsed AI simulation results
            output_path: Path for output PNG file
        """
        fig, ax1 = plt.subplots(figsize=(12, 6))
        
        # Plot energy consumption on primary y-axis
        if baseline.timestamps and baseline.energy_total:
            ax1.plot(
                baseline.timestamps,
                baseline.energy_total,
                label='Baseline (Rule-Based)',
                color='#e74c3c',
                linewidth=2,
                marker='o',
                markersize=4
            )
        
        if ai.timestamps and ai.energy_total:
            ax1.plot(
                ai.timestamps,
                ai.energy_total,
                label='AI-Driven Control',
                color='#2ecc71',
                linewidth=2,
                marker='s',
                markersize=4
            )
        
        ax1.set_xlabel('Simulation Time', fontsize=12, fontweight='bold')
        ax1.set_ylabel('Cumulative Energy (kWh)', fontsize=12, fontweight='bold')
        ax1.tick_params(axis='y')
        ax1.grid(True, alpha=0.3, linestyle='--')
        ax1.legend(loc='upper left', fontsize=10)
        
        # Create secondary y-axis for carbon intensity overlay
        ax2 = ax1.twinx()
        
        # Use baseline or AI timestamps for carbon intensity (whichever has data)
        carbon_timestamps = baseline.timestamps if baseline.timestamps else ai.timestamps
        
        if carbon_timestamps:
            carbon_intensities = [
                self._get_grid_carbon_intensity(ts) for ts in carbon_timestamps
            ]
            
            ax2.fill_between(
                carbon_timestamps,
                carbon_intensities,
                alpha=0.15,
                color='#95a5a6',
                label='Grid Carbon Intensity'
            )
            ax2.set_ylabel('Carbon Intensity (gCO2/kWh)', fontsize=12, fontweight='bold')
            ax2.tick_params(axis='y')
            ax2.legend(loc='upper right', fontsize=10)
        
        # Format x-axis for datetime
        if carbon_timestamps:
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            # Use AutoDateLocator for better automatic tick placement
            locator = mdates.AutoDateLocator(maxticks=12)
            ax1.xaxis.set_major_locator(locator)
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')
        
        plt.title('Cumulative Energy Consumption: Baseline vs AI', fontsize=14, fontweight='bold', pad=20)
        plt.tight_layout()
        
        # Export as 300 DPI PNG
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        print(f"Energy chart saved to: {output_path}")
    
    def generate_comfort_chart(
        self,
        baseline: SimulationResults,
        ai: SimulationResults,
        output_path: str
    ) -> None:
        """
        Generate a daily aggregate PMV chart with ASHRAE 55 comfort bands.

        All zone readings are averaged within each simulation day.  This
        keeps the comparison readable and avoids a legend entry for every
        zone in each scenario.
        
        Args:
            baseline: Parsed baseline simulation results
            ai: Parsed AI simulation results
            output_path: Path for output PNG file
        """
        fig, ax = plt.subplots(figsize=(12, 6))
        baseline_times, baseline_means = self._aggregate_daily_pmv(baseline)
        ai_times, ai_means = self._aggregate_daily_pmv(ai)
        all_timestamps = baseline_times + ai_times

        ax.axhspan(
            -0.5,
            0.5,
            alpha=0.2,
            color='#2ecc71',
            label='ASHRAE 55 Comfort Band',
        )
        if baseline_times:
            ax.plot(
                baseline_times,
                baseline_means,
                color='#e74c3c',
                linewidth=2.0,
                label='Baseline Daily Mean PMV',
            )
        if ai_times:
            ax.plot(
                ai_times,
                ai_means,
                color='#2ecc71',
                linewidth=2.0,
                linestyle='--',
                label='AI Daily Mean PMV',
            )
        
        ax.set_xlabel('Simulation Time', fontsize=12, fontweight='bold')
        ax.set_ylabel('PMV (Predicted Mean Vote)', fontsize=12, fontweight='bold')
        ax.set_ylim(-1.5, 1.5)
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(loc='upper right', fontsize=10)
        
        # Format x-axis for datetime
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        if all_timestamps:
            # Use AutoDateLocator for better automatic tick placement
            locator = mdates.AutoDateLocator(maxticks=12)
            ax.xaxis.set_major_locator(locator)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
        
        plt.title('Daily Mean Thermal Comfort (PMV): Baseline vs AI', fontsize=14, fontweight='bold', pad=20)
        plt.tight_layout()
        
        # Export as 300 DPI PNG
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        print(f"Comfort chart saved to: {output_path}")

    def _aggregate_daily_pmv(
        self,
        results: SimulationResults,
    ) -> Tuple[List[datetime], List[float]]:
        """Return mean PMV across all available zones for each simulation day."""
        values_by_day: Dict[datetime, List[float]] = {}
        for zone_id, pmv_values in results.pmv_values.items():
            timestamps = results.pmv_timestamps.get(zone_id, [])
            for timestamp, pmv in zip(timestamps, pmv_values):
                day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
                values_by_day.setdefault(day, []).append(pmv)

        timestamps = sorted(values_by_day)
        return (
            timestamps,
            [sum(values_by_day[timestamp]) / len(values_by_day[timestamp]) for timestamp in timestamps],
        )
    
    def _calculate_pmv_violations(
        self,
        results: SimulationResults,
        pmv_min: float = -0.5,
        pmv_max: float = 0.5
    ) -> int:
        """
        Calculate total number of PMV violations outside comfort band.
        
        Args:
            results: Simulation results to analyze
            pmv_min: Minimum acceptable PMV
            pmv_max: Maximum acceptable PMV
            
        Returns:
            Count of PMV violations
        """
        violations = 0
        
        for zone_id, pmv_list in results.pmv_values.items():
            for pmv in pmv_list:
                if pmv < pmv_min or pmv > pmv_max:
                    violations += 1
        
        return violations
    
    def _calculate_average_pmv(self, results: SimulationResults) -> Optional[float]:
        """
        Calculate average PMV across all zones and timesteps.
        
        Args:
            results: Simulation results to analyze
            
        Returns:
            Average PMV value, or None if no data
        """
        all_pmv = []
        
        for zone_id, pmv_list in results.pmv_values.items():
            all_pmv.extend(pmv_list)
        
        if not all_pmv:
            return None
        
        return sum(all_pmv) / len(all_pmv)
    
    def generate_summary_table(
        self,
        baseline: SimulationResults,
        ai: SimulationResults,
        output_path: str
    ) -> None:
        """
        Generate CSV summary table with key performance metrics.
        
        Creates a summary table comparing:
        - Total energy consumption (baseline, AI, % savings)
        - Average PMV (baseline, AI)
        - PMV violations count (baseline, AI)
        - Fallback activation count (AI only)
        
        Args:
            baseline: Parsed baseline simulation results
            ai: Parsed AI simulation results
            output_path: Path for output CSV file
        """
        # Calculate total energy
        baseline_total_energy = baseline.energy_total[-1] if baseline.energy_total else 0.0
        ai_total_energy = ai.energy_total[-1] if ai.energy_total else 0.0
        
        # Calculate energy savings
        energy_savings = baseline_total_energy - ai_total_energy
        percent_savings = (energy_savings / baseline_total_energy * 100) if baseline_total_energy > 0 else 0.0
        
        # Calculate average PMV
        baseline_avg_pmv = self._calculate_average_pmv(baseline)
        ai_avg_pmv = self._calculate_average_pmv(ai)
        
        # Calculate PMV violations
        baseline_violations = self._calculate_pmv_violations(baseline)
        ai_violations = self._calculate_pmv_violations(ai)
        
        # Count fallback events
        fallback_count = len(ai.fallback_events)
        
        # Prepare summary data
        summary_rows = [
            ["Metric", "Baseline (Rule-Based)", "AI-Driven Control", "Difference"],
            ["Total Energy (kWh)", f"{baseline_total_energy:.2f}", f"{ai_total_energy:.2f}", f"{energy_savings:.2f}"],
            ["Energy Savings (%)", "-", f"{percent_savings:.2f}%", "-"],
            ["Average PMV", 
             f"{baseline_avg_pmv:.3f}" if baseline_avg_pmv is not None else "N/A",
             f"{ai_avg_pmv:.3f}" if ai_avg_pmv is not None else "N/A",
             f"{(ai_avg_pmv - baseline_avg_pmv):.3f}" if (baseline_avg_pmv and ai_avg_pmv) else "N/A"],
            ["PMV Violations (count)", str(baseline_violations), str(ai_violations), str(ai_violations - baseline_violations)],
            ["Fallback Activations", "N/A", str(fallback_count), "-"],
        ]
        
        # Write CSV file with UTF-8 encoding
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(summary_rows)
        
        print(f"Summary table saved to: {output_path}")
        
        # Also print summary to console
        print("\n" + "="*70)
        print("SIMULATION PERFORMANCE SUMMARY")
        print("="*70)
        for row in summary_rows:
            if row[0] == "Metric":
                print(f"\n{row[0]:<30} {row[1]:<20} {row[2]:<20} {row[3]:<15}")
                print("-"*70)
            else:
                print(f"{row[0]:<30} {row[1]:<20} {row[2]:<20} {row[3]:<15}")
        print("="*70 + "\n")
    
    def generate_all_comparisons(
        self,
        baseline_log_path: str,
        ai_log_path: str,
        output_prefix: str = "comparison"
    ) -> None:
        """
        Generate all comparison visualizations and tables.
        
        Convenience method that parses both log files and generates:
        - Energy consumption chart
        - PMV comfort chart
        - Summary statistics table
        
        Args:
            baseline_log_path: Path to baseline simulation log
            ai_log_path: Path to AI simulation log
            output_prefix: Prefix for output filenames (default: "comparison")
        """
        print("Parsing simulation logs...")
        baseline = self.parse_logs(baseline_log_path)
        ai = self.parse_logs(ai_log_path)
        
        print(f"Baseline: {len(baseline.timestamps)} energy datapoints, "
              f"{sum(len(pmv) for pmv in baseline.pmv_values.values())} PMV readings")
        print(f"AI: {len(ai.timestamps)} energy datapoints, "
              f"{sum(len(pmv) for pmv in ai.pmv_values.values())} PMV readings")
        
        # Generate energy chart
        energy_chart_path = self.output_dir / f"{output_prefix}_energy.png"
        self.generate_energy_chart(baseline, ai, str(energy_chart_path))
        
        # Generate comfort chart
        comfort_chart_path = self.output_dir / f"{output_prefix}_pmv.png"
        self.generate_comfort_chart(baseline, ai, str(comfort_chart_path))
        
        # Generate summary table
        summary_table_path = self.output_dir / f"{output_prefix}_summary.csv"
        self.generate_summary_table(baseline, ai, str(summary_table_path))
        
        print(f"\nAll comparisons generated successfully in: {self.output_dir}")

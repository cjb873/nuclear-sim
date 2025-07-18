"""
Abstract Base Classes for Lubrication Systems

This module provides abstract base classes for all lubrication systems in the
nuclear plant simulation, enabling code reuse and consistency across different
rotating machinery systems.

Key Features:
1. Common oil quality tracking and degradation
2. Standardized component wear calculations
3. Unified maintenance scheduling and procedures
4. Consistent performance degradation models
5. Extensible architecture for new lubricated systems

Physical Basis:
- Oil degradation kinetics (oxidation, contamination, thermal breakdown)
- Tribological wear mechanisms (adhesive, abrasive, fatigue)
- Heat transfer in lubrication systems
- Fluid mechanics of oil circulation
- Materials science of lubricant additives
"""

import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

warnings.filterwarnings("ignore")


@dataclass
class BaseLubricationConfig:
    """
    Base configuration for all lubrication systems
    
    References:
    - ASTM D4378: Standard Practice for In-Service Monitoring of Mineral Turbine Oils
    - ISO 4406: Hydraulic fluid power - Fluids - Method for coding level of contamination
    - ASTM D974: Standard Test Method for Acid and Base Number by Color-Indicator Titration
    - Machinery Lubrication Magazine - Oil Analysis Best Practices
    """
    
    # System identification
    system_id: str = "LUB-001"                          # Lubrication system identifier
    system_type: str = "turbine"                        # "turbine", "pump", "governor", etc.
    
    # Oil system parameters
    oil_reservoir_capacity: float = 500.0               # liters total capacity
    oil_operating_pressure: float = 0.35                # MPa operating pressure
    oil_temperature_range: Tuple[float, float] = (40.0, 80.0)  # °C min/max operating temps
    oil_viscosity_grade: str = "ISO VG 32"              # Oil viscosity grade
    oil_density: float = 850.0                          # kg/m³ oil density
    
    # Oil quality parameters and limits - ALIGNED with maintenance thresholds
    contamination_limit: float = 15.0                   # ppm particles >10 microns (matches maintenance threshold)
    moisture_limit: float = 0.08                        # % water content limit (matches maintenance threshold)
    acidity_limit: float = 1.6                          # mg KOH/g acid number limit (matches maintenance threshold)
    viscosity_change_limit: float = 10.0                # % viscosity change limit
    
    # Filtration system
    filter_micron_rating: float = 10.0                  # microns filter rating
    filter_capacity: float = 100.0                      # L/min filter flow capacity
    filter_change_interval: float = 2000.0              # hours filter change interval
    
    # Maintenance parameters
    oil_change_interval: float = 8760.0                 # hours (1 year typical)
    oil_analysis_interval: float = 720.0                # hours (monthly analysis)
    condition_monitoring_enabled: bool = True           # Enable condition monitoring
    
    # Environmental factors
    ambient_temperature_effect: float = 0.02            # Effect per °C ambient temp change
    humidity_effect: float = 0.001                      # Effect per % humidity change
    dust_environment_factor: float = 1.0                # Dust contamination multiplier


@dataclass
class LubricationComponent:
    """
    Configuration for individual lubricated component
    
    This represents a single component that requires lubrication,
    such as a bearing, seal, gear, or actuator.
    """
    
    component_id: str = "COMP-001"                      # Component identifier
    component_type: str = "bearing"                     # "bearing", "seal", "gear", "actuator"
    
    # Lubrication requirements
    oil_flow_requirement: float = 5.0                  # L/min required oil flow
    oil_pressure_requirement: float = 0.2              # MPa required oil pressure
    oil_temperature_max: float = 80.0                  # °C maximum oil temperature
    
    # Wear characteristics
    base_wear_rate: float = 0.001                      # %/hour base wear rate
    load_wear_exponent: float = 1.5                    # Load effect on wear (wear ∝ load^exp)
    speed_wear_exponent: float = 1.2                   # Speed effect on wear
    contamination_wear_factor: float = 2.0             # Contamination multiplier
    
    # Performance degradation
    wear_performance_factor: float = 0.01              # Performance loss per % wear
    lubrication_performance_factor: float = 0.5        # Performance loss with poor lubrication
    
    # Maintenance thresholds
    wear_alarm_threshold: float = 10.0                 # % wear for alarm
    wear_trip_threshold: float = 25.0                  # % wear for trip/shutdown
    performance_degradation_limit: float = 15.0        # % performance loss limit


class BaseLubricationSystem(ABC):
    """
    Abstract base class for all lubrication systems
    
    This class provides common functionality for oil quality tracking,
    component wear calculation, and maintenance scheduling that can be
    inherited by specific lubrication systems.
    
    Physical Models Implemented:
    1. Oil degradation kinetics (Arrhenius equation for thermal effects)
    2. Contamination accumulation and filtration
    3. Tribological wear mechanisms
    4. Heat generation and transfer in lubrication systems
    5. Additive depletion and oil aging
    """
    
    def __init__(self, config: BaseLubricationConfig, components: List[LubricationComponent]):
        """Initialize base lubrication system"""
        self.config = config
        self.components = {comp.component_id: comp for comp in components}
        
        # Oil system state
        self.oil_level = 90.0                                # % oil level (0-100%)
        self.oil_temperature = 55.0                           # °C current temperature
        self.oil_pressure = config.oil_operating_pressure     # MPa current pressure
        self.oil_flow_rate = 50.0                            # L/min circulation rate
        
        # Oil quality state
        self.oil_contamination_level = 5.0                   # ppm particles >10 microns
        self.oil_moisture_content = 0.02                     # % water content
        self.oil_acidity_number = 0.15                       # mg KOH/g acid number
        self.oil_viscosity_index = 95.0                      # Viscosity index (0-100)
        self.oil_viscosity_change = 0.0                      # % change from new oil viscosity
        self.oil_viscosity_at_40c = 46.0                     # cSt viscosity at 40°C (ISO VG 46 typical)
        self.oil_viscosity_at_100c = 6.8                     # cSt viscosity at 100°C
        self.oil_operating_hours = 0.0                       # Hours since oil change
        
        # Additive package state
        self.antioxidant_level = 100.0                       # % remaining antioxidants
        self.anti_wear_additive_level = 100.0                # % remaining AW additives
        self.corrosion_inhibitor_level = 100.0               # % remaining CI additives
        
        # Component wear state
        self.component_wear = {comp_id: 0.0 for comp_id in self.components.keys()}
        self.component_performance_factors = {comp_id: 1.0 for comp_id in self.components.keys()}
        
        # System health metrics
        self.lubrication_effectiveness = 1.0                 # Overall lubrication quality factor
        self.system_health_factor = 1.0                      # Overall system health
        self.maintenance_due = False                          # Maintenance required flag
        self.oil_analysis_due = False                         # Oil analysis due flag
        
        # Performance tracking
        self.operating_hours = 0.0                           # Total system operating hours
        self.oil_changes_performed = 0                       # Number of oil changes
        self.filter_changes_performed = 0                    # Number of filter changes
        
        # Alarm and trip states
        self.active_alarms = []                              # List of active alarms
        self.trip_conditions = []                            # List of trip conditions
    
    @abstractmethod
    def get_lubricated_components(self) -> List[str]:
        """Return list of components that require lubrication"""
        pass
    
    @abstractmethod
    def calculate_component_wear(self, component_id: str, operating_conditions: Dict) -> float:
        """Calculate wear rate for specific component based on operating conditions"""
        pass
    
    @abstractmethod
    def get_component_lubrication_requirements(self, component_id: str) -> Dict[str, float]:
        """Get lubrication requirements for specific component"""
        pass
    
    def update_oil_quality(self, 
                          operating_temperature: float,
                          contamination_input: float,
                          moisture_input: float,
                          dt: float) -> Dict[str, float]:
        """
        Update oil quality based on operating conditions and time
        
        Physical Basis:
        - Thermal degradation follows Arrhenius kinetics
        - Contamination accumulation with filtration removal
        - Moisture absorption and desorption
        - Additive depletion kinetics
        
        Args:
            operating_temperature: Current oil temperature (°C)
            contamination_input: Contamination input rate (ppm/hour)
            moisture_input: Moisture input rate (%/hour)
            dt: Time step (hours)
            
        Returns:
            Dictionary with oil quality results
        """
        # Update oil temperature (first-order lag with bounds checking)
        temp_time_constant = 0.5  # hours
        temp_change = (operating_temperature - self.oil_temperature) / temp_time_constant * dt
        
        # Limit temperature change rate to prevent extreme values
        max_temp_change = 10.0 * dt  # Maximum 10°C change per hour
        temp_change = max(-max_temp_change, min(max_temp_change, temp_change))
        
        self.oil_temperature += temp_change
        
        # Ensure temperature stays within reasonable bounds
        self.oil_temperature = max(20.0, min(120.0, self.oil_temperature))
        
        # STEP 3: Reduced aggressive thermal degradation for more realistic behavior
        # Thermal degradation (Arrhenius equation) - ENHANCED with realistic rates
        reference_temp = 60.0  # °C
        temp_diff = self.oil_temperature - reference_temp
        # Limit temperature difference to prevent overflow (max 200°C above reference)
        temp_diff = max(-50.0, min(200.0, temp_diff))
        # TARGETED FIX: Much more conservative activation factor to prevent explosion
        activation_factor = max(0.1, min(1.5, 1.0 + temp_diff / 50.0))  # Linear instead of exponential
        # TARGETED FIX: Significantly reduce base degradation rate
        base_degradation_rate = 0.00001  # Very conservative base rate (90% reduction)
        thermal_degradation_rate = base_degradation_rate * activation_factor
        
        # STEP 4: FIXED - Reduced filtration effectiveness for maintenance scenario realism
        # Base filtration efficiency reduced from 85% to 60% for more realistic contamination buildup
        base_filtration_efficiency = 0.60  # 60% - realistic industrial oil filtration for maintenance scenarios
        
        # Increased filter loading sensitivity - filters more affected by contamination
        filter_loading_factor = max(0.3, 1.0 - (self.oil_contamination_level / 50.0))
        # At 15 ppm: factor = 0.7 (30% efficiency reduction) - more realistic
        # At 25 ppm: factor = 0.5 (50% efficiency reduction) - more realistic
        
        # Increased temperature sensitivity for realistic filter performance
        temp_factor = max(0.5, 1.0 - (self.oil_temperature - 60.0) / 60.0)
        # At 80°C: factor = 0.67 (33% efficiency reduction) - more realistic
        # At 100°C: factor = 0.33 (67% efficiency reduction) - more realistic
        
        # Combined filtration efficiency
        effective_filtration_efficiency = base_filtration_efficiency * filter_loading_factor * temp_factor
        
        # FIXED - Reduced circulation rate from 1.2% to 0.5% for more realistic contamination buildup
        circulation_rate = 0.005  # 0.5% per time step - more realistic circulation for maintenance scenarios
        contamination_removal_rate = self.oil_contamination_level * effective_filtration_efficiency * circulation_rate
        
        # Reduced thermal contamination generation with wear factor
        base_thermal_contamination = thermal_degradation_rate * 0.75  # Reduced base contamination generation
        
        # Add operating condition stress factors
        if hasattr(self, 'component_wear') and self.component_wear:
            avg_wear = sum(self.component_wear.values()) / len(self.component_wear)
            wear_contamination_factor = 1.0 + (avg_wear / 20.0)  # More wear = more contamination
            thermal_contamination_input = base_thermal_contamination * wear_contamination_factor
        else:
            thermal_contamination_input = base_thermal_contamination
        
        # FINAL FIX: Remove temperature acceleration that was causing explosion
        # Temperature effects are already handled in the activation factor above
        # No additional temperature acceleration needed
        
        # Calculate net contamination change
        contamination_change = contamination_input - contamination_removal_rate + thermal_contamination_input
        self.oil_contamination_level += contamination_change * dt
        self.oil_contamination_level = max(1.0, self.oil_contamination_level)  # Minimum 1 ppm
        
        # Moisture dynamics
        # Moisture input minus evaporation at high temperatures
        if self.oil_temperature > 70.0:
            evaporation_rate = (self.oil_temperature - 70.0) * 0.001  # Evaporation above 70°C
            moisture_change = moisture_input - evaporation_rate
        else:
            moisture_change = moisture_input
        
        self.oil_moisture_content += moisture_change * dt
        self.oil_moisture_content = max(0.001, self.oil_moisture_content)  # Minimum moisture
        
        # Acidity increase (oil oxidation)
        # Accelerated by temperature and contamination
        contamination_factor = 1.0 + self.oil_contamination_level / 50.0
        acidity_increase_rate = thermal_degradation_rate * contamination_factor * 0.1
        self.oil_acidity_number += acidity_increase_rate * dt
        
        # Viscosity change (thermal breakdown and contamination)
        viscosity_change_rate = thermal_degradation_rate * 0.5 + contamination_change * 0.01
        self.oil_viscosity_change += viscosity_change_rate * dt
        
        # Additive depletion
        # Antioxidants consumed by thermal stress
        antioxidant_consumption_rate = thermal_degradation_rate * 10.0  # 10x faster than base oil
        self.antioxidant_level = max(0.0, self.antioxidant_level - antioxidant_consumption_rate * dt * 100.0)
        
        # Anti-wear additives consumed by mechanical stress
        mechanical_stress_factor = contamination_input * 0.1  # Contamination indicates mechanical stress
        aw_consumption_rate = mechanical_stress_factor * 0.5
        self.anti_wear_additive_level = max(0.0, self.anti_wear_additive_level - aw_consumption_rate * dt * 100.0)
        
        # Corrosion inhibitors consumed by moisture
        ci_consumption_rate = self.oil_moisture_content * 2.0
        self.corrosion_inhibitor_level = max(0.0, self.corrosion_inhibitor_level - ci_consumption_rate * dt * 100.0)
        
        # Calculate overall oil quality factors
        contamination_factor = max(0.1, 1.0 - self.oil_contamination_level / self.config.contamination_limit)
        acidity_factor = max(0.1, 1.0 - self.oil_acidity_number / self.config.acidity_limit)
        moisture_factor = max(0.1, 1.0 - self.oil_moisture_content / self.config.moisture_limit)
        viscosity_factor = max(0.1, 1.0 - abs(self.oil_viscosity_change) / self.config.viscosity_change_limit)
        
        # Additive factors
        antioxidant_factor = self.antioxidant_level / 100.0
        aw_factor = self.anti_wear_additive_level / 100.0
        
        # FIXED: Improved lubrication effectiveness calculation with realistic weighting
        # Use geometric mean for critical factors and arithmetic mean for secondary factors
        critical_factors = [contamination_factor, antioxidant_factor, aw_factor]
        secondary_factors = [acidity_factor, moisture_factor, viscosity_factor]
        
        # Geometric mean of critical factors (prevents one bad factor from dominating)
        critical_effectiveness = (contamination_factor * antioxidant_factor * aw_factor) ** (1/3)
        
        # Arithmetic mean of secondary factors
        secondary_effectiveness = sum(secondary_factors) / len(secondary_factors)
        
        # Combined effectiveness with 70% weight on critical factors, 30% on secondary
        self.lubrication_effectiveness = critical_effectiveness * 0.7 + secondary_effectiveness * 0.3
        
        # FIXED: More realistic bounds - even degraded oil provides some lubrication
        self.lubrication_effectiveness = max(0.3, min(1.0, self.lubrication_effectiveness))
        
        # Update operating hours
        self.oil_operating_hours += dt
        self.operating_hours += dt
        
        return {
            'oil_quality_factor': self.lubrication_effectiveness,
            'contamination_level': self.oil_contamination_level,
            'acidity_number': self.oil_acidity_number,
            'moisture_content': self.oil_moisture_content,
            'viscosity_change': self.oil_viscosity_change,
            'antioxidant_level': self.antioxidant_level,
            'anti_wear_level': self.anti_wear_additive_level,
            'thermal_degradation_rate': thermal_degradation_rate,
            'contamination_factor': contamination_factor,
            'oil_temperature': self.oil_temperature
        }
    
    def update_component_wear(self, operating_conditions: Dict, dt: float) -> Dict[str, float]:
        """
        Update wear for all lubricated components
        
        Args:
            operating_conditions: Dictionary with operating conditions for each component
            dt: Time step (hours)
            
        Returns:
            Dictionary with component wear results
        """
        wear_results = {}
        
        for component_id, component in self.components.items():
            # Get component-specific operating conditions
            comp_conditions = operating_conditions.get(component_id, {})
            
            # Calculate component wear rate
            wear_rate = self.calculate_component_wear(component_id, comp_conditions)
            
            # Apply lubrication quality effects
            lubrication_wear_factor = 1.0 + (1.0 - self.lubrication_effectiveness) * component.contamination_wear_factor
            actual_wear_rate = wear_rate * lubrication_wear_factor
            
            # Update component wear
            wear_increase = actual_wear_rate * dt
            self.component_wear[component_id] += wear_increase
            
            # Calculate performance degradation
            wear_performance_loss = self.component_wear[component_id] * component.wear_performance_factor
            lubrication_performance_loss = (1.0 - self.lubrication_effectiveness) * component.lubrication_performance_factor
            total_performance_loss = wear_performance_loss + lubrication_performance_loss
            
            self.component_performance_factors[component_id] = max(0.1, 1.0 - total_performance_loss)
            
            wear_results[component_id] = {
                'wear_rate': actual_wear_rate,
                'total_wear': self.component_wear[component_id],
                'performance_factor': self.component_performance_factors[component_id],
                'wear_increase': wear_increase,
                'lubrication_factor': lubrication_wear_factor
            }
        
        # Calculate overall system health
        avg_performance = sum(self.component_performance_factors.values()) / len(self.component_performance_factors)
        self.system_health_factor = avg_performance * self.lubrication_effectiveness
        
        return wear_results
    
    def check_maintenance_requirements(self) -> Dict[str, bool]:
        """Check if maintenance is required based on oil quality and component wear"""
        maintenance_flags = {
            'oil_change_due': self.oil_operating_hours >= self.config.oil_change_interval,
            'oil_analysis_due': (self.oil_operating_hours % self.config.oil_analysis_interval) < 1.0,
            'contamination_high': self.oil_contamination_level >= self.config.contamination_limit,
            'acidity_high': self.oil_acidity_number >= self.config.acidity_limit,
            'moisture_high': self.oil_moisture_content >= self.config.moisture_limit,
            'viscosity_change_high': abs(self.oil_viscosity_change) >= self.config.viscosity_change_limit,
            'antioxidants_depleted': self.antioxidant_level < 20.0,
            'additives_depleted': self.anti_wear_additive_level < 30.0
        }
        
        # Check component wear
        for component_id, component in self.components.items():
            wear_level = self.component_wear[component_id]
            maintenance_flags[f'{component_id}_wear_alarm'] = wear_level >= component.wear_alarm_threshold
            maintenance_flags[f'{component_id}_wear_trip'] = wear_level >= component.wear_trip_threshold
        
        # Set overall maintenance flags
        self.maintenance_due = any([
            maintenance_flags['oil_change_due'],
            maintenance_flags['contamination_high'],
            maintenance_flags['acidity_high'],
            maintenance_flags['moisture_high']
        ])
        
        self.oil_analysis_due = maintenance_flags['oil_analysis_due']
        
        return maintenance_flags
    
    def check_alarms_and_trips(self) -> Dict[str, List[str]]:
        """Check for alarm and trip conditions"""
        alarms = []
        trips = []
        
        # Oil quality alarms
        if self.oil_contamination_level >= self.config.contamination_limit * 0.8:
            alarms.append("High Oil Contamination")
        if self.oil_contamination_level >= self.config.contamination_limit:
            trips.append("Excessive Oil Contamination")
        
        if self.oil_acidity_number >= self.config.acidity_limit * 0.8:
            alarms.append("High Oil Acidity")
        if self.oil_acidity_number >= self.config.acidity_limit:
            trips.append("Excessive Oil Acidity")
        
        if self.oil_moisture_content >= self.config.moisture_limit * 0.8:
            alarms.append("High Oil Moisture")
        if self.oil_moisture_content >= self.config.moisture_limit:
            trips.append("Excessive Oil Moisture")
        
        # Oil level and pressure alarms (oil_level is now in percentage 0-100%)
        if self.oil_level < 20.0:  # Below 20%
            alarms.append("Low Oil Level")
        if self.oil_level < 10.0:  # Below 10%
            trips.append("Very Low Oil Level")
        
        # High oil level protection
        if self.oil_level > 95.0:  # Above 95%
            alarms.append("High Oil Level")
        if self.oil_level > 105.0:  # Above 105% (overfill with spillage)
            trips.append("Oil System Overfill")
        
        if self.oil_pressure < self.config.oil_operating_pressure * 0.8:
            alarms.append("Low Oil Pressure")
        if self.oil_pressure < self.config.oil_operating_pressure * 0.6:
            trips.append("Very Low Oil Pressure")
        
        # Temperature alarms
        if self.oil_temperature > self.config.oil_temperature_range[1]:
            alarms.append("High Oil Temperature")
        if self.oil_temperature > self.config.oil_temperature_range[1] + 10.0:
            trips.append("Excessive Oil Temperature")
        
        # Component wear alarms and trips
        for component_id, component in self.components.items():
            wear_level = self.component_wear[component_id]
            if wear_level >= component.wear_alarm_threshold:
                alarms.append(f"{component_id} Wear Alarm")
            if wear_level >= component.wear_trip_threshold:
                trips.append(f"{component_id} Wear Trip")
        
        # Additive depletion alarms
        if self.antioxidant_level < 30.0:
            alarms.append("Antioxidant Depletion")
        if self.anti_wear_additive_level < 40.0:
            alarms.append("Anti-Wear Additive Depletion")
        
        self.active_alarms = alarms
        self.trip_conditions = trips
        
        return {
            'alarms': alarms,
            'trips': trips,
            'alarm_count': len(alarms),
            'trip_count': len(trips)
        }
    
    def perform_maintenance(self, maintenance_type: str, **kwargs) -> Dict[str, float]:
        """
        Perform maintenance actions on the lubrication system
        
        Args:
            maintenance_type: Type of maintenance to perform
            **kwargs: Additional maintenance parameters
            
        Returns:
            Dictionary with maintenance results compatible with MaintenanceResult
        """
        results = {
            'success': True,
            'duration_hours': 1.0,
            'work_performed': f"Performed {maintenance_type} on lubrication system",
            'findings': None,
            'recommendations': [],
            'effectiveness_score': 1.0
        }
        
        if maintenance_type == "oil_change":
            # Complete oil change - Enhanced with viscosity restoration
            old_hours = self.oil_operating_hours
            old_contamination = self.oil_contamination_level
            old_viscosity_change = self.oil_viscosity_change
            old_acidity = self.oil_acidity_number
            
            # Reset oil operating hours
            self.oil_operating_hours = 0.0
            
            # Restore fresh oil contamination levels
            self.oil_contamination_level = 0.5  # Very clean fresh oil
            self.oil_acidity_number = 0.02      # Fresh oil low acidity
            self.oil_moisture_content = 0.005   # Dry fresh oil
            
            # ENHANCED: Restore fresh oil viscosity properties
            self.oil_viscosity_change = 0.0     # Reset viscosity change
            self.oil_viscosity_index = 95.0     # Fresh oil viscosity index
            
            # Restore viscosity at standard temperatures based on oil grade
            if "VG 32" in self.config.oil_viscosity_grade:
                self.oil_viscosity_at_40c = 32.0
                self.oil_viscosity_at_100c = 5.4
            elif "VG 46" in self.config.oil_viscosity_grade:
                self.oil_viscosity_at_40c = 46.0
                self.oil_viscosity_at_100c = 6.8
            elif "VG 68" in self.config.oil_viscosity_grade:
                self.oil_viscosity_at_40c = 68.0
                self.oil_viscosity_at_100c = 8.8
            else:
                # Default to VG 46
                self.oil_viscosity_at_40c = 46.0
                self.oil_viscosity_at_100c = 6.8
            
            # Restore fresh additive package to 100%
            self.antioxidant_level = 100.0
            self.anti_wear_additive_level = 100.0
            self.corrosion_inhibitor_level = 100.0
            
            # Restore oil level to 95%
            self.oil_level = 95.0
            
            self.oil_changes_performed += 1
            
            # Calculate improvements achieved
            contamination_improvement = old_contamination - self.oil_contamination_level
            viscosity_improvement = abs(old_viscosity_change)
            acidity_improvement = old_acidity - self.oil_acidity_number
            
            results.update({
                'duration_hours': 4.0,
                'work_performed': f"Complete oil change on {self.config.system_id}",
                'findings': f"Replaced {old_hours:.1f} hour old oil. Improvements: "
                           f"contamination -{contamination_improvement:.1f} ppm, "
                           f"viscosity restored {viscosity_improvement:.1f}%, "
                           f"acidity -{acidity_improvement:.2f} mg KOH/g",
                'effectiveness_score': 1.0
            })
            
        elif maintenance_type == "oil_top_off":
            # Enhanced oil top-off with dilution effects
            old_level = self.oil_level
            old_contamination = self.oil_contamination_level
            old_acidity = self.oil_acidity_number
            old_moisture = self.oil_moisture_content
            old_viscosity_change = self.oil_viscosity_change
            
            target_level = kwargs.get('target_level', 95.0)
            oil_added_percent = max(0.0, target_level - old_level)
            
            if oil_added_percent > 0:
                self.oil_level = min(100.0, target_level)
                
                # Calculate dilution factor (fraction of fresh oil added)
                dilution_factor = oil_added_percent / 100.0
                
                # DILUTION EFFECTS - fresh oil improves quality
                # Contamination dilution (fresh oil is much cleaner)
                contamination_dilution = dilution_factor * 0.7  # 70% improvement factor
                self.oil_contamination_level = max(0.5, self.oil_contamination_level * (1.0 - contamination_dilution))
                
                # Acidity dilution (fresh oil has low acidity)
                acidity_dilution = dilution_factor * 0.4  # 40% improvement factor
                self.oil_acidity_number = max(0.02, self.oil_acidity_number * (1.0 - acidity_dilution))
                
                # Moisture dilution (fresh oil is dry)
                moisture_dilution = dilution_factor * 0.5  # 50% improvement factor
                self.oil_moisture_content = max(0.005, self.oil_moisture_content * (1.0 - moisture_dilution))
                
                # ADDITIVE RESTORATION - fresh oil has additives
                additive_boost = dilution_factor * 30.0  # Up to 30% boost
                self.antioxidant_level = min(100.0, self.antioxidant_level + additive_boost)
                self.anti_wear_additive_level = min(100.0, self.anti_wear_additive_level + additive_boost * 0.8)
                self.corrosion_inhibitor_level = min(100.0, self.corrosion_inhibitor_level + additive_boost * 0.6)
                
                # VISCOSITY IMPROVEMENT - fresh oil has better viscosity
                viscosity_improvement = dilution_factor * 0.3  # 30% improvement factor
                self.oil_viscosity_change *= (1.0 - viscosity_improvement)
                
                # Calculate improvements achieved
                contamination_improvement = old_contamination - self.oil_contamination_level
                acidity_improvement = old_acidity - self.oil_acidity_number
                moisture_improvement = old_moisture - self.oil_moisture_content
                viscosity_improvement_actual = abs(old_viscosity_change - self.oil_viscosity_change)
                
                results.update({
                    'duration_hours': 0.5,
                    'work_performed': f"Oil top-off on {self.config.system_id}",
                    'findings': f"Added {oil_added_percent:.1f}% fresh oil, level now {self.oil_level:.1f}%. "
                               f"Dilution improvements: contamination -{contamination_improvement:.1f} ppm, "
                               f"acidity -{acidity_improvement:.2f} mg KOH/g, "
                               f"moisture -{moisture_improvement:.3f}%, "
                               f"viscosity improved {viscosity_improvement_actual:.1f}%",
                    'effectiveness_score': min(1.0, 0.5 + dilution_factor)  # Effectiveness scales with amount added
                })
            else:
                # No oil added (already at or above target level)
                results.update({
                    'duration_hours': 0.1,
                    'work_performed': f"Oil level check on {self.config.system_id}",
                    'findings': f"Oil level already at {self.oil_level:.1f}%, no top-off needed",
                    'effectiveness_score': 1.0
                })
            
        elif maintenance_type == "filtration":
            # Enhanced filtration/cleaning
            contamination_removed = self.oil_contamination_level * 0.8
            self.oil_contamination_level *= 0.2  # Remove 80% of contamination
            
            results.update({
                'duration_hours': 2.0,
                'work_performed': f"Enhanced filtration on {self.config.system_id}",
                'findings': f"Removed {contamination_removed:.1f} ppm contamination",
                'effectiveness_score': 0.9
            })
            
        elif maintenance_type == "filter_change":
            # Filter replacement
            contamination_removed = self.oil_contamination_level * 0.5
            self.oil_contamination_level *= 0.5  # Remove 50% of contamination
            self.filter_changes_performed += 1
            
            results.update({
                'duration_hours': 1.5,
                'work_performed': f"Filter replacement on {self.config.system_id}",
                'findings': f"Replaced filters, removed {contamination_removed:.1f} ppm contamination",
                'effectiveness_score': 0.8
            })
            
        elif maintenance_type == "component_overhaul":
            # Component overhaul/replacement
            component_id = kwargs.get('component_id', None)
            if component_id and component_id in self.component_wear:
                old_wear = self.component_wear[component_id]
                self.component_wear[component_id] = 0.0
                self.component_performance_factors[component_id] = 1.0
                
                results.update({
                    'duration_hours': 8.0,
                    'work_performed': f"Component overhaul: {component_id}",
                    'findings': f"Overhauled {component_id}, reset {old_wear:.1f}% wear",
                    'effectiveness_score': 1.0
                })
            else:
                # Overhaul all components
                total_wear_reset = 0.0
                for comp_id in self.component_wear:
                    total_wear_reset += self.component_wear[comp_id] * 0.9
                    self.component_wear[comp_id] *= 0.1  # Reduce wear by 90%
                    self.component_performance_factors[comp_id] = min(1.0, 
                        self.component_performance_factors[comp_id] + 0.2)
                
                results.update({
                    'duration_hours': 16.0,
                    'work_performed': f"Complete system overhaul on {self.config.system_id}",
                    'findings': f"Overhauled all components, reset {total_wear_reset:.1f}% total wear",
                    'effectiveness_score': 1.0
                })
        
        elif maintenance_type == "additive_treatment":
            # Add additive package
            old_antioxidant = self.antioxidant_level
            old_aw = self.anti_wear_additive_level
            
            self.antioxidant_level = min(100.0, self.antioxidant_level + 50.0)
            self.anti_wear_additive_level = min(100.0, self.anti_wear_additive_level + 40.0)
            self.corrosion_inhibitor_level = min(100.0, self.corrosion_inhibitor_level + 30.0)
            
            results.update({
                'duration_hours': 1.0,
                'work_performed': f"Additive treatment on {self.config.system_id}",
                'findings': f"Restored additives: AO {old_antioxidant:.0f}%->{self.antioxidant_level:.0f}%, AW {old_aw:.0f}%->{self.anti_wear_additive_level:.0f}%",
                'effectiveness_score': 0.8
            })
            
        elif maintenance_type == "efficiency_analysis":
            # Efficiency analysis (inspection/diagnostic)
            efficiency = self.lubrication_effectiveness * 100.0
            health = self.system_health_factor * 100.0
            
            if efficiency > 90.0:
                findings = f"Excellent lubrication efficiency: {efficiency:.1f}%"
                recommendations = ["Continue normal operation"]
            elif efficiency > 75.0:
                findings = f"Good lubrication efficiency: {efficiency:.1f}%"
                recommendations = ["Monitor oil quality", "Consider additive treatment"]
            else:
                findings = f"Poor lubrication efficiency: {efficiency:.1f}%"
                recommendations = ["Oil change recommended", "Check filtration system", "Inspect components"]
            
            results.update({
                'duration_hours': 2.0,
                'work_performed': f"Efficiency analysis on {self.config.system_id}",
                'findings': findings,
                'recommendations': recommendations,
                'effectiveness_score': 1.0  # Analysis is always effective
            })
            
        elif maintenance_type == "routine_maintenance":
            # Routine maintenance (general upkeep)
            # Minor improvements from routine maintenance
            self.oil_level = min(100.0, self.oil_level + 2.0)
            self.oil_contamination_level = max(1.0, self.oil_contamination_level - 1.0)
            
            results.update({
                'duration_hours': 3.0,
                'work_performed': f"Routine maintenance on {self.config.system_id}",
                'findings': "Performed standard maintenance tasks, minor improvements achieved",
                'effectiveness_score': 0.7
            })
            
        else:
            # Unknown maintenance type - return failure
            results.update({
                'success': False,
                'duration_hours': 0.0,
                'work_performed': f"Unknown maintenance type: {maintenance_type}",
                'findings': f"Maintenance type '{maintenance_type}' not supported by lubrication system",
                'effectiveness_score': 0.0
            })
        
        # Recalculate lubrication effectiveness after maintenance (if successful)
        if results['success']:
            self.update_oil_quality(self.oil_temperature, 0.0, 0.0, 0.0)
        
        return results
    
    def get_state_dict(self) -> Dict[str, float]:
        """Get current state as dictionary for logging/monitoring"""
        state_dict = {
            f'{self.config.system_id}_oil_level': self.oil_level,
            f'{self.config.system_id}_oil_temperature': self.oil_temperature,
            f'{self.config.system_id}_oil_pressure': self.oil_pressure,
            f'{self.config.system_id}_oil_contamination': self.oil_contamination_level,
            f'{self.config.system_id}_oil_acidity': self.oil_acidity_number,
            f'{self.config.system_id}_oil_moisture': self.oil_moisture_content,
            f'{self.config.system_id}_lubrication_effectiveness': self.lubrication_effectiveness,
            f'{self.config.system_id}_system_health': self.system_health_factor,
            f'{self.config.system_id}_operating_hours': self.operating_hours,
            f'{self.config.system_id}_oil_operating_hours': self.oil_operating_hours,
            f'{self.config.system_id}_maintenance_due': float(self.maintenance_due),
            f'{self.config.system_id}_antioxidant_level': self.antioxidant_level,
            f'{self.config.system_id}_anti_wear_level': self.anti_wear_additive_level
        }
        
        # Add component wear states
        for component_id in self.components:
            state_dict[f'{self.config.system_id}_{component_id}_wear'] = self.component_wear[component_id]
            state_dict[f'{self.config.system_id}_{component_id}_performance'] = self.component_performance_factors[component_id]
        
        return state_dict
    
    def reset(self) -> None:
        """Reset lubrication system to initial conditions"""
        # Reset oil system state
        self.oil_level = 90.0  # % oil level (0-100%)
        self.oil_temperature = 55.0
        self.oil_pressure = self.config.oil_operating_pressure
        self.oil_flow_rate = 50.0
        
        # Reset oil quality
        self.oil_contamination_level = 5.0
        self.oil_moisture_content = 0.02
        self.oil_acidity_number = 0.15
        self.oil_viscosity_index = 95.0
        self.oil_viscosity_change = 0.0
        self.oil_operating_hours = 0.0
        
        # Reset additive levels
        self.antioxidant_level = 100.0
        self.anti_wear_additive_level = 100.0
        self.corrosion_inhibitor_level = 100.0
        
        # Reset component wear
        self.component_wear = {comp_id: 0.0 for comp_id in self.components.keys()}
        self.component_performance_factors = {comp_id: 1.0 for comp_id in self.components.keys()}
        
        # Reset system health
        self.lubrication_effectiveness = 1.0
        self.system_health_factor = 1.0
        self.maintenance_due = False
        self.oil_analysis_due = False
        
        # Reset tracking
        self.operating_hours = 0.0
        self.oil_changes_performed = 0
        self.filter_changes_performed = 0
        
        # Reset alarms
        self.active_alarms = []
        self.trip_conditions = []


# Example usage and testing
if __name__ == "__main__":
    print("Base Lubrication System - Abstract Class Validation")
    print("=" * 60)
    print("This module provides abstract base classes for lubrication systems.")
    print("Specific implementations should inherit from BaseLubricationSystem.")
    print()
    print("Key Features:")
    print("- Oil quality tracking and degradation modeling")
    print("- Component wear calculation with lubrication effects")
    print("- Maintenance scheduling and procedures")
    print("- Alarm and trip condition monitoring")
    print("- Performance degradation tracking")
    print()
    print("Ready for implementation by specific lubrication systems!")

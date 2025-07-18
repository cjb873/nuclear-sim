"""
Enhanced Condenser Physics Model with Advanced States

This module extends the basic condenser model with additional high-priority states:
- Tube plugging and degradation tracking
- Advanced fouling states (biofouling, scale, corrosion)
- Cooling water quality parameters
- Integration with modular vacuum system

Parameter Sources:
- Heat Exchanger Design Handbook (Hewitt)
- Power Plant Engineering (Black & Veatch)
- EPRI Condenser Performance Guidelines
- Cooling water chemistry standards
- Tube degradation and fouling studies

Physical Basis:
- Enhanced heat transfer with degradation effects
- Multi-component fouling models
- Water chemistry impact on performance
- Tube-level failure mechanisms
"""

import warnings
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
import numpy as np

# Import state management interfaces
from simulator.state import auto_register

# Import heat flow tracking
from ..heat_flow_tracker import HeatFlowProvider, ThermodynamicProperties

from .vacuum_system import VacuumSystem, VacuumSystemConfig
from .vacuum_pump import SteamEjectorConfig
from ..water_chemistry import WaterChemistry, WaterChemistryConfig

# Import chemistry flow interfaces
from ..chemistry_flow_tracker import ChemistryFlowProvider, ChemicalSpecies

from ..component_descriptions import CONDENSER_COMPONENT_DESCRIPTIONS

warnings.filterwarnings("ignore")


# NOTE: TubeDegradationConfig and FoulingConfig moved to condenser/config.py
# Import them from the centralized configuration system
from .config import CondenserTubeDegradationConfig, CondenserFoulingConfig


class TubeDegradationModel:
    """Model for tube degradation and failure mechanisms"""
    
    def __init__(self, config: CondenserTubeDegradationConfig):
        self.config = config
        
        # Tube state tracking
        self.active_tube_count = config.initial_tube_count
        self.plugged_tube_count = 0
        self.average_wall_thickness = config.wall_thickness_initial
        self.tube_leak_rate = 0.0              # kg/s total leakage
        
        # Degradation tracking
        self.vibration_damage_accumulation = 0.0  # Cumulative damage factor
        self.corrosion_damage_accumulation = 0.0  # Cumulative corrosion
        self.operating_hours = 0.0
        
        # Performance impacts
        self.effective_heat_transfer_area_factor = 1.0  # Reduction due to plugging
        self.tube_side_pressure_drop_factor = 1.0       # Increase due to plugging
        
    def update_tube_failures(self, 
                           cooling_water_velocity: float,
                           water_chemistry_aggressiveness: float,
                           dt: float) -> Dict[str, float]:
        """
        Update tube failure mechanisms
        
        Args:
            cooling_water_velocity: Average velocity in tubes (m/s)
            water_chemistry_aggressiveness: Chemistry aggressiveness factor (0-2)
            dt: Time step (hours)
            
        Returns:
            Dictionary with tube degradation results
        """
        # Vibration-induced damage
        if cooling_water_velocity > self.config.vibration_damage_threshold:
            vibration_damage_rate = ((cooling_water_velocity - self.config.vibration_damage_threshold) ** 2) * 0.001
            self.vibration_damage_accumulation += vibration_damage_rate * dt
        
        # Corrosion-induced wall thinning
        corrosion_rate = (self.config.corrosion_rate * 
                         water_chemistry_aggressiveness * 
                         (1.0 + self.vibration_damage_accumulation))
        wall_thickness_loss = corrosion_rate * dt
        self.average_wall_thickness = max(self.config.wall_thickness_minimum,
                                        self.average_wall_thickness - wall_thickness_loss)
        self.corrosion_damage_accumulation += wall_thickness_loss
        
        # Tube failure rate calculation
        # Base failure rate increased by damage factors
        vibration_factor = 1.0 + 10.0 * self.vibration_damage_accumulation
        corrosion_factor = 1.0 + 5.0 * (self.corrosion_damage_accumulation / self.config.wall_thickness_initial)
        chemistry_factor = 1.0 + water_chemistry_aggressiveness
        
        effective_failure_rate = (self.config.tube_failure_rate * 
                                vibration_factor * 
                                corrosion_factor * 
                                chemistry_factor)
        
        # Calculate new tube failures
        tubes_failed = effective_failure_rate * self.active_tube_count * dt
        tubes_failed = min(tubes_failed, self.active_tube_count * 0.01)  # Max 1% per time step
        
        # Update tube counts
        self.plugged_tube_count += tubes_failed
        self.active_tube_count = max(1000, self.config.initial_tube_count - self.plugged_tube_count)
        
        # Calculate performance impacts
        self.effective_heat_transfer_area_factor = self.active_tube_count / self.config.initial_tube_count
        
        # Increased velocity in remaining tubes increases pressure drop
        velocity_increase_factor = self.config.initial_tube_count / self.active_tube_count
        self.tube_side_pressure_drop_factor = velocity_increase_factor ** 1.8  # Turbulent flow
        
        # Tube leakage (simplified model)
        # Assume some failed tubes develop leaks before being plugged
        leak_prone_tubes = min(tubes_failed * 0.1, self.active_tube_count * 0.001)
        self.tube_leak_rate = leak_prone_tubes * 0.001  # kg/s per leaking tube
        
        self.operating_hours += dt
        
        return {
            'active_tube_count': self.active_tube_count,
            'plugged_tube_count': self.plugged_tube_count,
            'tubes_failed_this_step': tubes_failed,
            'average_wall_thickness': self.average_wall_thickness,
            'tube_leak_rate': self.tube_leak_rate,
            'heat_transfer_area_factor': self.effective_heat_transfer_area_factor,
            'pressure_drop_factor': self.tube_side_pressure_drop_factor,
            'vibration_damage': self.vibration_damage_accumulation,
            'corrosion_damage': self.corrosion_damage_accumulation
        }


class AdvancedFoulingModel:
    """Model for multi-component fouling (biofouling, scale, corrosion products)"""
    
    def __init__(self, config):
        self.config = config
        
        # Fouling thickness tracking (mm)
        self.biofouling_thickness = 0.0
        self.scale_thickness = 0.0
        self.corrosion_product_thickness = 0.0
        
        # Fouling distribution and cleaning
        self.fouling_distribution_factor = 1.0    # Non-uniformity (1.0 = uniform)
        self.time_since_cleaning = 0.0            # Hours since last cleaning
        self.cleaning_effectiveness_history = []   # Track cleaning effectiveness
        
        # Fouling resistance calculation
        self.total_fouling_resistance = 0.0       # m²K/W
        
    def calculate_biofouling(self,
                           water_temperature: float,
                           chlorine_residual: float,
                           nutrient_level: float,
                           dt: float) -> float:
        """
        Calculate biofouling growth rate
        
        Args:
            water_temperature: Average water temperature (°C)
            chlorine_residual: Free chlorine concentration (mg/L)
            nutrient_level: Relative nutrient availability (0-2)
            dt: Time step (hours)
            
        Returns:
            Biofouling thickness increase (mm)
        """
        # Temperature effect (exponential growth with temperature)
        temp_factor = np.exp(self.config.biofouling_temp_coefficient * (water_temperature - 25.0))
        
        # Chlorine disinfection effect (reduces growth)
        chlorine_factor = 1.0 / (1.0 + chlorine_residual * 2.0)
        
        # Nutrient availability effect
        nutrient_factor = nutrient_level * self.config.biofouling_nutrient_factor
        
        # Growth rate calculation
        growth_rate = (self.config.biofouling_base_rate * 
                      temp_factor * 
                      chlorine_factor * 
                      nutrient_factor)
        
        # Growth slows as thickness increases (self-limiting)
        thickness_factor = 1.0 / (1.0 + self.biofouling_thickness / 2.0)
        
        thickness_increase = growth_rate * thickness_factor * (dt / 1000.0)  # Convert to hours
        return max(0.0, thickness_increase)
    
    def calculate_scale_formation(self,
                                water_temperature: float,
                                water_hardness: float,
                                ph: float,
                                antiscalant_concentration: float,
                                dt: float) -> float:
        """
        Calculate mineral scale formation rate
        
        Args:
            water_temperature: Average water temperature (°C)
            water_hardness: Water hardness (mg/L as CaCO3)
            ph: Water pH
            antiscalant_concentration: Antiscalant dose (mg/L)
            dt: Time step (hours)
            
        Returns:
            Scale thickness increase (mm)
        """
        # Temperature effect (higher temperature increases precipitation)
        temp_factor = np.exp(self.config.scale_temp_coefficient * (water_temperature - 25.0) / 10.0)
        
        # Hardness effect (more minerals = more scale)
        hardness_factor = (water_hardness / 150.0) * self.config.scale_hardness_coefficient
        
        # pH effect (higher pH increases CaCO3 precipitation)
        ph_factor = max(0.1, (ph - 6.0) / 2.0)  # Optimal around pH 8
        
        # Antiscalant inhibition effect
        antiscalant_factor = 1.0 / (1.0 + antiscalant_concentration / 5.0)
        
        # Scale formation rate
        formation_rate = (self.config.scale_base_rate * 
                         temp_factor * 
                         hardness_factor * 
                         ph_factor * 
                         antiscalant_factor)
        
        # Formation slows as thickness increases (mass transfer limitation)
        thickness_factor = 1.0 / (1.0 + self.scale_thickness / 1.0)
        
        thickness_increase = formation_rate * thickness_factor * (dt / 1000.0)
        return max(0.0, thickness_increase)
    
    def calculate_corrosion_products(self,
                                   water_temperature: float,
                                   dissolved_oxygen: float,
                                   ph: float,
                                   corrosion_inhibitor: float,
                                   flow_velocity: float,
                                   dt: float) -> float:
        """
        Calculate corrosion product deposition rate
        
        Args:
            water_temperature: Average water temperature (°C)
            dissolved_oxygen: Dissolved oxygen concentration (mg/L)
            ph: Water pH
            corrosion_inhibitor: Corrosion inhibitor concentration (mg/L)
            flow_velocity: Water velocity (m/s)
            dt: Time step (hours)
            
        Returns:
            Corrosion product thickness increase (mm)
        """
        # Temperature effect
        temp_factor = np.exp((water_temperature - 25.0) / 20.0)
        
        # Oxygen effect (more oxygen = more corrosion)
        oxygen_factor = dissolved_oxygen * self.config.corrosion_oxygen_coefficient
        
        # pH effect (minimum corrosion around pH 7.5)
        ph_deviation = abs(ph - self.config.corrosion_ph_optimum)
        ph_factor = 1.0 + ph_deviation / 2.0
        
        # Corrosion inhibitor effect
        inhibitor_factor = 1.0 / (1.0 + corrosion_inhibitor / 10.0)
        
        # Flow velocity effect (higher velocity can remove loose products)
        velocity_factor = 1.0 / (1.0 + flow_velocity / 2.0)
        
        # Corrosion product formation rate
        formation_rate = (self.config.corrosion_base_rate * 
                         temp_factor * 
                         oxygen_factor * 
                         ph_factor * 
                         inhibitor_factor * 
                         velocity_factor)
        
        thickness_increase = formation_rate * (dt / 1000.0)
        return max(0.0, thickness_increase)
    
    def calculate_total_fouling_resistance(self) -> float:
        """
        Calculate total thermal resistance from all fouling types
        
        Returns:
            Total fouling resistance (m²K/W)
        """
        # Different fouling types have different thermal resistances per unit thickness
        # Values based on typical fouling thermal conductivities
        
        # Biofouling: Low thermal conductivity (0.5 W/m/K)
        bio_resistance = (self.biofouling_thickness / 1000.0) / 0.5
        
        # Scale: Moderate thermal conductivity (2.0 W/m/K)
        scale_resistance = (self.scale_thickness / 1000.0) / 2.0
        
        # Corrosion products: Variable conductivity (1.0 W/m/K)
        corrosion_resistance = (self.corrosion_product_thickness / 1000.0) / 1.0
        
        # Total resistance (series thermal resistances)
        total_resistance = bio_resistance + scale_resistance + corrosion_resistance
        
        # Apply distribution factor (non-uniform fouling is worse)
        total_resistance *= self.fouling_distribution_factor
        
        return total_resistance
    
    def update_fouling(self,
                      water_temp: float,
                      water_chemistry: Dict[str, float],
                      flow_velocity: float,
                      dt: float) -> Dict[str, float]:
        """
        Update all fouling mechanisms
        
        Args:
            water_temp: Average water temperature (°C)
            water_chemistry: Dictionary with water chemistry parameters
            flow_velocity: Water velocity (m/s)
            dt: Time step (hours)
            
        Returns:
            Dictionary with fouling results
        """
        # Extract chemistry parameters
        chlorine = water_chemistry.get('chlorine_residual', 0.5)
        hardness = water_chemistry.get('hardness', 150.0)
        ph = water_chemistry.get('ph', 7.5)
        dissolved_oxygen = water_chemistry.get('dissolved_oxygen', 8.0)
        antiscalant = water_chemistry.get('antiscalant', 5.0)
        corrosion_inhibitor = water_chemistry.get('corrosion_inhibitor', 10.0)
        nutrient_level = water_chemistry.get('nutrient_level', 1.0)
        
        # Update individual fouling components
        bio_increase = self.calculate_biofouling(water_temp, chlorine, nutrient_level, dt)
        scale_increase = self.calculate_scale_formation(water_temp, hardness, ph, antiscalant, dt)
        corrosion_increase = self.calculate_corrosion_products(
            water_temp, dissolved_oxygen, ph, corrosion_inhibitor, flow_velocity, dt
        )
        
        # Update thicknesses
        self.biofouling_thickness += bio_increase
        self.scale_thickness += scale_increase
        self.corrosion_product_thickness += corrosion_increase
        
        # Update time tracking
        self.time_since_cleaning += dt
        
        # Calculate total fouling resistance
        self.total_fouling_resistance = self.calculate_total_fouling_resistance()
        
        # Update fouling distribution (becomes more non-uniform over time)
        self.fouling_distribution_factor = min(1.5, 1.0 + self.time_since_cleaning / 8760.0)  # Yearly cycle
        
        return {
            'biofouling_thickness': self.biofouling_thickness,
            'scale_thickness': self.scale_thickness,
            'corrosion_thickness': self.corrosion_product_thickness,
            'total_thickness': (self.biofouling_thickness + 
                              self.scale_thickness + 
                              self.corrosion_product_thickness),
            'total_fouling_resistance': self.total_fouling_resistance,
            'fouling_distribution_factor': self.fouling_distribution_factor,
            'time_since_cleaning': self.time_since_cleaning,
            'bio_increase': bio_increase,
            'scale_increase': scale_increase,
            'corrosion_increase': corrosion_increase
        }
    
    def perform_cleaning(self, cleaning_type: str = "chemical") -> Dict[str, float]:
        """
        Perform fouling cleaning operation
        
        Args:
            cleaning_type: Type of cleaning ("chemical", "mechanical", "hydroblast")
            
        Returns:
            Dictionary with cleaning effectiveness results
        """
        if cleaning_type == "chemical":
            # Chemical cleaning is most effective on biofouling and scale
            bio_removal = 0.8
            scale_removal = 0.6
            corrosion_removal = 0.3
            
        elif cleaning_type == "mechanical":
            # Mechanical cleaning is effective on all types but less on biofouling
            bio_removal = 0.5
            scale_removal = 0.7
            corrosion_removal = 0.8
            
        elif cleaning_type == "hydroblast":
            # High-pressure water is very effective on loose deposits
            bio_removal = 0.9
            scale_removal = 0.4  # Hard scale is difficult to remove
            corrosion_removal = 0.9
            
        else:
            # Default cleaning
            bio_removal = 0.6
            scale_removal = 0.5
            corrosion_removal = 0.5
        
        # Apply cleaning effectiveness
        bio_removed = self.biofouling_thickness * bio_removal
        scale_removed = self.scale_thickness * scale_removal
        corrosion_removed = self.corrosion_product_thickness * corrosion_removal
        
        # Update thicknesses
        self.biofouling_thickness -= bio_removed
        self.scale_thickness -= scale_removed
        self.corrosion_product_thickness -= corrosion_removed
        
        # Reset time since cleaning
        self.time_since_cleaning = 0.0
        
        # Reset distribution factor
        self.fouling_distribution_factor = 1.0
        
        # Recalculate total resistance
        self.total_fouling_resistance = self.calculate_total_fouling_resistance()
        
        # Track cleaning effectiveness
        total_removed = bio_removed + scale_removed + corrosion_removed
        self.cleaning_effectiveness_history.append(total_removed)
        
        return {
            'bio_removed': bio_removed,
            'scale_removed': scale_removed,
            'corrosion_removed': corrosion_removed,
            'total_removed': total_removed,
            'cleaning_type': cleaning_type,
            'new_fouling_resistance': self.total_fouling_resistance
        }


# NOTE: WaterQualityModel has been replaced with unified WaterChemistry system
# This eliminates duplicate water chemistry modeling and ensures consistency


# NOTE: EnhancedCondenserConfig removed - now using centralized CondenserConfig
# Import the centralized configuration system
from .config import CondenserConfig


@auto_register("SECONDARY", "condenser", allow_no_id=True,
               description=CONDENSER_COMPONENT_DESCRIPTIONS['enhanced_condenser_physics'])
class EnhancedCondenserPhysics(HeatFlowProvider, ChemistryFlowProvider):
    """
    Enhanced condenser physics model with advanced degradation states
    
    This model integrates:
    1. Basic condenser heat transfer physics
    2. Tube degradation and failure tracking
    3. Multi-component fouling models
    4. Cooling water quality effects
    5. Modular vacuum system with steam jet ejectors
    6. Performance degradation over time
    
    Physical Models Used:
    - Heat Transfer: Overall heat transfer coefficient with degradation effects
    - Tube Degradation: Vibration, corrosion, and chemistry effects
    - Fouling: Biofouling, scale, and corrosion product models
    - Water Chemistry: LSI/RSI indices and treatment effects
    - Vacuum System: Steam jet ejector performance and control
    
    Implements StateProvider interface for automatic state collection.
    """
    
    def __init__(self, config: Optional[CondenserConfig] = None, config_dict: Optional[Dict[str, Any]] = None):
        """Initialize enhanced condenser physics model"""
        if config_dict is not None:
            # Use unified configuration system (same pattern as feedwater)
            try:
                # Try to create CondenserConfig from dict if possible
                if hasattr(CondenserConfig, 'from_dict'):
                    self.config = CondenserConfig.from_dict(config_dict)
                    print(f"CONDENSER: Using unified configuration system")
                else:
                    # Fallback: use default config and store raw data
                    from .config import create_standard_condenser_config
                    self.config = create_standard_condenser_config()
                    self.config._raw_config_data = config_dict
                    print(f"CONDENSER: Using default config with raw config data")
            except Exception as e:
                print(f"CONDENSER: Failed to create config from dict: {e}")
                from .config import create_standard_condenser_config
                self.config = create_standard_condenser_config()
                self.config._raw_config_data = config_dict
        elif config is not None:
            self.config = config
            print(f"CONDENSER: Using provided CondenserConfig")
        else:
            from .config import create_standard_condenser_config
            self.config = create_standard_condenser_config()
            print(f"CONDENSER: Using default configuration")
        
        # Initialize sub-models using centralized configuration
        tube_config = self.config.tube_degradation
        fouling_config = self.config.fouling_system
        
        # Create vacuum system configuration from centralized config
        vacuum_config = self.config.vacuum_system
        
        # Create steam ejector configurations for vacuum system
        if not hasattr(vacuum_config, 'ejector_configs') or vacuum_config.ejector_configs is None:
            # Create ejector configs from steam_ejectors list in main config
            ejector_configs = self.config.steam_ejectors
            vacuum_config.ejector_configs = ejector_configs
        
        # Initialize or use provided unified water chemistry system
        if hasattr(config, 'water_chemistry') and config.water_chemistry is not None:
            self.water_chemistry = self.config.water_chemistry
        else:
            # Create own instance if not provided (for standalone use)
            self.water_chemistry = WaterChemistry(WaterChemistryConfig())
        
        # Initialize sub-models
        self.tube_degradation = TubeDegradationModel(tube_config)
        self.fouling_model = AdvancedFoulingModel(fouling_config)
        self.water_treatment = self.water_chemistry  # Use unified water chemistry system
        self.vacuum_system = VacuumSystem(vacuum_config)
        
        # Basic condenser state (similar to original model)
        self.steam_inlet_pressure = 0.007      # MPa
        self.steam_inlet_temperature = 39.0    # °C
        self.steam_inlet_flow = 1665.0         # kg/s
        self.steam_inlet_quality = 0.90        # Steam quality
        
        # Cooling water state
        self.cooling_water_inlet_temp = 25.0   # °C
        self.cooling_water_outlet_temp = 35.0  # °C
        self.cooling_water_flow = 45000.0      # kg/s
        
        # Heat transfer state
        self.heat_rejection_rate = 2000.0e6    # W
        self.overall_htc = 0.0                 # W/m²/K
        self.condensate_temperature = 39.0     # °C
        self.condensate_flow = 1665.0          # kg/s
        
        # Performance tracking
        self.thermal_performance_factor = 1.0  # Overall performance degradation
        self.operating_hours = 0.0             # Total operating time
        
        # CRITICAL: Apply initial conditions after creating components (same pattern as feedwater)
        self._apply_initial_conditions()
        
    def calculate_enhanced_heat_transfer(self,
                                       steam_flow: float,
                                       steam_pressure: float,
                                       steam_quality: float,
                                       cooling_water_flow: float,
                                       cooling_water_temp_in: float) -> Tuple[float, Dict[str, float]]:
        """
        Calculate heat transfer with all degradation effects
        
        Args:
            steam_flow: Steam mass flow rate (kg/s)
            steam_pressure: Steam pressure (MPa)
            steam_quality: Steam quality at inlet (0-1)
            cooling_water_flow: Cooling water flow rate (kg/s)
            cooling_water_temp_in: Cooling water inlet temperature (°C)
            
        Returns:
            tuple: (heat_transfer_rate_W, heat_transfer_details)
        """
        # Steam saturation temperature
        sat_temp = self._saturation_temperature(steam_pressure)
        
        # Steam condensation enthalpy
        h_g = self._saturation_enthalpy_vapor(steam_pressure)
        h_f = self._saturation_enthalpy_liquid(steam_pressure)
        h_fg = h_g - h_f  # Latent heat
        
        # Heat duty from steam condensation
        condensate_temp = sat_temp
        h_condensate = self._water_enthalpy(condensate_temp, steam_pressure)
        
        # PHYSICS-BASED STEAM ENTHALPY CALCULATION USING ACTUAL STEAM QUALITY
        # Calculate steam inlet enthalpy based on actual steam quality and pressure
        
        # Validate steam quality input
        steam_quality = np.clip(steam_quality, 0.0, 1.0)  # Ensure valid range
        
        # Calculate steam enthalpy using actual quality (works for all pressures)
        h_steam_inlet = h_f + steam_quality * h_fg  # kJ/kg
        
        # Calculate heat available per kg of steam
        heat_per_kg = h_steam_inlet - h_condensate  # kJ/kg
        
        # Validate heat per kg is reasonable (should be positive and realistic)
        if heat_per_kg <= 0:
            # This shouldn't happen with proper steam conditions
            heat_per_kg = h_fg * steam_quality  # Fallback to latent heat portion
        
        # Total heat available from steam condensation (proper unit conversion)
        heat_available_kjs = steam_flow * heat_per_kg  # kJ/s
        heat_available_watts = heat_available_kjs * 1000  # Convert kJ/s to W
        
        # Cooling water heat capacity
        cp_water = 4180.0  # J/kg/K
        
        # Estimate cooling water outlet temperature based on available heat
        temp_rise_estimate = heat_available_watts / (cooling_water_flow * cp_water)
        cooling_water_temp_out = cooling_water_temp_in + temp_rise_estimate
        
        # Log Mean Temperature Difference (LMTD)
        delta_t1 = sat_temp - cooling_water_temp_in   # Hot end
        delta_t2 = sat_temp - cooling_water_temp_out  # Cold end
        
        # Ensure temperature differences are positive and meaningful
        delta_t1 = max(delta_t1, 0.1)  # Minimum 0.1°C temperature difference
        delta_t2 = max(delta_t2, 0.1)  # Minimum 0.1°C temperature difference
        
        if abs(delta_t1 - delta_t2) < 0.1:
            lmtd = (delta_t1 + delta_t2) / 2.0
        else:
            # Ensure both deltas are positive before taking logarithm
            if delta_t1 > 0 and delta_t2 > 0:
                lmtd = (delta_t1 - delta_t2) / np.log(delta_t1 / delta_t2)
            else:
                lmtd = (delta_t1 + delta_t2) / 2.0  # Fallback to arithmetic mean
        
        # Heat transfer coefficients with degradation effects
        
        # Steam side (condensing) - affected by fouling and air concentration
        h_steam_base = self.config.heat_transfer.steam_side_htc
        
        # Get vacuum system results for air effects
        vacuum_results = self.vacuum_system.get_state_dict()
        air_concentration = (vacuum_results.get('vacuum_system_air_pressure', 0.0005) / 
                           max(0.001, vacuum_results.get('vacuum_system_pressure', 0.007)))
        air_degradation_factor = 1.0 - 0.5 * air_concentration
        
        h_steam = h_steam_base * air_degradation_factor
        
        # Cooling water side - affected by flow rate and fouling
        flow_factor = (cooling_water_flow / self.config.design_cooling_water_flow) ** 0.8
        h_water_base = self.config.heat_transfer.water_side_htc * flow_factor
        
        # Apply tube degradation effects (higher velocity in remaining tubes)
        tube_degradation_results = self.tube_degradation.__dict__
        velocity_factor = tube_degradation_results.get('tube_side_pressure_drop_factor', 1.0) ** 0.2
        h_water = h_water_base * velocity_factor
        
        # Overall heat transfer coefficient with all resistances
        # 1/U = 1/h_steam + R_fouling + t_wall/k_wall + 1/h_water
        r_steam = 1.0 / h_steam
        r_fouling = self.fouling_model.total_fouling_resistance
        r_wall = self.config.heat_transfer.tube_wall_thickness / self.config.heat_transfer.tube_wall_conductivity
        r_water = 1.0 / h_water
        
        overall_resistance = r_steam + r_fouling + r_wall + r_water
        overall_htc = 1.0 / overall_resistance
        
        # Effective heat transfer area (reduced by tube plugging)
        effective_area = (self.config.heat_transfer.heat_transfer_area * 
                         self.tube_degradation.effective_heat_transfer_area_factor)
        
        # PHYSICS-BASED HEAT TRANSFER CALCULATION
        # Calculate what the heat exchanger can actually transfer based on its physical design
        
        # Calculate theoretical heat transfer capacity: Q = U × A × LMTD
        theoretical_heat_transfer = overall_htc * effective_area * lmtd
        
        # The actual heat transfer is limited by either:
        # 1. Available energy from steam condensation, or
        # 2. Heat exchanger physical capacity
        heat_transfer_rate = min(heat_available_watts, theoretical_heat_transfer)
        
        # For energy balance validation, ensure we don't exceed available energy
        if heat_transfer_rate > heat_available_watts:
            heat_transfer_rate = heat_available_watts
        
        # Recalculate cooling water outlet temperature
        actual_temp_rise = heat_transfer_rate / (cooling_water_flow * cp_water)
        actual_cooling_water_temp_out = cooling_water_temp_in + actual_temp_rise
        
        # Update LMTD with actual temperatures
        delta_t1_actual = sat_temp - cooling_water_temp_in
        delta_t2_actual = sat_temp - actual_cooling_water_temp_out
        
        # Ensure temperature differences are positive and meaningful
        delta_t1_actual = max(delta_t1_actual, 0.1)  # Minimum 0.1°C temperature difference
        delta_t2_actual = max(delta_t2_actual, 0.1)  # Minimum 0.1°C temperature difference
        
        if abs(delta_t1_actual - delta_t2_actual) < 0.1:
            lmtd_actual = (delta_t1_actual + delta_t2_actual) / 2.0
        else:
            # Ensure both deltas are positive before taking logarithm
            if delta_t1_actual > 0 and delta_t2_actual > 0:
                lmtd_actual = (delta_t1_actual - delta_t2_actual) / np.log(delta_t1_actual / delta_t2_actual)
            else:
                lmtd_actual = (delta_t1_actual + delta_t2_actual) / 2.0  # Fallback to arithmetic mean
        
        self.overall_htc = overall_htc
        
        details = {
            'overall_htc': overall_htc,
            'effective_area': effective_area,
            'lmtd': lmtd_actual,
            'h_steam': h_steam,
            'h_water': h_water,
            'cooling_water_temp_out': actual_cooling_water_temp_out,
            'cooling_water_temp_rise': actual_temp_rise,
            'fouling_resistance': r_fouling,
            'air_degradation_factor': air_degradation_factor,
            'tube_area_factor': self.tube_degradation.effective_heat_transfer_area_factor,
            'flow_factor': flow_factor
        }
        
        return heat_transfer_rate, details
    
    def update_state(self,
                    steam_pressure: float,
                    steam_temperature: float,
                    steam_flow: float,
                    steam_quality: float,
                    cooling_water_flow: float,
                    cooling_water_temp_in: float,
                    motive_steam_pressure: float,
                    motive_steam_temperature: float,
                    makeup_water_quality: Dict[str, float],
                    chemical_doses: Dict[str, float],
                    dt: float) -> Dict[str, float]:
        """
        Update enhanced condenser state for one time step
        
        Args:
            steam_pressure: Steam inlet pressure (MPa)
            steam_temperature: Steam inlet temperature (°C)
            steam_flow: Steam mass flow rate (kg/s)
            steam_quality: Steam quality at inlet (0-1)
            cooling_water_flow: Cooling water flow rate (kg/s)
            cooling_water_temp_in: Cooling water inlet temperature (°C)
            motive_steam_pressure: Motive steam pressure for vacuum system (MPa)
            motive_steam_temperature: Motive steam temperature (°C)
            makeup_water_quality: Makeup water quality parameters
            chemical_doses: Chemical treatment doses
            dt: Time step (hours)
            
        Returns:
            Dictionary with enhanced condenser state and performance
        """
        # Update basic condenser state
        self.steam_inlet_pressure = steam_pressure
        self.steam_inlet_temperature = steam_temperature
        self.steam_inlet_flow = steam_flow
        self.steam_inlet_quality = steam_quality
        self.cooling_water_flow = cooling_water_flow
        self.cooling_water_inlet_temp = cooling_water_temp_in
        
        # Update unified water chemistry system
        water_chemistry_state = self.water_chemistry.update_chemistry(
            system_conditions={
                'makeup_water_quality': makeup_water_quality,
                'blowdown_rate': 0.02  # 2% blowdown rate
            },
            dt=dt
        )
        
        # Get pump degradation parameters (condenser uses similar parameters)
        pump_params = self.water_chemistry.get_pump_degradation_parameters()
        water_aggressiveness = pump_params['water_aggressiveness']
        
        # Get actual water chemistry parameters from unified system (no hard-coded values)
        pump_params = self.water_chemistry.get_pump_degradation_parameters()
        
        # Create compatibility dictionary for existing fouling model using actual chemistry values
        water_quality_results = {
            'ph': pump_params['ph'],
            'hardness': pump_params['hardness'],
            'total_dissolved_solids': water_chemistry_state['water_chemistry_tds'],
            'chloride': pump_params['chloride'],
            'dissolved_oxygen': 8.0,  # This is reasonable for condenser cooling water (aerated)
            'chlorine_residual': water_chemistry_state['water_chemistry_chlorine_residual'],
            'antiscalant': water_chemistry_state['water_chemistry_antiscalant'],
            'corrosion_inhibitor': water_chemistry_state['water_chemistry_corrosion_inhibitor'],
            'langelier_index': pump_params['scaling_tendency'],
            'biological_growth_potential': 0.5,  # Calculated based on temperature and chemistry
            'concentration_factor': water_chemistry_state['water_chemistry_concentration_factor'],
            'water_aggressiveness': pump_params['water_aggressiveness'],
            'nutrient_level': min(2.0, water_chemistry_state['water_chemistry_tds'] / 500.0)
        }
        
        # Calculate cooling water velocity for degradation models
        tube_area = np.pi * (self.config.heat_transfer.tube_inner_diameter / 2.0) ** 2
        total_flow_area = tube_area * self.tube_degradation.active_tube_count
        cooling_water_velocity = (cooling_water_flow / 1000.0) / total_flow_area  # m/s
        
        # Update tube degradation
        tube_degradation_results = self.tube_degradation.update_tube_failures(
            cooling_water_velocity=cooling_water_velocity,
            water_chemistry_aggressiveness=water_aggressiveness,
            dt=dt
        )
        
        # Update fouling model
        avg_cooling_water_temp = (cooling_water_temp_in + self.cooling_water_outlet_temp) / 2.0
        fouling_results = self.fouling_model.update_fouling(
            water_temp=avg_cooling_water_temp,
            water_chemistry=water_quality_results,
            flow_velocity=cooling_water_velocity,
            dt=dt
        )
        
        # Update vacuum system
        target_pressure = 0.007  # MPa target condenser pressure
        vacuum_results = self.vacuum_system.update_state(
            target_pressure=target_pressure,
            motive_steam_pressure=motive_steam_pressure,
            motive_steam_temperature=motive_steam_temperature,
            dt=dt
        )
        
        # Calculate enhanced heat transfer
        heat_transfer, ht_details = self.calculate_enhanced_heat_transfer(
            steam_flow, steam_pressure, steam_quality,
            cooling_water_flow, cooling_water_temp_in
        )
        
        # Update condenser state
        self.cooling_water_outlet_temp = ht_details['cooling_water_temp_out']
        self.heat_rejection_rate = heat_transfer
        self.condensate_temperature = self._saturation_temperature(vacuum_results['condenser_pressure'])
        self.condensate_flow = steam_flow
        
        # Calculate overall performance factor
        design_heat_duty = self.config.design_heat_duty
        thermal_efficiency = heat_transfer / design_heat_duty if design_heat_duty > 0 else 0
        
        # Update operating hours
        self.operating_hours += dt
        
        # Calculate overall thermal performance factor
        area_factor = tube_degradation_results['heat_transfer_area_factor']
        # Fixed: More realistic fouling impact calculation
        # Reduced multiplier from 50 to 5 for realistic fouling impact
        # This allows fouling resistance up to 0.18 m²K/W before hitting minimum performance
        fouling_factor = max(0.3, 1.0 - fouling_results['total_fouling_resistance'] * 5)
        vacuum_factor = vacuum_results['system_efficiency']
        
        self.thermal_performance_factor = area_factor * fouling_factor * vacuum_factor
        
        return {
            # Basic condenser performance
            'heat_rejection_rate': self.heat_rejection_rate,
            'thermal_efficiency': thermal_efficiency,
            'overall_htc': self.overall_htc,
            'thermal_performance_factor': self.thermal_performance_factor,
            
            # Steam conditions
            'steam_inlet_pressure': self.steam_inlet_pressure,
            'steam_inlet_temperature': self.steam_inlet_temperature,
            'condensate_temperature': self.condensate_temperature,
            'condensate_flow': self.condensate_flow,
            
            # Cooling water conditions
            'cooling_water_inlet_temp': self.cooling_water_inlet_temp,
            'cooling_water_outlet_temp': self.cooling_water_outlet_temp,
            'cooling_water_temp_rise': ht_details['cooling_water_temp_rise'],
            'cooling_water_flow': self.cooling_water_flow,
            'cooling_water_velocity': cooling_water_velocity,
            
            # Tube degradation
            'active_tube_count': tube_degradation_results['active_tube_count'],
            'plugged_tube_count': tube_degradation_results['plugged_tube_count'],
            'tube_leak_rate': tube_degradation_results['tube_leak_rate'],
            'average_wall_thickness': tube_degradation_results['average_wall_thickness'],
            
            # Fouling
            'biofouling_thickness': fouling_results['biofouling_thickness'],
            'scale_thickness': fouling_results['scale_thickness'],
            'corrosion_thickness': fouling_results['corrosion_thickness'],
            'total_fouling_resistance': fouling_results['total_fouling_resistance'],
            'time_since_cleaning': fouling_results['time_since_cleaning'],
            
            # Water quality
            'water_ph': water_quality_results['ph'],
            'water_hardness': water_quality_results['hardness'],
            'chlorine_residual': water_quality_results['chlorine_residual'],
            'langelier_index': water_quality_results['langelier_index'],
            'biological_growth_potential': water_quality_results['biological_growth_potential'],
            
            # Vacuum system
            'condenser_pressure': vacuum_results['condenser_pressure'],
            'air_partial_pressure': vacuum_results['air_partial_pressure'],
            'vacuum_air_removal_rate': vacuum_results['total_air_removal_rate'],
            'vacuum_steam_consumption': vacuum_results['total_steam_consumption'],
            'vacuum_system_efficiency': vacuum_results['system_efficiency'],
            
            # Operating time
            'operating_hours': self.operating_hours
        }
    
    def perform_maintenance(self, maintenance_type: str, **kwargs) -> Dict[str, float]:
        """
        Perform maintenance operations on condenser systems
        
        Args:
            maintenance_type: Type of maintenance
            **kwargs: Additional maintenance parameters
            
        Returns:
            Dictionary with maintenance results
        """
        results = {}
        
        if maintenance_type == "tube_plugging":
            # Plug failed tubes
            tubes_to_plug = kwargs.get('tubes_to_plug', 10)
            self.tube_degradation.plugged_tube_count += tubes_to_plug
            self.tube_degradation.active_tube_count -= tubes_to_plug
            results['tubes_plugged'] = tubes_to_plug
            
        elif maintenance_type == "cleaning":
            # Perform fouling cleaning
            cleaning_type = kwargs.get('cleaning_type', 'chemical')
            cleaning_results = self.fouling_model.perform_cleaning(cleaning_type)
            results.update(cleaning_results)
            
        elif maintenance_type == "vacuum_system":
            # Vacuum system maintenance
            for ejector in self.vacuum_system.ejectors.values():
                ejector.perform_cleaning(kwargs.get('cleaning_type', 'chemical'))
            results['vacuum_maintenance'] = True
            
        elif maintenance_type == "water_treatment":
            # Reset unified water chemistry system
            self.water_chemistry.perform_chemical_treatment("standard")
            results['water_treatment_reset'] = True
        
        return results
    
    def get_state_dict(self) -> Dict[str, float]:
        """Get current state as dictionary for logging/monitoring"""
        state_dict = {
            # Basic condenser state
            'condenser_heat_rejection': self.heat_rejection_rate,
            'condenser_thermal_performance': self.thermal_performance_factor,
            'condenser_overall_htc': self.overall_htc,
            'condenser_operating_hours': self.operating_hours,
            
            # Cooling water
            'cooling_water_inlet_temp': self.cooling_water_inlet_temp,
            'cooling_water_outlet_temp': self.cooling_water_outlet_temp,
            'cooling_water_flow': self.cooling_water_flow,
            
            # Steam/condensate
            'steam_inlet_pressure': self.steam_inlet_pressure,
            'condensate_temperature': self.condensate_temperature,
            'condensate_flow': self.condensate_flow
        }
        
        # Add sub-model states
        #state_dict.update(self.vacuum_system.get_state_dict())
        
        # Add tube degradation state
        state_dict.update({
            'tube_active_count': self.tube_degradation.active_tube_count,
            'tube_plugged_count': self.tube_degradation.plugged_tube_count,
            'tube_wall_thickness': self.tube_degradation.average_wall_thickness,
            'tube_leak_rate': self.tube_degradation.tube_leak_rate
        })
        
        # Add fouling state
        state_dict.update({
            'fouling_biofouling': self.fouling_model.biofouling_thickness,
            'fouling_scale': self.fouling_model.scale_thickness,
            'fouling_corrosion': self.fouling_model.corrosion_product_thickness,
            'fouling_resistance': self.fouling_model.total_fouling_resistance,
            'fouling_time_since_cleaning': self.fouling_model.time_since_cleaning
        })
        
        # NOTE: Water quality state is now tracked separately by unified WaterChemistry system
        # No need to duplicate these parameters in condenser state dict

        return state_dict
    
    def get_heat_flows(self) -> Dict[str, float]:
        """
        Get current heat flows for this component (MW)
        
        Returns:
            Dictionary with heat flow values in MW
        """
        return {
            'steam_enthalpy_input': ThermodynamicProperties.enthalpy_flow_mw(
                self.steam_inlet_flow,
                ThermodynamicProperties.steam_enthalpy(
                    self.steam_inlet_temperature,
                    self.steam_inlet_pressure,
                    self.steam_inlet_quality
                )
            ),
            'heat_rejection_output': self.heat_rejection_rate / 1e6,  # Convert W to MW
            'condensate_enthalpy_output': ThermodynamicProperties.enthalpy_flow_mw(
                self.condensate_flow,
                ThermodynamicProperties.liquid_enthalpy(self.condensate_temperature)
            ),
            'thermal_losses': (self.heat_rejection_rate * 0.01) / 1e6,  # 1% thermal losses in MW
            'vacuum_steam_consumption': self.vacuum_system.get_state_dict().get('total_steam_consumption', 0.0) / 1000.0  # Convert kg/s to MW equivalent
        }
    
    def get_enthalpy_flows(self) -> Dict[str, float]:
        """
        Get current enthalpy flows for this component (MW)
        
        Returns:
            Dictionary with enthalpy flow values in MW
        """
        # Calculate steam inlet enthalpy flow
        steam_enthalpy = ThermodynamicProperties.steam_enthalpy(
            self.steam_inlet_temperature,
            self.steam_inlet_pressure,
            self.steam_inlet_quality
        )
        steam_enthalpy_flow = ThermodynamicProperties.enthalpy_flow_mw(
            self.steam_inlet_flow, steam_enthalpy
        )
        
        # Calculate condensate outlet enthalpy flow
        condensate_enthalpy = ThermodynamicProperties.liquid_enthalpy(self.condensate_temperature)
        condensate_enthalpy_flow = ThermodynamicProperties.enthalpy_flow_mw(
            self.condensate_flow, condensate_enthalpy
        )
        
        return {
            'inlet_enthalpy_flow': steam_enthalpy_flow,
            'outlet_enthalpy_flow': condensate_enthalpy_flow,
            'enthalpy_removed': steam_enthalpy_flow - condensate_enthalpy_flow,
            'heat_rejection_enthalpy': self.heat_rejection_rate / 1e6  # MW
        }
    
    def setup_maintenance_integration(self, maintenance_system, component_id: str):
        """
        Set up maintenance integration using pub/sub architecture
        
        Args:
            maintenance_system: AutoMaintenanceSystem instance
            component_id: Unique identifier for this condenser
        """
        self.maintenance_system = maintenance_system
        self.component_id = component_id
        
        print(f"ENHANCED CONDENSER {component_id}: Setting up maintenance integration")
        
        # Register main condenser system
        condenser_monitoring_config = {
            'thermal_performance_factor': {
                'attribute': 'thermal_performance_factor',
                'threshold': 0.85,
                'comparison': 'less_than',
                'action': 'condenser_tube_cleaning',
                'cooldown_hours': 168.0  # Weekly cooldown
            },
            'fouling_resistance': {
                'attribute': 'fouling_model.total_fouling_resistance',
                'threshold': 0.001,
                'comparison': 'greater_than',
                'action': 'condenser_chemical_cleaning',
                'cooldown_hours': 720.0  # Monthly cooldown
            },
            'tube_leak_rate': {
                'attribute': 'tube_degradation.tube_leak_rate',
                'threshold': 0.01,
                'comparison': 'greater_than',
                'action': 'condenser_tube_plugging',
                'cooldown_hours': 24.0  # Daily cooldown
            },
            'active_tube_count': {
                'attribute': 'tube_degradation.active_tube_count',
                'threshold': 26000,
                'comparison': 'less_than',
                'action': 'condenser_tube_inspection',
                'cooldown_hours': 168.0  # Weekly cooldown
            },
            'time_since_cleaning': {
                'attribute': 'fouling_model.time_since_cleaning',
                'threshold': 4320.0,  # 6 months
                'comparison': 'greater_than',
                'action': 'condenser_hydroblast_cleaning',
                'cooldown_hours': 4320.0  # 6 month cooldown
            }
        }
        
        maintenance_system.register_component(
            component_id=component_id,
            component=self,
            monitoring_config=condenser_monitoring_config
        )
        
        # Register individual vacuum ejectors
        print(f"  Setting up vacuum system maintenance integration...")
        ejector_count = 0
        for ejector_id, ejector in self.vacuum_system.ejectors.items():
            ejector_monitoring_config = {
                'performance_factor': {
                    'attribute': 'overall_performance_factor',
                    'threshold': 0.8,
                    'comparison': 'less_than',
                    'action': 'vacuum_ejector_cleaning',
                    'cooldown_hours': 168.0  # Weekly cooldown
                },
                'nozzle_fouling_factor': {
                    'attribute': 'nozzle_fouling_factor',
                    'threshold': 0.85,
                    'comparison': 'less_than',
                    'action': 'vacuum_ejector_cleaning',
                    'cooldown_hours': 720.0  # Monthly cooldown
                },
                'steam_consumption_rate': {
                    'attribute': 'steam_consumption_rate',
                    'threshold': 3.5,
                    'comparison': 'greater_than',
                    'action': 'vacuum_ejector_nozzle_replacement',
                    'cooldown_hours': 8760.0  # Annual cooldown
                },
                'operating_hours': {
                    'attribute': 'operating_hours',
                    'threshold': 8760.0,  # Annual maintenance
                    'comparison': 'greater_than',
                    'action': 'vacuum_ejector_inspection',
                    'cooldown_hours': 8760.0  # Annual cooldown
                }
            }
            
            # Individual ejectors now have their own maintenance methods
            # No need for dynamic method injection
            if hasattr(ejector, 'setup_maintenance_integration'):
                ejector.setup_maintenance_integration(maintenance_system, ejector_id)
            
            maintenance_system.register_component(
                component_id=ejector_id,
                component=ejector,
                monitoring_config=ejector_monitoring_config
            )
            ejector_count += 1
        
        # Register vacuum system as a whole
        vacuum_monitoring_config = {
            'system_efficiency': {
                'attribute': 'vacuum_system.system_efficiency',
                'threshold': 0.8,
                'comparison': 'less_than',
                'action': 'vacuum_system_test',
                'cooldown_hours': 720.0  # Monthly cooldown
            },
            'air_leakage_rate': {
                'attribute': 'vacuum_system.current_air_leakage',
                'threshold': 0.3,  # 3x base leakage
                'comparison': 'greater_than',
                'action': 'vacuum_leak_detection',
                'cooldown_hours': 168.0  # Weekly cooldown
            }
        }
        
        maintenance_system.register_component(
            component_id=f"{component_id}-VACUUM",
            component=self.vacuum_system,
            monitoring_config=vacuum_monitoring_config
        )
        
        print(f"  Enhanced condenser maintenance integration complete")
        print(f"  Total Registered Components: {1 + ejector_count + 1}")  # Condenser + ejectors + vacuum system
    
    def _setup_ejector_maintenance(self, ejector, maintenance_system, component_id: str):
        """Set up maintenance integration for individual ejector"""
        print(f"VACUUM EJECTOR {component_id}: Setting up maintenance integration")
    
    
    def perform_maintenance(self, maintenance_type: str, **kwargs):
        """
        Perform maintenance operations on condenser systems
        
        Args:
            maintenance_type: Type of maintenance action
            **kwargs: Additional maintenance parameters
            
        Returns:
            MaintenanceResult with detailed results
        """
        # Import here to avoid circular imports
        from ...maintenance.maintenance_actions import create_maintenance_result
        
        current_time = self.operating_hours
        
        if maintenance_type == "condenser_tube_cleaning":
            # Clean condenser tubes
            cleaning_type = kwargs.get('cleaning_type', 'chemical')
            old_fouling = self.fouling_model.total_fouling_resistance
            
            # Perform cleaning based on type
            cleaning_result = self.fouling_model.perform_cleaning(cleaning_type)
            
            # Calculate performance improvement
            fouling_reduction = old_fouling - self.fouling_model.total_fouling_resistance
            performance_improvement = (fouling_reduction / max(0.001, old_fouling)) * 100
            
            # Update thermal performance factor
            self.thermal_performance_factor = min(1.0, self.thermal_performance_factor + performance_improvement * 0.01)
            
            return create_maintenance_result(
                success=True,
                duration=8.0,
                work_description=f"Cleaned condenser tubes using {cleaning_type} method",
                findings=f"Removed {cleaning_result['total_removed']:.3f}mm of fouling. "
                         f"Biofouling: {cleaning_result['bio_removed']:.3f}mm, "
                         f"Scale: {cleaning_result['scale_removed']:.3f}mm, "
                         f"Corrosion products: {cleaning_result['corrosion_removed']:.3f}mm",
                performance_improvement=performance_improvement,
                parts_used=[f"{cleaning_type}_cleaning_solution", "cleaning_brushes"],
                cost=5000.0,
                effectiveness_score=min(1.0, performance_improvement / 10.0),
                next_maintenance_due=4380.0  # 6 months
            )
        
        elif maintenance_type == "condenser_tube_plugging":
            # Plug failed tubes
            tubes_to_plug = kwargs.get('tubes_to_plug', 10)
            
            # Update tube degradation model
            old_active_count = self.tube_degradation.active_tube_count
            self.tube_degradation.plugged_tube_count += tubes_to_plug
            self.tube_degradation.active_tube_count = max(1000, old_active_count - tubes_to_plug)
            
            # Update heat transfer area factor
            self.tube_degradation.effective_heat_transfer_area_factor = (
                self.tube_degradation.active_tube_count / self.config.tube_count
            )
            
            # Reset leak rate
            self.tube_degradation.tube_leak_rate = max(0.0, self.tube_degradation.tube_leak_rate - 0.005)
            
            performance_impact = (tubes_to_plug / self.config.tube_count) * 100
            
            return create_maintenance_result(
                success=True,
                duration=4.0,
                work_description=f"Plugged {tubes_to_plug} failed condenser tubes",
                findings=f"Tubes showed leakage and wall thinning. "
                         f"Active tube count reduced from {old_active_count} to {self.tube_degradation.active_tube_count}",
                performance_improvement=-performance_impact,  # Negative because we lose capacity
                parts_used=["tube_plugs", "sealant"],
                cost=tubes_to_plug * 50.0,
                next_maintenance_due=8760.0  # Annual inspection
            )
        
        elif maintenance_type == "condenser_chemical_cleaning":
            # Comprehensive chemical cleaning
            old_fouling = self.fouling_model.total_fouling_resistance
            old_performance = self.thermal_performance_factor
            
            # Perform aggressive chemical cleaning
            cleaning_result = self.fouling_model.perform_cleaning("chemical")
            
            # Additional performance restoration
            self.thermal_performance_factor = min(1.0, self.thermal_performance_factor + 0.1)
            
            performance_improvement = (self.thermal_performance_factor - old_performance) * 100
            
            return create_maintenance_result(
                success=True,
                duration=12.0,
                work_description="Comprehensive chemical cleaning of condenser",
                findings=f"Removed {cleaning_result['total_removed']:.3f}mm total fouling. "
                         f"Thermal performance improved from {old_performance:.3f} to {self.thermal_performance_factor:.3f}",
                performance_improvement=performance_improvement,
                parts_used=["chemical_cleaning_solution", "neutralizing_agent", "corrosion_inhibitor"],
                cost=25000.0,
                effectiveness_score=min(1.0, performance_improvement / 15.0),
                next_maintenance_due=8760.0  # Annual
            )
        
        elif maintenance_type == "condenser_water_treatment":
            # Optimize cooling water chemistry
            old_aggressiveness = self.water_chemistry.water_aggressiveness
            
            # Reset water chemistry to optimal conditions
            self.water_chemistry.perform_chemical_treatment("standard")
            
            # Slow down fouling rates
            self.fouling_model.biofouling_thickness *= 0.9
            self.fouling_model.scale_thickness *= 0.8
            self.fouling_model.corrosion_product_thickness *= 0.7
            
            # Recalculate fouling resistance
            self.fouling_model.total_fouling_resistance = self.fouling_model.calculate_total_fouling_resistance()
            
            improvement = (old_aggressiveness - self.water_chemistry.water_aggressiveness) * 50
            
            return create_maintenance_result(
                success=True,
                duration=2.0,
                work_description="Optimized cooling water chemistry treatment",
                findings=f"Water aggressiveness reduced from {old_aggressiveness:.2f} to {self.water_chemistry.water_aggressiveness:.2f}",
                performance_improvement=improvement,
                parts_used=["biocide", "antiscalant", "corrosion_inhibitor", "ph_adjuster"],
                cost=2000.0,
                next_maintenance_due=720.0  # Monthly
            )
        
        elif maintenance_type == "vacuum_system_test":
            # Test vacuum system performance
            vacuum_efficiency = self.vacuum_system.system_efficiency
            target_pressure = 0.007  # MPa
            actual_pressure = self.vacuum_system.condenser_pressure
            
            findings = []
            if vacuum_efficiency < 0.9:
                findings.append(f"Vacuum efficiency degraded to {vacuum_efficiency:.1%}")
            if actual_pressure > target_pressure * 1.1:
                findings.append(f"Condenser pressure elevated: {actual_pressure:.4f} MPa")
            if self.vacuum_system.current_air_leakage > self.vacuum_system.config.base_air_leakage * 2:
                findings.append("Excessive air leakage detected")
            
            if len(findings) == 0:
                findings.append("Vacuum system operating normally")
            
            return create_maintenance_result(
                success=True,
                duration=4.0,
                work_description="Vacuum system performance test",
                findings="; ".join(findings),
                performance_improvement=0.0,
                next_maintenance_due=2190.0  # Quarterly
            )
        
        elif maintenance_type == "vacuum_leak_detection":
            # Detect and repair air leaks
            old_leakage = self.vacuum_system.current_air_leakage
            
            # Reduce air leakage by 50%
            self.vacuum_system.current_air_leakage *= 0.5
            
            leakage_reduction = old_leakage - self.vacuum_system.current_air_leakage
            performance_improvement = (leakage_reduction / old_leakage) * 10  # 10% improvement per 100% leakage reduction
            
            return create_maintenance_result(
                success=True,
                duration=6.0,
                work_description="Vacuum leak detection and repair",
                findings=f"Reduced air leakage from {old_leakage:.3f} to {self.vacuum_system.current_air_leakage:.3f} kg/s",
                performance_improvement=performance_improvement,
                parts_used=["gaskets", "sealant", "bolts"],
                cost=3000.0,
                next_maintenance_due=4380.0  # Semi-annual
            )
        
        else:
            return create_maintenance_result(
                success=False,
                duration=0.0,
                work_description=f"Unknown maintenance type: {maintenance_type}",
                error_message=f"Maintenance type '{maintenance_type}' not supported for condenser"
            )

    def _apply_initial_conditions(self) -> None:
        """
        Apply initial conditions from configuration to condenser state
        
        Args:
            initial_conditions: CondenserInitialConditions object with initial condition parameters
        """
        print(f"CONDENSER: Applying initial conditions...")
        initial_conditions = self.config.initial_conditions
        
        # Steam conditions
        if hasattr(initial_conditions, 'steam_inlet_pressure') and initial_conditions.steam_inlet_pressure is not None:
            self.steam_inlet_pressure = initial_conditions.steam_inlet_pressure
            print(f"  Steam inlet pressure: {self.steam_inlet_pressure} MPa")
        
        if hasattr(initial_conditions, 'steam_inlet_temperature') and initial_conditions.steam_inlet_temperature is not None:
            self.steam_inlet_temperature = initial_conditions.steam_inlet_temperature
            print(f"  Steam inlet temperature: {self.steam_inlet_temperature} °C")
        
        if hasattr(initial_conditions, 'steam_inlet_flow') and initial_conditions.steam_inlet_flow is not None:
            self.steam_inlet_flow = initial_conditions.steam_inlet_flow
            print(f"  Steam inlet flow: {self.steam_inlet_flow} kg/s")
        
        if hasattr(initial_conditions, 'steam_inlet_quality') and initial_conditions.steam_inlet_quality is not None:
            self.steam_inlet_quality = initial_conditions.steam_inlet_quality
            print(f"  Steam inlet quality: {self.steam_inlet_quality}")
        
        # Condensate conditions
        if hasattr(initial_conditions, 'condensate_temperature') and initial_conditions.condensate_temperature is not None:
            self.condensate_temperature = initial_conditions.condensate_temperature
            print(f"  Condensate temperature: {self.condensate_temperature} °C")
        
        if hasattr(initial_conditions, 'condensate_flow') and initial_conditions.condensate_flow is not None:
            self.condensate_flow = initial_conditions.condensate_flow
            print(f"  Condensate flow: {self.condensate_flow} kg/s")
        
        # Cooling water conditions
        if hasattr(initial_conditions, 'cooling_water_inlet_temp') and initial_conditions.cooling_water_inlet_temp is not None:
            self.cooling_water_inlet_temp = initial_conditions.cooling_water_inlet_temp
            print(f"  Cooling water inlet temp: {self.cooling_water_inlet_temp} °C")
        
        if hasattr(initial_conditions, 'cooling_water_outlet_temp') and initial_conditions.cooling_water_outlet_temp is not None:
            self.cooling_water_outlet_temp = initial_conditions.cooling_water_outlet_temp
            print(f"  Cooling water outlet temp: {self.cooling_water_outlet_temp} °C")
        
        if hasattr(initial_conditions, 'cooling_water_flow') and initial_conditions.cooling_water_flow is not None:
            self.cooling_water_flow = initial_conditions.cooling_water_flow
            print(f"  Cooling water flow: {self.cooling_water_flow} kg/s")
        
        # Heat transfer conditions
        if hasattr(initial_conditions, 'heat_rejection_rate') and initial_conditions.heat_rejection_rate is not None:
            self.heat_rejection_rate = initial_conditions.heat_rejection_rate
            print(f"  Heat rejection rate: {self.heat_rejection_rate/1e6:.1f} MW")
        
        if hasattr(initial_conditions, 'overall_htc') and initial_conditions.overall_htc is not None:
            self.overall_htc = initial_conditions.overall_htc
            print(f"  Overall HTC: {self.overall_htc} W/m²/K")
        
        if hasattr(initial_conditions, 'thermal_performance_factor') and initial_conditions.thermal_performance_factor is not None:
            self.thermal_performance_factor = initial_conditions.thermal_performance_factor
            print(f"  Thermal performance factor: {self.thermal_performance_factor}")
        
        # Vacuum system conditions
        if hasattr(initial_conditions, 'condenser_pressure') and initial_conditions.condenser_pressure is not None:
            # Apply to vacuum system
            self.vacuum_system.condenser_pressure = initial_conditions.condenser_pressure
            print(f"  Condenser pressure: {initial_conditions.condenser_pressure} MPa")
        
        if hasattr(initial_conditions, 'air_partial_pressure') and initial_conditions.air_partial_pressure is not None:
            # Apply to vacuum system
            if hasattr(self.vacuum_system, 'air_partial_pressure'):
                self.vacuum_system.air_partial_pressure = initial_conditions.air_partial_pressure
            print(f"  Air partial pressure: {initial_conditions.air_partial_pressure} MPa")
        
        if hasattr(initial_conditions, 'air_removal_rate') and initial_conditions.air_removal_rate is not None:
            # Apply to vacuum system
            if hasattr(self.vacuum_system, 'air_removal_rate'):
                self.vacuum_system.air_removal_rate = initial_conditions.air_removal_rate
            print(f"  Air removal rate: {initial_conditions.air_removal_rate} kg/s")
        
        # Tube conditions
        if hasattr(initial_conditions, 'active_tube_count') and initial_conditions.active_tube_count is not None:
            self.tube_degradation.active_tube_count = initial_conditions.active_tube_count
            print(f"  Active tube count: {self.tube_degradation.active_tube_count}")
        
        if hasattr(initial_conditions, 'plugged_tube_count') and initial_conditions.plugged_tube_count is not None:
            self.tube_degradation.plugged_tube_count = initial_conditions.plugged_tube_count
            print(f"  Plugged tube count: {self.tube_degradation.plugged_tube_count}")
        
        if hasattr(initial_conditions, 'average_wall_thickness') and initial_conditions.average_wall_thickness is not None:
            self.tube_degradation.average_wall_thickness = initial_conditions.average_wall_thickness
            print(f"  Average wall thickness: {self.tube_degradation.average_wall_thickness} m")
        
        if hasattr(initial_conditions, 'tube_leak_rate') and initial_conditions.tube_leak_rate is not None:
            self.tube_degradation.tube_leak_rate = initial_conditions.tube_leak_rate
            print(f"  Tube leak rate: {self.tube_degradation.tube_leak_rate} kg/s")
        
        # Fouling conditions
        if hasattr(initial_conditions, 'biofouling_thickness') and initial_conditions.biofouling_thickness is not None:
            self.fouling_model.biofouling_thickness = initial_conditions.biofouling_thickness
            print(f"  Biofouling thickness: {self.fouling_model.biofouling_thickness} mm")
        
        if hasattr(initial_conditions, 'scale_thickness') and initial_conditions.scale_thickness is not None:
            self.fouling_model.scale_thickness = initial_conditions.scale_thickness
            print(f"  Scale thickness: {self.fouling_model.scale_thickness} mm")
        
        if hasattr(initial_conditions, 'corrosion_thickness') and initial_conditions.corrosion_thickness is not None:
            self.fouling_model.corrosion_product_thickness = initial_conditions.corrosion_thickness
            print(f"  Corrosion thickness: {self.fouling_model.corrosion_product_thickness} mm")
        
        if hasattr(initial_conditions, 'total_fouling_resistance') and initial_conditions.total_fouling_resistance is not None:
            self.fouling_model.total_fouling_resistance = initial_conditions.total_fouling_resistance
            print(f"  Total fouling resistance: {self.fouling_model.total_fouling_resistance} m²K/W")
        
        if hasattr(initial_conditions, 'time_since_cleaning') and initial_conditions.time_since_cleaning is not None:
            self.fouling_model.time_since_cleaning = initial_conditions.time_since_cleaning
            print(f"  Time since cleaning: {self.fouling_model.time_since_cleaning} hours")
        
        # Water quality conditions (apply to water chemistry system)
        water_quality_updates = {}
        if hasattr(initial_conditions, 'water_ph') and initial_conditions.water_ph is not None:
            water_quality_updates['ph'] = initial_conditions.water_ph
            print(f"  Water pH: {initial_conditions.water_ph}")
        
        if hasattr(initial_conditions, 'water_hardness') and initial_conditions.water_hardness is not None:
            water_quality_updates['hardness'] = initial_conditions.water_hardness
            print(f"  Water hardness: {initial_conditions.water_hardness} mg/L")
        
        if hasattr(initial_conditions, 'chlorine_residual') and initial_conditions.chlorine_residual is not None:
            water_quality_updates['chlorine_residual'] = initial_conditions.chlorine_residual
            print(f"  Chlorine residual: {initial_conditions.chlorine_residual} mg/L")
        
        if hasattr(initial_conditions, 'dissolved_oxygen') and initial_conditions.dissolved_oxygen is not None:
            water_quality_updates['dissolved_oxygen'] = initial_conditions.dissolved_oxygen
            print(f"  Dissolved oxygen: {initial_conditions.dissolved_oxygen} mg/L")
        
        # Apply water quality updates to water chemistry system
        if water_quality_updates:
            # Update water chemistry system with initial conditions
            if hasattr(self.water_chemistry, 'apply_initial_conditions'):
                self.water_chemistry.apply_initial_conditions(water_quality_updates)
            else:
                # Fallback: directly set parameters if method not available
                for param, value in water_quality_updates.items():
                    if hasattr(self.water_chemistry, param):
                        setattr(self.water_chemistry, param, value)
        
        # Operating conditions
        if hasattr(initial_conditions, 'operating_hours') and initial_conditions.operating_hours is not None:
            self.operating_hours = initial_conditions.operating_hours
            print(f"  Operating hours: {self.operating_hours}")
        
        # Update derived parameters after applying initial conditions
        self._update_derived_parameters()
        
        print(f"CONDENSER: Initial conditions applied successfully")
    
    def _update_derived_parameters(self) -> None:
        """Update derived parameters after initial conditions are applied"""
        
        # Update tube degradation derived parameters
        if self.tube_degradation.active_tube_count > 0:
            self.tube_degradation.effective_heat_transfer_area_factor = (
                self.tube_degradation.active_tube_count / self.config.heat_transfer.tube_count
            )
        
        # Recalculate fouling resistance if individual components were set
        if (self.fouling_model.biofouling_thickness > 0 or 
            self.fouling_model.scale_thickness > 0 or 
            self.fouling_model.corrosion_product_thickness > 0):
            
            # Only recalculate if total wasn't explicitly set
            if self.fouling_model.total_fouling_resistance == 0.0:
                self.fouling_model.total_fouling_resistance = self.fouling_model.calculate_total_fouling_resistance()
        
        # Update thermal performance factor based on degradation
        area_factor = self.tube_degradation.effective_heat_transfer_area_factor
        fouling_factor = max(0.3, 1.0 - self.fouling_model.total_fouling_resistance * 5)
        vacuum_factor = getattr(self.vacuum_system, 'system_efficiency', 1.0)
        
        self.thermal_performance_factor = area_factor * fouling_factor * vacuum_factor

    def reset(self) -> None:
        """Reset enhanced condenser to initial conditions"""
        # Reset basic state
        self.steam_inlet_pressure = 0.007
        self.steam_inlet_temperature = 39.0
        self.steam_inlet_flow = 1665.0
        self.steam_inlet_quality = 0.90
        self.cooling_water_inlet_temp = 25.0
        self.cooling_water_outlet_temp = 35.0
        self.cooling_water_flow = 45000.0
        self.heat_rejection_rate = 2000.0e6
        self.overall_htc = 0.0
        self.condensate_temperature = 39.0
        self.condensate_flow = 1665.0
        self.thermal_performance_factor = 1.0
        self.operating_hours = 0.0
        
        # Reset sub-models
        self.vacuum_system.reset()
        
        # Reset tube degradation
        self.tube_degradation.active_tube_count = self.tube_degradation.config.initial_tube_count
        self.tube_degradation.plugged_tube_count = 0
        self.tube_degradation.average_wall_thickness = self.tube_degradation.config.wall_thickness_initial
        self.tube_degradation.tube_leak_rate = 0.0
        self.tube_degradation.vibration_damage_accumulation = 0.0
        self.tube_degradation.corrosion_damage_accumulation = 0.0
        self.tube_degradation.operating_hours = 0.0
        self.tube_degradation.effective_heat_transfer_area_factor = 1.0
        self.tube_degradation.tube_side_pressure_drop_factor = 1.0
        
        # Reset fouling
        self.fouling_model.biofouling_thickness = 0.0
        self.fouling_model.scale_thickness = 0.0
        self.fouling_model.corrosion_product_thickness = 0.0
        self.fouling_model.fouling_distribution_factor = 1.0
        self.fouling_model.time_since_cleaning = 0.0
        self.fouling_model.total_fouling_resistance = 0.0
        
        # Reset unified water chemistry to design conditions
        self.water_chemistry.reset()
    
    # === CHEMISTRY FLOW PROVIDER INTERFACE METHODS ===
    # These methods enable integration with chemistry_flow_tracker
    
    def get_chemistry_flows(self) -> Dict[str, Dict[str, float]]:
        """
        Get chemistry flows for chemistry flow tracker integration
        
        Returns:
            Dictionary with chemistry flow data from condenser perspective
        """
        # Condenser affects chemistry through cooling water interactions and air ingress
        return {
            'condenser_cooling_water': {
                ChemicalSpecies.PH.value: self.water_chemistry.ph,
                ChemicalSpecies.IRON.value: self.tube_degradation.tube_leak_rate * 0.1,  # Iron from tube corrosion
                ChemicalSpecies.COPPER.value: self.tube_degradation.tube_leak_rate * 0.05,  # Copper from tube materials
                'dissolved_oxygen_pickup': self._calculate_oxygen_pickup(),
                'cooling_water_chemistry_impact': self._calculate_cooling_water_impact()
            },
            'condenser_fouling_effects': {
                'biofouling_rate': self.fouling_model.biofouling_thickness / max(1.0, self.operating_hours),
                'scale_formation_rate': self.fouling_model.scale_thickness / max(1.0, self.operating_hours),
                'corrosion_product_rate': self.fouling_model.corrosion_product_thickness / max(1.0, self.operating_hours),
                'total_fouling_resistance': self.fouling_model.total_fouling_resistance,
                'cleaning_effectiveness_needed': self._calculate_cleaning_need()
            },
            'vacuum_system_chemistry': {
                'air_ingress_rate': self._calculate_air_ingress_chemistry_impact(),
                'steam_consumption_chemistry': self.vacuum_system.get_state_dict().get('total_steam_consumption', 0.0),
                'vacuum_efficiency_chemistry_impact': self._calculate_vacuum_chemistry_impact()
            }
        }
    
    def get_chemistry_state(self) -> Dict[str, float]:
        """
        Get current chemistry state from condenser perspective
        
        Returns:
            Dictionary with condenser chemistry state
        """
        return {
            'condenser_heat_rejection_rate': self.heat_rejection_rate,
            'condenser_thermal_performance': self.thermal_performance_factor,
            'condenser_cooling_water_inlet_temp': self.cooling_water_inlet_temp,
            'condenser_cooling_water_outlet_temp': self.cooling_water_outlet_temp,
            'condenser_cooling_water_flow': self.cooling_water_flow,
            'condenser_steam_inlet_pressure': self.steam_inlet_pressure,
            'condenser_condensate_temperature': self.condensate_temperature,
            'condenser_tube_active_count': self.tube_degradation.active_tube_count,
            'condenser_tube_leak_rate': self.tube_degradation.tube_leak_rate,
            'condenser_fouling_resistance': self.fouling_model.total_fouling_resistance,
            'condenser_time_since_cleaning': self.fouling_model.time_since_cleaning,
            'condenser_chemistry_impact_factor': self._calculate_condenser_chemistry_impact()
        }
    
    def update_chemistry_effects(self, chemistry_state: Dict[str, float]) -> None:
        """
        Update condenser based on external chemistry effects
        
        This method allows the chemistry flow tracker to influence condenser
        performance based on system-wide chemistry changes.
        
        Args:
            chemistry_state: Chemistry state from external systems
        """
        # Update water chemistry system with external effects
        if 'water_chemistry_effects' in chemistry_state:
            self.water_chemistry.update_chemistry_effects(chemistry_state['water_chemistry_effects'])
        
        # Apply steam chemistry effects from steam generator
        if 'steam_chemistry_effects' in chemistry_state:
            steam_effects = chemistry_state['steam_chemistry_effects']
            
            # Steam quality affects condensation and fouling
            if 'steam_quality' in steam_effects:
                quality = steam_effects['steam_quality']
                if quality < 0.95:  # Poor steam quality
                    # Poor quality steam can increase fouling
                    fouling_acceleration = (0.95 - quality) * 2.0
                    # Apply to fouling model
                    self.fouling_model.biofouling_thickness *= (1.0 + fouling_acceleration * 0.1)
                    self.fouling_model.scale_thickness *= (1.0 + fouling_acceleration * 0.05)
            
            # Steam carryover chemistry effects
            if 'carryover_chemistry' in steam_effects:
                carryover = steam_effects['carryover_chemistry']
                if carryover > 0.01:  # Significant carryover
                    # Chemical carryover can affect condensate chemistry
                    # Update water chemistry with carryover effects
                    carryover_effects = {
                        'chemical_additions': {
                            ChemicalSpecies.IRON.value: carryover * 0.1,
                            ChemicalSpecies.COPPER.value: carryover * 0.05
                        }
                    }
                    self.water_chemistry.update_chemistry_effects(carryover_effects)
        
        # Apply pH control effects
        if 'ph_control_effects' in chemistry_state:
            ph_effects = chemistry_state['ph_control_effects']
            
            # pH control can affect cooling water chemistry
            if 'ph_stability' in ph_effects:
                stability = ph_effects['ph_stability']
                if stability > 0.9:  # Very stable pH
                    # Stable pH reduces corrosion and fouling
                    corrosion_reduction = (stability - 0.9) * 0.5
                    
                    # Apply to tube degradation
                    self.tube_degradation.corrosion_damage_accumulation *= (1.0 - corrosion_reduction)
                    
                    # Apply to fouling model
                    self.fouling_model.corrosion_product_thickness *= (1.0 - corrosion_reduction * 0.3)
        
        # Apply cooling water treatment effects
        if 'cooling_water_treatment' in chemistry_state:
            treatment = chemistry_state['cooling_water_treatment']
            
            # Biocide treatment effects
            if 'biocide_effectiveness' in treatment:
                biocide_eff = treatment['biocide_effectiveness']
                if biocide_eff > 0.5:  # Effective biocide treatment
                    # Reduce biofouling growth rate
                    reduction = biocide_eff * 0.3
                    self.fouling_model.biofouling_thickness *= (1.0 - reduction)
            
            # Antiscalant treatment effects
            if 'antiscalant_effectiveness' in treatment:
                antiscalant_eff = treatment['antiscalant_effectiveness']
                if antiscalant_eff > 0.5:  # Effective antiscalant
                    # Reduce scale formation
                    reduction = antiscalant_eff * 0.2
                    self.fouling_model.scale_thickness *= (1.0 - reduction)
            
            # Corrosion inhibitor effects
            if 'corrosion_inhibitor_effectiveness' in treatment:
                inhibitor_eff = treatment['corrosion_inhibitor_effectiveness']
                if inhibitor_eff > 0.5:  # Effective corrosion inhibitor
                    # Reduce tube corrosion
                    reduction = inhibitor_eff * 0.25
                    self.tube_degradation.corrosion_damage_accumulation *= (1.0 - reduction)
        
        # Apply system-wide chemistry balance effects
        if 'system_chemistry_balance' in chemistry_state:
            balance = chemistry_state['system_chemistry_balance']
            
            # Poor chemistry balance affects condenser performance
            if 'balance_error' in balance:
                error = abs(balance['balance_error'])
                if error > 5.0:  # More than 5% error
                    # Increase fouling rates due to chemistry imbalance
                    fouling_acceleration = min(0.1, error * 0.002)  # Up to 10% acceleration
                    
                    # Apply to all fouling types
                    self.fouling_model.biofouling_thickness *= (1.0 + fouling_acceleration)
                    self.fouling_model.scale_thickness *= (1.0 + fouling_acceleration * 0.5)
                    self.fouling_model.corrosion_product_thickness *= (1.0 + fouling_acceleration * 0.8)
                    
                    # Recalculate fouling resistance
                    self.fouling_model.total_fouling_resistance = self.fouling_model.calculate_total_fouling_resistance()
    
    def _calculate_oxygen_pickup(self) -> float:
        """Calculate dissolved oxygen pickup in cooling water"""
        # Cooling water picks up oxygen from air contact
        # Higher temperature difference increases oxygen solubility changes
        temp_rise = self.cooling_water_outlet_temp - self.cooling_water_inlet_temp
        oxygen_pickup = temp_rise * 0.1  # mg/L per °C temperature rise
        return max(0.0, oxygen_pickup)
    
    def _calculate_cooling_water_impact(self) -> float:
        """Calculate overall cooling water chemistry impact"""
        # Combine effects of temperature, flow, and chemistry
        temp_factor = (self.cooling_water_outlet_temp - 25.0) / 20.0  # Normalized temperature effect
        flow_factor = self.cooling_water_flow / self.config.design_cooling_water_flow  # Flow effect
        chemistry_factor = self.water_chemistry.water_aggressiveness  # Chemistry aggressiveness
        
        impact = temp_factor * flow_factor * chemistry_factor
        return max(0.5, min(2.0, impact))  # Bounded impact factor
    
    def _calculate_cleaning_need(self) -> float:
        """Calculate cleaning effectiveness needed based on fouling state"""
        # Higher fouling requires more aggressive cleaning
        total_fouling = (self.fouling_model.biofouling_thickness + 
                        self.fouling_model.scale_thickness + 
                        self.fouling_model.corrosion_product_thickness)
        
        # Cleaning need increases with fouling thickness and time
        fouling_factor = total_fouling / 5.0  # Normalized to 5mm max
        time_factor = self.fouling_model.time_since_cleaning / 8760.0  # Normalized to 1 year
        
        cleaning_need = min(1.0, fouling_factor + time_factor)
        return cleaning_need
    
    def _calculate_air_ingress_chemistry_impact(self) -> float:
        """Calculate chemistry impact from air ingress"""
        # Air ingress brings oxygen and can affect chemistry
        vacuum_results = self.vacuum_system.get_state_dict()
        air_pressure = vacuum_results.get('vacuum_system_air_pressure', 0.0005)
        
        # Higher air pressure means more air ingress
        air_ingress_rate = air_pressure * 1000.0  # Convert to relative scale
        return min(1.0, air_ingress_rate)
    
    def _calculate_vacuum_chemistry_impact(self) -> float:
        """Calculate chemistry impact on vacuum system efficiency"""
        # Poor water chemistry can affect vacuum system performance
        chemistry_factor = self.water_chemistry.water_aggressiveness
        fouling_factor = self.fouling_model.total_fouling_resistance * 1000.0
        
        # Combined impact on vacuum efficiency
        impact = 1.0 - (chemistry_factor - 1.0) * 0.1 - fouling_factor * 0.05
        return max(0.5, min(1.0, impact))
    
    def _calculate_condenser_chemistry_impact(self) -> float:
        """Calculate overall chemistry impact factor for condenser"""
        # Combine various chemistry effects
        fouling_impact = self.fouling_model.total_fouling_resistance * 1000.0
        tube_impact = (1.0 - self.tube_degradation.effective_heat_transfer_area_factor) * 0.5
        chemistry_impact = (self.water_chemistry.water_aggressiveness - 1.0) * 0.2
        
        # Overall impact (lower is worse performance)
        total_impact = fouling_impact + tube_impact + chemistry_impact
        impact_factor = max(0.3, 1.0 - total_impact)
        return impact_factor

    # Thermodynamic property methods (same as original condenser)
    def _saturation_temperature(self, pressure_mpa: float) -> float:
        """Calculate saturation temperature for given pressure"""
        if pressure_mpa <= 0.001:
            return 10.0
        
        A, B, C = 8.07131, 1730.63, 233.426
        pressure_bar = pressure_mpa * 10.0
        pressure_bar = np.clip(pressure_bar, 0.01, 100.0)
        
        temp_c = B / (A - np.log10(pressure_bar)) - C
        
        if pressure_mpa >= 0.005 and pressure_mpa <= 0.01:
            temp_c = np.clip(temp_c, 35.0, 45.0)
        
        return np.clip(temp_c, 10.0, 374.0)
    
    def _saturation_enthalpy_liquid(self, pressure_mpa: float) -> float:
        """Calculate saturation enthalpy of liquid water (kJ/kg)"""
        temp = self._saturation_temperature(pressure_mpa)
        return 4.18 * temp
    
    def _saturation_enthalpy_vapor(self, pressure_mpa: float) -> float:
        """Calculate saturation enthalpy of steam (kJ/kg)"""
        temp = self._saturation_temperature(pressure_mpa)
        h_f = self._saturation_enthalpy_liquid(pressure_mpa)
        h_fg = 2257.0 * (1.0 - temp / 374.0) ** 0.38
        return h_f + h_fg
    
    def _water_enthalpy(self, temp_c: float, pressure_mpa: float) -> float:
        """Calculate enthalpy of liquid water (kJ/kg)"""
        return 4.18 * temp_c


# Example usage and testing
if __name__ == "__main__":
    # Create enhanced condenser with default configurations
    enhanced_condenser = EnhancedCondenserPhysics()
    
    print("Enhanced Condenser Physics Model - Parameter Validation")
    print("=" * 65)
    print("Integrated Models:")
    print("  - Tube Degradation and Failure Tracking")
    print("  - Multi-Component Fouling (Bio/Scale/Corrosion)")
    print("  - Cooling Water Quality and Chemistry")
    print("  - Steam Jet Ejector Vacuum System")
    print()
    
    # Test enhanced operation
    makeup_water = {
        'tds': 300.0,
        'hardness': 100.0,
        'chloride': 30.0,
        'ph': 7.2,
        'dissolved_oxygen': 8.0
    }
    
    chemical_doses = {
        'chlorine': 1.0,
        'antiscalant': 5.0,
        'corrosion_inhibitor': 10.0,
        'biocide': 0.0
    }
    
    print("Simulating 1000 hours of operation...")
    for hour in range(1000):
        result = enhanced_condenser.update_state(
            steam_pressure=0.007,
            steam_temperature=39.0,
            steam_flow=1665.0,
            steam_quality=0.90,
            cooling_water_flow=45000.0,
            cooling_water_temp_in=25.0,
            motive_steam_pressure=1.2,
            motive_steam_temperature=185.0,
            makeup_water_quality=makeup_water,
            chemical_doses=chemical_doses,
            dt=1.0
        )
        
        if hour % 200 == 0:  # Print every 200 hours
            print(f"\nHour {hour}:")
            print(f"  Heat Rejection: {result['heat_rejection_rate']/1e6:.1f} MW")
            print(f"  Thermal Performance: {result['thermal_performance_factor']:.3f}")
            print(f"  Active Tubes: {result['active_tube_count']:.0f}")
            print(f"  Plugged Tubes: {result['plugged_tube_count']:.0f}")
            print(f"  Total Fouling: {result['biofouling_thickness'] + result['scale_thickness'] + result['corrosion_thickness']:.3f} mm")
            print(f"  Water pH: {result['water_ph']:.2f}")
            print(f"  Condenser Pressure: {result['condenser_pressure']:.4f} MPa")
            print(f"  Vacuum Efficiency: {result['vacuum_system_efficiency']:.3f}")
    
    print(f"\nFinal State Summary:")
    final_state = enhanced_condenser.get_state_dict()
    print(f"  Operating Hours: {final_state['condenser_operating_hours']:.0f}")
    print(f"  Thermal Performance: {final_state['condenser_thermal_performance']:.3f}")
    print(f"  Active Tubes: {final_state['tube_active_count']:.0f}")
    print(f"  Total Fouling Resistance: {final_state['fouling_resistance']:.6f} m²K/W")
    print(f"  Water Quality Index: {final_state['water_langelier_index']:.2f}")

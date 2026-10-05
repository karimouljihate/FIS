"""Hydraulic engineering calculations.

Implements:
- Pipe diameter sizing from flow rate and velocity limits
- Hazen-Williams friction head loss
- Distributed outflow friction (drip lines)
- Pressure calculations
- Emitter/treerow flow demand
- Water source capacity checks

All calculations use SI units (meters, m³/s, m/s, bar).
"""
import math
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional


# === ENGINEERING DEFAULTS ===

ENGINEERING_DEFAULTS = {
    # Emitter properties
    'emitter_flow_lph': 4.0,          # Liters per hour per emitter
    'emitters_per_tree': 2,           # Drips per tree
    'emitter_spacing_m': 0.4,         # Spacing between emitters on drip line

    # Velocity limits (m/s)
    'main_max_velocity_mps': 2.0,
    'sub_max_velocity_mps': 1.8,
    'drip_max_velocity_mps': 1.2,

    # Hazen-Williams
    'hazen_williams_c': 140,          # C factor (PE pipe ~140-150)
    'minor_loss_factor': 0.15,        # Minor losses as fraction of friction loss

    # Pressure
    'min_emitter_pressure_bar': 1.0,  # Minimum pressure at emitter
    'max_zone_pressure_variation_pct': 20,  # Max pressure variation within a zone

    # Standard pipe diameters (mm) - commercially available
    'standard_diameters_mm': [16, 20, 25, 32, 40, 50, 63, 75, 90, 110, 125, 140, 160, 180, 200, 225, 250, 280, 315],

    # Water source
    'source_pressure_bar': 2.5,      # Available pressure at source
    'source_flow_lpm': 500,          # Available flow at source (L/min)

    # Drip line
    'drip_diameter_mm': 16,
}


@dataclass
class HydraulicCheck:
    """Result of a single hydraulic check."""
    code: str
    severity: str  # 'pass', 'warning', 'fail'
    object_type: str
    object_id: int
    message_en: str
    message_ar: str
    metrics: Dict = field(default_factory=dict)
    recommendation_en: str = ''
    recommendation_ar: str = ''


# === CORE HYDRAULIC FORMULAS ===

def flow_per_tree_lph(emitter_flow_lph, emitters_per_tree):
    """Q_tree = emitters_per_tree × emitter_flow_lph"""
    return emitters_per_tree * emitter_flow_lph


def tree_count_for_zone(project_id, zone_id):
    """Count trees in a zone."""
    from irrigation.extensions import mongo
    return mongo.db.trees.count_documents({'project_id': project_id, 'zone_id': zone_id})


def zone_flow_demand_m3s(tree_count, emitter_flow_lph, emitters_per_tree):
    """Calculate zone flow demand in m³/s.

    Q_zone = tree_count × Q_tree_lph / 1000 / 3600
    """
    q_tree = flow_per_tree_lph(emitter_flow_lph, emitters_per_tree)
    return tree_count * q_tree / 1000.0 / 3600.0


def sector_flow_demand_m3s(project_id, sector_id, emitter_flow_lph, emitters_per_tree):
    """Calculate sector flow demand (sum of all zone demands in sector)."""
    from irrigation.extensions import mongo
    zones = list(mongo.db.zones.find({'project_id': project_id, 'sector_id': sector_id}))
    total = 0.0
    for zone in zones:
        tc = tree_count_for_zone(project_id, zone['id'])
        total += zone_flow_demand_m3s(tc, emitter_flow_lph, emitters_per_tree)
    return total


def pipe_velocity_mps(flow_m3s, diameter_mm):
    """v = 4Q / (πD²)

    Where Q is in m³/s and D is in meters.
    """
    d_m = diameter_mm / 1000.0
    if d_m <= 0:
        return 0.0
    return (4.0 * flow_m3s) / (math.pi * d_m ** 2)


def hazen_williams_head_loss_m(flow_m3s, length_m, diameter_mm, c_factor=140):
    """Hazen-Williams friction head loss.

    hf = 10.67 × L × Q^1.852 / (C^1.852 × D^4.871)

    Where:
    - hf = head loss (meters)
    - L = pipe length (meters)
    - Q = flow rate (m³/s)
    - C = Hazen-Williams coefficient
    - D = internal diameter (meters)
    """
    d_m = diameter_mm / 1000.0
    if d_m <= 0 or flow_m3s <= 0 or length_m <= 0:
        return 0.0

    hf = (10.67 * length_m * flow_m3s ** 1.852) / (c_factor ** 1.852 * d_m ** 4.871)
    return hf


def distributed_friction_loss_m(full_flow_loss_m, emitter_count):
    """Approximate friction loss for uniformly distributed outflow.

    For drip lines with evenly spaced emitters:
    hf_distributed ≈ 0.36 × hf_full_flow (when many emitters)
    """
    if emitter_count <= 1:
        return full_flow_loss_m
    factor = 0.36  # Standard approximation for many emitters
    return factor * full_flow_loss_m


def pressure_bar_from_head_m(head_m):
    """Convert head (meters) to pressure (bar).

    P = head / 10.197
    """
    return head_m / 10.197


def head_m_from_pressure_bar(pressure_bar):
    """Convert pressure (bar) to head (meters)."""
    return pressure_bar * 10.197


def total_dynamic_head_m(friction_loss_m, elevation_diff_m=0, minor_loss_factor=0.15):
    """Total dynamic head = friction + elevation + minor losses."""
    minor_losses = friction_loss_m * minor_loss_factor
    return friction_loss_m + minor_losses + abs(elevation_diff_m)


def recommended_diameter_mm(flow_m3s, max_velocity_mps, standard_diameters):
    """Calculate the minimum standard pipe diameter that keeps velocity below max.

    D_min = sqrt(4Q / (π × v_max))

    Then round up to the next standard diameter.
    """
    if flow_m3s <= 0 or max_velocity_mps <= 0:
        return standard_diameters[0] if standard_diameters else 16

    d_min_m = math.sqrt((4.0 * flow_m3s) / (math.pi * max_velocity_mps))
    d_min_mm = d_min_m * 1000.0

    # Round up to next standard diameter
    for d in sorted(standard_diameters):
        if d >= d_min_mm:
            return d

    return max(standard_diameters) if standard_diameters else d_min_mm


# === PIPE LENGTH CALCULATIONS ===

def calculate_pipe_length_m(geom_geojson, lng, lat):
    """Calculate pipe length in meters using projected geometry."""
    from irrigation.engineering.projection import projected_shape

    if not geom_geojson:
        return 0.0

    try:
        shapely_geom = projected_shape(geom_geojson, lng, lat)
        return shapely_geom.length
    except Exception:
        from irrigation.engineering.projection import haversine_length_meters
        if geom_geojson.get('type') == 'LineString':
            return haversine_length_meters(geom_geojson['coordinates'])
        return 0.0


# === VALIDATION FUNCTIONS ===

def validate_pipe_velocity(checks, elem, flow_m3s, max_velocity, pipe_type, lng, lat, config):
    """Validate pipe velocity and recommend diameter."""
    diameter_mm = elem.get('properties', {}).get('diameter', 50)
    length_m = calculate_pipe_length_m(elem.get('geometry'), lng, lat)
    velocity = pipe_velocity_mps(flow_m3s, diameter_mm)

    recommended_d = recommended_diameter_mm(
        flow_m3s, max_velocity, config['standard_diameters_mm']
    )

    severity = 'pass'
    msg_en = f"{pipe_type}: velocity {velocity:.2f} m/s (limit {max_velocity} m/s)"
    msg_ar = f"{pipe_type}: السرعة {velocity:.2f} م/ث (الحد {max_velocity} م/ث)"

    rec_en = ''
    rec_ar = ''

    if velocity > max_velocity:
        severity = 'fail'
        rec_en = f"Pipe diameter too small. Recommended: {recommended_d}mm (current: {diameter_mm}mm). Increase diameter to reduce velocity."
        rec_ar = f"قطر الأنبوب صغير جداً. الموصى به: {recommended_d}مم (الحالي: {diameter_mm}مم). زيادة القطر لتقليل السرعة."
    elif velocity > max_velocity * 0.8:
        severity = 'warning'
        rec_en = f"Velocity approaching limit. Consider upgrading to {recommended_d}mm."
        rec_ar = f"السرعة تقترب من الحد. فكر في الترقية إلى {recommended_d}مم."

    checks.append(HydraulicCheck(
        code=f'{pipe_type.upper().replace(" ", "_")}_VELOCITY',
        severity=severity,
        object_type='network_element',
        object_id=elem['id'],
        message_en=msg_en,
        message_ar=msg_ar,
        metrics={
            'velocity_mps': round(velocity, 3),
            'max_velocity_mps': max_velocity,
            'diameter_mm': diameter_mm,
            'recommended_diameter_mm': recommended_d,
            'flow_m3s': round(flow_m3s, 6),
            'length_m': round(length_m, 2)
        },
        recommendation_en=rec_en,
        recommendation_ar=rec_ar
    ))


def validate_pressure_loss(checks, elem, flow_m3s, length_m, diameter_mm, pipe_type, min_pressure, config):
    """Validate pressure loss along a pipe."""
    c = config['hazen_williams_c']
    hf = hazen_williams_head_loss_m(flow_m3s, length_m, diameter_mm, c)
    minor_loss = hf * config['minor_loss_factor']
    total_loss = hf + minor_loss
    pressure_loss_bar = pressure_bar_from_head_m(total_loss)

    severity = 'pass'
    msg_en = f"{pipe_type}: pressure loss {pressure_loss_bar:.3f} bar over {length_m:.1f}m"
    msg_ar = f"{pipe_type}: فقد الضغط {pressure_loss_bar:.3f} بار على {length_m:.1f}م"

    rec_en = ''
    rec_ar = ''

    # Flag if pressure loss exceeds 20% of source pressure
    source_pressure = config['source_pressure_bar']
    if pressure_loss_bar > source_pressure * 0.2:
        severity = 'warning'
        rec_en = f"Pressure loss is {pressure_loss_bar:.3f} bar ({pressure_loss_bar/source_pressure*100:.1f}% of source). Consider larger diameter."
        rec_ar = f"فقد الضغط {pressure_loss_bar:.3f} بار ({pressure_loss_bar/source_pressure*100:.1f}٪ من المصدر). فكر في قطر أكبر."
    elif pressure_loss_bar > source_pressure * 0.5:
        severity = 'fail'
        rec_en = f"Excessive pressure loss. Pipe diameter must be increased."
        rec_ar = f"فقد ضغط مفرط. يجب زيادة قطر الأنبوب."

    checks.append(HydraulicCheck(
        code=f'{pipe_type.upper().replace(" ", "_")}_PRESSURE_LOSS',
        severity=severity,
        object_type='network_element',
        object_id=elem['id'],
        message_en=msg_en,
        message_ar=msg_ar,
        metrics={
            'head_loss_m': round(total_loss, 3),
            'friction_loss_m': round(hf, 3),
            'minor_loss_m': round(minor_loss, 3),
            'pressure_loss_bar': round(pressure_loss_bar, 3),
            'length_m': round(length_m, 2),
            'diameter_mm': diameter_mm,
            'c_factor': c
        },
        recommendation_en=rec_en,
        recommendation_ar=rec_ar
    ))


def validate_dripline(checks, elem, row, trees, config, lng, lat):
    """Validate drip line coverage: 2 drips per tree, along row."""
    emitter_flow = config['emitter_flow_lph']
    emitter_spacing = config['emitter_spacing_m']

    # Count trees on this row
    row_trees = [t for t in trees if t.get('row_id') == row.get('id')] if row else []
    tree_count = len(row_trees)

    # Check drips per tree
    drips_per_tree = config['emitters_per_tree']
    expected_drips = tree_count * drips_per_tree

    # Estimate emitter count from drip line length
    drip_length_m = calculate_pipe_length_m(elem.get('geometry'), lng, lat)
    estimated_emitters = max(1, int(drip_length_m / emitter_spacing)) if emitter_spacing > 0 else 0

    severity = 'pass'
    msg_en = f"DripLine: {estimated_emitters} emitters for {tree_count} trees ({drips_per_tree} drips/tree)"
    msg_ar = f"خط التنقيط: {estimated_emitters} قطارة لـ {tree_count} شجرة ({drips_per_tree} قطارة/شجرة)"

    rec_en = ''
    rec_ar = ''

    if estimated_emitters < expected_drips:
        severity = 'fail'
        rec_en = f"Insufficient emitters: {estimated_emitters} found, {expected_drips} needed ({tree_count} trees × {drips_per_tree} drips). Extend drip line."
        rec_ar = f"قطارات غير كافية: {estimated_emitters} موجود، {expected_drips} مطلوب ({tree_count} شجرة × {drips_per_tree} قطارة). مدد خط التنقيط."
    elif estimated_emitters < expected_drips * 1.1:
        severity = 'warning'
        rec_en = f"Emitter count close to minimum. Consider extending drip line."
        rec_ar = f"عدد القطرات قريب من الحد الأدنى. فكر في تمديد خط التنقيط."

    # Also check drip line velocity
    drip_flow_m3s = expected_drips * emitter_flow / 1000.0 / 3600.0
    drip_diameter = elem.get('properties', {}).get('diameter', config['drip_diameter_mm'])
    drip_velocity = pipe_velocity_mps(drip_flow_m3s, drip_diameter)

    if drip_velocity > config['drip_max_velocity_mps']:
        severity = 'fail' if severity != 'fail' else severity
        rec_en += f" Drip velocity {drip_velocity:.2f} m/s exceeds limit {config['drip_max_velocity_mps']} m/s."

    checks.append(HydraulicCheck(
        code='DRIPLINE_COVERAGE',
        severity=severity,
        object_type='network_element',
        object_id=elem['id'],
        message_en=msg_en,
        message_ar=msg_ar,
        metrics={
            'tree_count': tree_count,
            'expected_drips': expected_drips,
            'estimated_emitters': estimated_emitters,
            'drips_per_tree': drips_per_tree,
            'drip_line_length_m': round(drip_length_m, 2),
            'drip_velocity_mps': round(drip_velocity, 3),
            'drip_diameter_mm': drip_diameter
        },
        recommendation_en=rec_en,
        recommendation_ar=rec_ar
    ))


def validate_water_source_capacity(checks, project_id, total_flow_m3s, config):
    """Validate that water source can supply total demand."""
    source_flow_m3s = config['source_flow_lpm'] / 1000.0 / 60.0
    source_pressure = config['source_pressure_bar']

    severity = 'pass'
    msg_en = f"Water source: {config['source_flow_lpm']} L/min available, demand {total_flow_m3s*1000*60:.1f} L/min"
    msg_ar = f"مصدر المياه: {config['source_flow_lpm']} ل/دقيقة متاح، الطلب {total_flow_m3s*1000*60:.1f} ل/دقيقة"

    rec_en = ''
    rec_ar = ''

    if total_flow_m3s > source_flow_m3s:
        severity = 'fail'
        rec_en = f"Water source capacity insufficient. Demand ({total_flow_m3s*1000*60:.1f} L/min) exceeds supply ({config['source_flow_lpm']} L/min). Reduce zone count or increase source capacity."
        rec_ar = f"سعة مصدر المياه غير كافية. الطلب ({total_flow_m3s*1000*60:.1f} ل/دقيقة) يتجاوز الإمداد ({config['source_flow_lpm']} ل/دقيقة). قلل عدد المناطق أو زود سعة المصدر."
    elif total_flow_m3s > source_flow_m3s * 0.8:
        severity = 'warning'
        rec_en = f"Water demand approaching source capacity ({total_flow_m3s*1000*60:.1f}/{config['source_flow_lpm']} L/min)."
        rec_ar = f"طلب المياه يقترب من سعة المصدر ({total_flow_m3s*1000*60:.1f}/{config['source_flow_lpm']} ل/دقيقة)."

    checks.append(HydraulicCheck(
        code='WATER_SOURCE_CAPACITY',
        severity=severity,
        object_type='project',
        object_id=project_id,
        message_en=msg_en,
        message_ar=msg_ar,
        metrics={
            'source_flow_lpm': config['source_flow_lpm'],
            'source_pressure_bar': source_pressure,
            'total_demand_lpm': round(total_flow_m3s * 1000 * 60, 1),
            'total_demand_m3s': round(total_flow_m3s, 6),
            'utilization_pct': round(total_flow_m3s / source_flow_m3s * 100, 1) if source_flow_m3s > 0 else 0
        },
        recommendation_en=rec_en,
        recommendation_ar=rec_ar
    ))

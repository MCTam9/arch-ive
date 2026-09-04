-- arch-ive — controlled vocabularies
--
-- Idempotent: every insert is ON CONFLICT DO NOTHING, so this file can be
-- re-applied after every schema.sql re-apply, or safely re-run in CI.
-- Run after db/schema.sql:
--   psql "$DATABASE_URL" -f db/seed.sql
--
-- All names below are either public standards bodies (fine to name per
-- CONTRACT.md) or generic vocabulary terms invented for this corpus's
-- facets. No organisation, client or person name appears here.

-- RLS is FORCED even for this session; without an editor account every
-- insert below silently matches zero rows. ARCHIVE_ACCOUNT_ID from
-- CONTRACT.md is seeded as 'owner' by db/test_schema.sh / the live DB setup.
SELECT set_config('app.account_id', '00000000-0000-0000-0000-0000000000aa', false);

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────
-- unit — the units this corpus actually uses
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO unit (id, symbol, dimension)
VALUES
  ('kwh_m2_yr',   'kWh/m²·yr',        'energy_intensity'),
  ('kgco2e_m2_gia','kgCO2e/m²GIA',    'carbon_intensity'),
  ('kgco2e_kg',   'kgCO2e/kg',        'carbon_intensity_mass'),
  ('l_p_day',     'l/p/day',          'water_use_rate'),
  ('ug_m3',       'µg/m³',            'concentration'),
  ('ppm',         'ppm',              'concentration'),
  ('ppb',         'ppb',              'concentration'),
  ('mg_m3',       'mg/m³',            'concentration'),
  ('dba',         'dBA',              'sound_level'),
  ('seconds',     's',                'time'),
  ('lux',         'lux',              'illuminance'),
  ('pct',         '%',                'dimensionless'),
  ('dwellings_ha','dwellings/hectare','density'),
  ('degc',        '°C',               'temperature'),
  ('m',           'm',                'length'),
  ('m2',          'm²',               'area'),
  ('mwh',         'MWh',              'energy'),
  ('index',       'index',            'dimensionless'),
  ('ratio',       'ratio',            'dimensionless')
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
-- metric
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO metric (id, name, definition, default_unit_id, higher_is_better)
VALUES
  ('eui', 'Energy Use Intensity',
   'Total annual operational energy consumption per unit of floor area.',
   'kwh_m2_yr', false),
  ('upfront_embodied_carbon', 'Upfront Embodied Carbon',
   'Embodied carbon from raw material extraction through to practical completion (RICS A1-A5), per m² GIA.',
   'kgco2e_m2_gia', false),
  ('potable_water_use', 'Potable Water Use',
   'Mains water consumption per person per day.',
   'l_p_day', false),
  ('daylight_factor', 'Daylight Factor',
   'Ratio of internal to external illuminance under overcast sky, expressed as a percentage.',
   'pct', true),
  ('utci', 'Universal Thermal Climate Index',
   'Equivalent outdoor temperature used to assess pedestrian thermal comfort. Comfort optimum, not a maximise/minimise target.',
   'degc', null),
  ('form_factor', 'Form Factor',
   'Ratio of external envelope area to gross internal area; lower generally reduces fabric heat loss.',
   'ratio', false),
  ('window_wall_ratio', 'Window-to-Wall Ratio',
   'Proportion of external wall area glazed. Has a context-dependent optimum, not a monotonic target.',
   'pct', null),
  ('street_connectivity_index', 'Street Connectivity Index',
   'Measure of permeability and route choice in the street network.',
   'index', true),
  ('biodiversity_net_gain', 'Biodiversity Net Gain',
   'Percentage uplift in biodiversity units delivered by a scheme relative to its pre-development baseline.',
   'pct', true),
  ('urban_green_factor', 'Urban Green Factor',
   'Weighted index of green and blue infrastructure provision across a site.',
   'index', true),
  ('co2_concentration', 'Indoor CO2 Concentration',
   'Steady-state indoor carbon dioxide concentration, an indicator of ventilation adequacy.',
   'ppm', false),
  ('pm25', 'PM2.5 Concentration',
   'Airborne particulate matter under 2.5 microns.',
   'ug_m3', false),
  ('voc', 'Volatile Organic Compounds',
   'Total volatile organic compound concentration.',
   'ug_m3', false),
  ('reverberation_time', 'Reverberation Time',
   'Time for sound to decay by 60dB in an enclosed space.',
   'seconds', false),
  ('illuminance', 'Illuminance',
   'Task-plane light level. Has a task-specific target, not a simple maximise/minimise direction.',
   'lux', null),
  ('waste_diversion_rate', 'Waste Diversion Rate',
   'Percentage of construction or operational waste diverted from landfill.',
   'pct', true),
  ('renewable_fraction', 'Renewable Energy Fraction',
   'Percentage of total energy demand met by on-site or directly procured renewables.',
   'pct', true)
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
-- taxonomy + taxonomy_term
--
-- Term ids embed their own hierarchy ('topic.operational_carbon.passive'),
-- so they double as the ltree path with no separate bookkeeping.
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO taxonomy (id, name) VALUES
  ('topic',         'Topic'),
  ('scale',         'Scale'),
  ('stage',         'Stage (coarse)'),
  ('building_use',  'Building Use'),
  ('region',        'Region / Climate'),
  ('level',         'Ambition Level'),
  ('project_type',  'Project Type'),
  ('discipline',    'Discipline'),
  ('authority',     'Authority')
ON CONFLICT (id) DO NOTHING;

-- 1. topic — hierarchical, three levels deep where the corpus warrants it
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('topic.climate_resilience', 'topic', NULL, 'climate_resilience', 'Climate Resilience', ARRAY[]::text[], 10),
  ('topic.operational_carbon', 'topic', NULL, 'operational_carbon', 'Operational Carbon', ARRAY[]::text[], 20),
  ('topic.operational_carbon.passive_design', 'topic', 'topic.operational_carbon', 'passive_design', 'Passive Design', ARRAY[]::text[], 21),
  ('topic.operational_carbon.passive_design.building_fabric', 'topic', 'topic.operational_carbon.passive_design', 'building_fabric', 'Building Fabric', ARRAY[]::text[], 22),
  ('topic.operational_carbon.passive_design.shading_glazing', 'topic', 'topic.operational_carbon.passive_design', 'shading_glazing', 'Shading & Glazing', ARRAY[]::text[], 23),
  ('topic.operational_carbon.active_systems', 'topic', 'topic.operational_carbon', 'active_systems', 'Active Systems & Controls', ARRAY[]::text[], 24),
  ('topic.operational_carbon.renewable_energy', 'topic', 'topic.operational_carbon', 'renewable_energy', 'Renewable Energy', ARRAY[]::text[], 25),
  ('topic.embodied_carbon_circularity', 'topic', NULL, 'embodied_carbon_circularity', 'Embodied Carbon & Circularity', ARRAY[]::text[], 30),
  ('topic.embodied_carbon_circularity.materials_specification', 'topic', 'topic.embodied_carbon_circularity', 'materials_specification', 'Materials Specification', ARRAY[]::text[], 31),
  ('topic.embodied_carbon_circularity.circular_design', 'topic', 'topic.embodied_carbon_circularity', 'circular_design', 'Circular Design & Reuse', ARRAY[]::text[], 32),
  ('topic.water', 'topic', NULL, 'water', 'Water', ARRAY[]::text[], 40),
  ('topic.water.potable_water', 'topic', 'topic.water', 'potable_water', 'Potable Water', ARRAY[]::text[], 41),
  ('topic.water.surface_water_drainage', 'topic', 'topic.water', 'surface_water_drainage', 'Surface Water & Drainage', ARRAY[]::text[], 42),
  ('topic.biodiversity_pollution_land', 'topic', NULL, 'biodiversity_pollution_land', 'Biodiversity, Pollution & Land Transformation', ARRAY[]::text[], 50),
  ('topic.biodiversity_pollution_land.biodiversity', 'topic', 'topic.biodiversity_pollution_land', 'biodiversity', 'Biodiversity & Ecology', ARRAY[]::text[], 51),
  ('topic.biodiversity_pollution_land.pollution', 'topic', 'topic.biodiversity_pollution_land', 'pollution', 'Pollution', ARRAY[]::text[], 52),
  ('topic.biodiversity_pollution_land.land_transformation', 'topic', 'topic.biodiversity_pollution_land', 'land_transformation', 'Land Transformation', ARRAY[]::text[], 53),
  ('topic.health_wellbeing', 'topic', NULL, 'health_wellbeing', 'Health & Wellbeing', ARRAY[]::text[], 60),
  ('topic.health_wellbeing.daylight_views', 'topic', 'topic.health_wellbeing', 'daylight_views', 'Daylight & Views', ARRAY[]::text[], 61),
  ('topic.health_wellbeing.air_quality', 'topic', 'topic.health_wellbeing', 'air_quality', 'Indoor Air Quality', ARRAY[]::text[], 62),
  ('topic.health_wellbeing.acoustics', 'topic', 'topic.health_wellbeing', 'acoustics', 'Acoustics', ARRAY[]::text[], 63),
  ('topic.health_wellbeing.thermal_comfort', 'topic', 'topic.health_wellbeing', 'thermal_comfort', 'Thermal Comfort', ARRAY[]::text[], 64),
  ('topic.social_value', 'topic', NULL, 'social_value', 'Social Value', ARRAY[]::text[], 70),
  ('topic.transport_mobility', 'topic', NULL, 'transport_mobility', 'Transport & Mobility', ARRAY[]::text[], 80),
  ('topic.smart_city_digital', 'topic', NULL, 'smart_city_digital', 'Smart City & Digital', ARRAY[]::text[], 90),
  ('topic.urban_design_typology', 'topic', NULL, 'urban_design_typology', 'Urban Design & Typology', ARRAY[]::text[], 100),
  ('topic.practice_management_fees', 'topic', NULL, 'practice_management_fees', 'Practice Management & Fees', ARRAY[]::text[], 110)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 2. scale — the corpus's inconsistent spellings live in synonyms; this is
-- what makes the facet usable across sheets that never agreed on wording.
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('scale.city_region', 'scale', NULL, 'city_region', 'City / Region', ARRAY[]::text[], 10),
  ('scale.masterplan_district', 'scale', NULL, 'masterplan_district', 'Masterplan / District',
    ARRAY['MASTER PLAN LEVEL', 'LARGE SCALE/MASTERPLAN'], 20),
  ('scale.neighbourhood_block', 'scale', NULL, 'neighbourhood_block', 'Neighbourhood / Block',
    ARRAY['URBAN / WIDER SITE CONNECTIONS', 'WIDER SITE/ NEIGHBORHOOD LEVEL'], 30),
  ('scale.plot', 'scale', NULL, 'plot', 'Plot', ARRAY[]::text[], 40),
  ('scale.building', 'scale', NULL, 'building', 'Building',
    ARRAY['BUILDING LEVEL', 'BUILDING SCALE'], 50),
  ('scale.unit', 'scale', NULL, 'unit', 'Unit', ARRAY[]::text[], 60),
  ('scale.element', 'scale', NULL, 'element', 'Element', ARRAY[]::text[], 70)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 3. stage — a coarse, cross-cutting tag for search/browse. Distinct from the
-- precise stage_scheme/stage/stage_crosswalk system below, which carries the
-- corpus's real, document-specific stage vocabularies.
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('stage.concept', 'stage', NULL, 'concept', 'Concept', ARRAY[]::text[], 10),
  ('stage.schematic', 'stage', NULL, 'schematic', 'Schematic', ARRAY[]::text[], 20),
  ('stage.detailed', 'stage', NULL, 'detailed', 'Detailed', ARRAY[]::text[], 30),
  ('stage.construction', 'stage', NULL, 'construction', 'Construction', ARRAY[]::text[], 40),
  ('stage.in_use', 'stage', NULL, 'in_use', 'In Use / Operation', ARRAY[]::text[], 50)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 4. building_use
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('building_use.residential', 'building_use', NULL, 'residential', 'Residential', ARRAY[]::text[], 10),
  ('building_use.residential.single_family', 'building_use', 'building_use.residential', 'single_family', 'Single-Family', ARRAY[]::text[], 11),
  ('building_use.residential.flats', 'building_use', 'building_use.residential', 'flats', 'Flats', ARRAY[]::text[], 12),
  ('building_use.office', 'building_use', NULL, 'office', 'Office', ARRAY[]::text[], 20),
  ('building_use.office.shell_core', 'building_use', 'building_use.office', 'shell_core', 'Office Shell & Core', ARRAY[]::text[], 21),
  ('building_use.retail', 'building_use', NULL, 'retail', 'Retail', ARRAY[]::text[], 30),
  ('building_use.retail.high_street', 'building_use', 'building_use.retail', 'high_street', 'High-Street Retail', ARRAY[]::text[], 31),
  ('building_use.retail.dept_store', 'building_use', 'building_use.retail', 'dept_store', 'Department Store', ARRAY[]::text[], 32),
  ('building_use.f_and_b', 'building_use', NULL, 'f_and_b', 'Food & Beverage', ARRAY[]::text[], 40),
  ('building_use.f_and_b.with_catering', 'building_use', 'building_use.f_and_b', 'with_catering', 'F&B With Catering', ARRAY[]::text[], 41),
  ('building_use.f_and_b.without_catering', 'building_use', 'building_use.f_and_b', 'without_catering', 'F&B Without Catering', ARRAY[]::text[], 42),
  ('building_use.hotel', 'building_use', NULL, 'hotel', 'Hotel', ARRAY[]::text[], 50),
  ('building_use.culture_entertainment', 'building_use', NULL, 'culture_entertainment', 'Culture & Entertainment', ARRAY[]::text[], 60),
  ('building_use.science_tech', 'building_use', NULL, 'science_tech', 'Science & Tech', ARRAY[]::text[], 70),
  ('building_use.sport_leisure', 'building_use', NULL, 'sport_leisure', 'Sport & Leisure', ARRAY[]::text[], 80),
  ('building_use.trading_floors', 'building_use', NULL, 'trading_floors', 'Trading Floors', ARRAY[]::text[], 90),
  ('building_use.education', 'building_use', NULL, 'education', 'Education', ARRAY[]::text[], 100),
  ('building_use.public_realm', 'building_use', NULL, 'public_realm', 'Public Realm', ARRAY[]::text[], 110),
  ('building_use.infrastructure', 'building_use', NULL, 'infrastructure', 'Infrastructure', ARRAY[]::text[], 120),
  ('building_use.mixed', 'building_use', NULL, 'mixed', 'Mixed Use', ARRAY[]::text[], 130)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 5. region / climate — two branches under one facet
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('region.geography', 'region', NULL, 'geography', 'Geography', ARRAY[]::text[], 10),
  ('region.geography.uk', 'region', 'region.geography', 'uk', 'UK', ARRAY[]::text[], 11),
  ('region.geography.gulf', 'region', 'region.geography', 'gulf', 'Gulf', ARRAY[]::text[], 12),
  ('region.geography.eu', 'region', 'region.geography', 'eu', 'EU', ARRAY[]::text[], 13),
  ('region.geography.global', 'region', 'region.geography', 'global', 'Global', ARRAY[]::text[], 14),
  ('region.climate', 'region', NULL, 'climate', 'Climate', ARRAY[]::text[], 20),
  ('region.climate.warm_arid', 'region', 'region.climate', 'warm_arid', 'Warm-Arid', ARRAY[]::text[], 21),
  ('region.climate.warm_humid', 'region', 'region.climate', 'warm_humid', 'Warm-Humid', ARRAY[]::text[], 22),
  ('region.climate.temperate', 'region', 'region.climate', 'temperate', 'Temperate', ARRAY[]::text[], 23)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 6. level — ambition/performance level as a cross-cutting facet, independent
-- of any one framework's own graded ladder (see rating_scale below).
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('level.baseline', 'level', NULL, 'baseline', 'Baseline', ARRAY[]::text[], 10),
  ('level.enhanced', 'level', NULL, 'enhanced', 'Enhanced', ARRAY[]::text[], 20),
  ('level.best_practice', 'level', NULL, 'best_practice', 'Best Practice', ARRAY[]::text[], 30),
  ('level.exemplar', 'level', NULL, 'exemplar', 'Exemplar', ARRAY[]::text[], 40)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 7. project_type
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('project_type.new_build', 'project_type', NULL, 'new_build', 'New Build', ARRAY[]::text[], 10),
  ('project_type.existing_retrofit', 'project_type', NULL, 'existing_retrofit', 'Existing / Retrofit', ARRAY[]::text[], 20),
  ('project_type.both', 'project_type', NULL, 'both', 'Both', ARRAY[]::text[], 30)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 8. discipline
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('discipline.architecture', 'discipline', NULL, 'architecture', 'Architecture', ARRAY[]::text[], 10),
  ('discipline.mep', 'discipline', NULL, 'mep', 'MEP', ARRAY[]::text[], 20),
  ('discipline.structures', 'discipline', NULL, 'structures', 'Structures', ARRAY[]::text[], 30),
  ('discipline.landscape', 'discipline', NULL, 'landscape', 'Landscape', ARRAY[]::text[], 40),
  ('discipline.ecology', 'discipline', NULL, 'ecology', 'Ecology', ARRAY[]::text[], 50),
  ('discipline.acoustics', 'discipline', NULL, 'acoustics', 'Acoustics', ARRAY[]::text[], 60),
  ('discipline.transport', 'discipline', NULL, 'transport', 'Transport', ARRAY[]::text[], 70),
  ('discipline.cost', 'discipline', NULL, 'cost', 'Cost', ARRAY[]::text[], 80),
  ('discipline.digital_ict', 'discipline', NULL, 'digital_ict', 'Digital / ICT', ARRAY[]::text[], 90)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- 9. authority
INSERT INTO taxonomy_term (id, taxonomy_id, parent_id, code, label, synonyms, path, ordinal)
SELECT id, taxonomy_id, parent_id, code, label, synonyms, id::ltree, ordinal
FROM (VALUES
  ('authority.internal_practice', 'authority', NULL, 'internal_practice', 'Internal Practice', ARRAY[]::text[], 10),
  ('authority.client_framework', 'authority', NULL, 'client_framework', 'Client Framework', ARRAY[]::text[], 20),
  ('authority.external_standard', 'authority', NULL, 'external_standard', 'External Standard', ARRAY[]::text[], 30)
) AS t(id, taxonomy_id, parent_id, code, label, synonyms, ordinal)
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
-- stage_scheme + stage + stage_crosswalk
--
-- No document in the corpus uses RIBA 0-7 itself; it exists purely as the
-- spine every native scheme below crosswalks onto.
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO stage_scheme (id, name, is_canonical) VALUES
  ('riba_2020', 'RIBA Plan of Work 2020 (canonical spine)', true),
  ('riba_legacy', 'RIBA Plan of Work (legacy, stages A-L)', false),
  ('masterplan', 'Masterplan stage scheme', false),
  ('generic', 'Generic concept/schematic/detailed scheme', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO stage (id, scheme_id, code, name, ordinal) VALUES
  ('riba_2020.0', 'riba_2020', '0', 'Strategic Definition', 0),
  ('riba_2020.1', 'riba_2020', '1', 'Preparation and Briefing', 1),
  ('riba_2020.2', 'riba_2020', '2', 'Concept Design', 2),
  ('riba_2020.3', 'riba_2020', '3', 'Spatial Coordination', 3),
  ('riba_2020.4', 'riba_2020', '4', 'Technical Design', 4),
  ('riba_2020.5', 'riba_2020', '5', 'Manufacturing and Construction', 5),
  ('riba_2020.6', 'riba_2020', '6', 'Handover', 6),
  ('riba_2020.7', 'riba_2020', '7', 'Use', 7),

  ('riba_legacy.a', 'riba_legacy', 'A', 'Appraisal', 0),
  ('riba_legacy.b', 'riba_legacy', 'B', 'Design Brief', 1),
  ('riba_legacy.c', 'riba_legacy', 'C', 'Concept', 2),
  ('riba_legacy.d', 'riba_legacy', 'D', 'Design Development', 3),
  ('riba_legacy.e', 'riba_legacy', 'E', 'Technical Design', 4),
  ('riba_legacy.f', 'riba_legacy', 'F', 'Production Information', 5),
  ('riba_legacy.g', 'riba_legacy', 'G', 'Tender Documentation', 6),
  ('riba_legacy.h', 'riba_legacy', 'H', 'Tender Action', 7),
  ('riba_legacy.j', 'riba_legacy', 'J', 'Mobilisation', 8),
  ('riba_legacy.k', 'riba_legacy', 'K', 'Construction to Practical Completion', 9),
  ('riba_legacy.l', 'riba_legacy', 'L', 'Post Practical Completion', 10),

  ('masterplan.cmp', 'masterplan', 'CMP', 'Concept Masterplan', 0),
  ('masterplan.dmp', 'masterplan', 'DMP', 'Detailed Masterplan', 1),
  ('masterplan.cd', 'masterplan', 'CD', 'Concept Design', 2),
  ('masterplan.sd', 'masterplan', 'SD', 'Schematic Design', 3),
  ('masterplan.dd', 'masterplan', 'DD', 'Design Development', 4),
  ('masterplan.construction', 'masterplan', 'Construction', 'Construction', 5),

  ('generic.concept', 'generic', 'concept', 'Concept', 0),
  ('generic.schematic', 'generic', 'schematic', 'Schematic', 1),
  ('generic.detailed', 'generic', 'detailed', 'Detailed', 2)
ON CONFLICT (id) DO NOTHING;

-- crosswalk onto the canonical spine, both directions
INSERT INTO stage_crosswalk (from_stage_id, to_stage_id) VALUES
  ('riba_legacy.a', 'riba_2020.0'), ('riba_2020.0', 'riba_legacy.a'),
  ('riba_legacy.b', 'riba_2020.1'), ('riba_2020.1', 'riba_legacy.b'),
  ('riba_legacy.c', 'riba_2020.2'), ('riba_2020.2', 'riba_legacy.c'),
  ('riba_legacy.d', 'riba_2020.3'), ('riba_2020.3', 'riba_legacy.d'),
  ('riba_legacy.e', 'riba_2020.4'), ('riba_2020.4', 'riba_legacy.e'),
  ('riba_legacy.f', 'riba_2020.4'), ('riba_2020.4', 'riba_legacy.f'),
  ('riba_legacy.g', 'riba_2020.4'), ('riba_2020.4', 'riba_legacy.g'),
  ('riba_legacy.h', 'riba_2020.4'), ('riba_2020.4', 'riba_legacy.h'),
  ('riba_legacy.j', 'riba_2020.5'), ('riba_2020.5', 'riba_legacy.j'),
  ('riba_legacy.k', 'riba_2020.5'), ('riba_2020.5', 'riba_legacy.k'),
  ('riba_legacy.l', 'riba_2020.6'), ('riba_2020.6', 'riba_legacy.l'),

  ('masterplan.cmp', 'riba_2020.1'), ('riba_2020.1', 'masterplan.cmp'),
  ('masterplan.dmp', 'riba_2020.2'), ('riba_2020.2', 'masterplan.dmp'),
  ('masterplan.cd', 'riba_2020.2'), ('riba_2020.2', 'masterplan.cd'),
  ('masterplan.sd', 'riba_2020.3'), ('riba_2020.3', 'masterplan.sd'),
  ('masterplan.dd', 'riba_2020.4'), ('riba_2020.4', 'masterplan.dd'),
  ('masterplan.construction', 'riba_2020.5'), ('riba_2020.5', 'masterplan.construction'),

  ('generic.concept', 'riba_2020.2'), ('riba_2020.2', 'generic.concept'),
  ('generic.schematic', 'riba_2020.3'), ('riba_2020.3', 'generic.schematic'),
  ('generic.detailed', 'riba_2020.4'), ('riba_2020.4', 'generic.detailed')
ON CONFLICT (from_stage_id, to_stage_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
-- standard — public standards bodies; safe to name in full
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO standard (id, name, publisher) VALUES
  ('uknzcbs', 'UK Net Zero Carbon Buildings Standard', 'NZCBS partnership'),
  ('riba_2030_challenge', 'RIBA 2030 Climate Challenge', 'RIBA'),
  ('riba_climate_guide', 'RIBA Climate Guide', 'RIBA'),
  ('leed_v5', 'LEED v5', 'USGBC'),
  ('breeam', 'BREEAM', 'BRE'),
  ('gsas', 'Global Sustainability Assessment System (GSAS)', 'GORD'),
  ('estidama', 'Estidama Pearl Rating System', 'Abu Dhabi Urban Planning Council'),
  ('mostadam', 'Mostadam', 'Saudi Green Building Forum'),
  ('ceequal', 'CEEQUAL', 'BRE'),
  ('envision', 'Envision', 'Institute for Sustainable Infrastructure'),
  ('ashrae_90_1_2019', 'ASHRAE 90.1-2019', 'ASHRAE'),
  ('well', 'WELL Building Standard', 'International WELL Building Institute'),
  ('fitwel', 'Fitwel', 'Center for Active Design'),
  ('lbc', 'Living Building Challenge', 'International Living Future Institute'),
  ('worldgbc', 'WorldGBC', 'World Green Building Council'),
  ('un_sdg', 'UN Sustainable Development Goals', 'United Nations'),
  ('un4ssc_itu_t', 'United for Smart Sustainable Cities (U4SSC) / ITU-T', 'ITU-T'),
  ('un_habitat', 'UN-Habitat', 'UN-Habitat'),
  ('c40', 'C40 Cities', 'C40 Cities'),
  ('sbti', 'Science Based Targets initiative', 'SBTi'),
  ('paris_agreement', 'Paris Agreement', 'UNFCCC'),
  ('ipcc_ar6', 'IPCC Sixth Assessment Report (AR6)', 'IPCC'),
  ('rics_measuring_practice_6', 'RICS Code of Measuring Practice, 6th ed.', 'RICS'),
  ('sbc', 'Saudi Building Code (SBC 201/601/602/1001)', 'Saudi Building Code National Committee')
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
-- rating_scale + rating_level + rating_level_crosswalk
--
-- Three ladders used across the corpus's frameworks, crosswalked so
-- 'exemplar' compares with 'pioneering' and 'transformational'.
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO rating_scale (slug, name) VALUES
  ('crib-levels', 'CRIB sheet performance levels'),
  ('framework-targets', 'Framework target tiers'),
  ('smart-city-contribution', 'Smart city contribution levels')
ON CONFLICT (slug) DO NOTHING;

-- level 1 is unlabelled on the printed sheets; name stays NULL
INSERT INTO rating_level (scale_id, ordinal, code, name, colour)
SELECT s.id, v.ordinal, v.code, v.name, v.colour
FROM (VALUES
  ('crib-levels', 1, 'L1', NULL, '#a9aebc'),
  ('crib-levels', 2, 'L2', 'CTO Contributive', '#C86A1E'),
  ('crib-levels', 3, 'L3', 'High Performance', '#2E7D4F'),
  ('crib-levels', 4, 'L4', 'Exemplar Performance', '#004AFF')
) AS v(scale_slug, ordinal, code, name, colour)
JOIN rating_scale s ON s.slug = v.scale_slug
ON CONFLICT (scale_id, ordinal) DO NOTHING;

INSERT INTO rating_level (scale_id, ordinal, code, name)
SELECT s.id, v.ordinal, v.code, v.name
FROM (VALUES
  ('framework-targets', 1, 'baseline', 'Baseline'),
  ('framework-targets', 2, 'stretch', 'Stretch'),
  ('framework-targets', 3, 'pioneering', 'Pioneering')
) AS v(scale_slug, ordinal, code, name)
JOIN rating_scale s ON s.slug = v.scale_slug
ON CONFLICT (scale_id, ordinal) DO NOTHING;

INSERT INTO rating_level (scale_id, ordinal, code, name)
SELECT s.id, v.ordinal, v.code, v.name
FROM (VALUES
  ('smart-city-contribution', 1, 'none', 'None'),
  ('smart-city-contribution', 2, 'minimal', 'Minimal'),
  ('smart-city-contribution', 3, 'significant', 'Significant'),
  ('smart-city-contribution', 4, 'transformational', 'Transformational')
) AS v(scale_slug, ordinal, code, name)
JOIN rating_scale s ON s.slug = v.scale_slug
ON CONFLICT (scale_id, ordinal) DO NOTHING;

-- crosswalk by (scale slug, ordinal) pair rather than literal uuids, since
-- rating_level ids are server-generated
INSERT INTO rating_level_crosswalk (from_level_id, to_level_id, equivalence)
SELECT a.id, b.id, x.equivalence
FROM (VALUES
  ('crib-levels', 4, 'framework-targets', 3, 1.0),
  ('framework-targets', 3, 'crib-levels', 4, 1.0),
  ('crib-levels', 4, 'smart-city-contribution', 4, 1.0),
  ('smart-city-contribution', 4, 'crib-levels', 4, 1.0),
  ('framework-targets', 3, 'smart-city-contribution', 4, 1.0),
  ('smart-city-contribution', 4, 'framework-targets', 3, 1.0),

  ('crib-levels', 3, 'framework-targets', 2, 1.0),
  ('framework-targets', 2, 'crib-levels', 3, 1.0),
  ('crib-levels', 3, 'smart-city-contribution', 3, 1.0),
  ('smart-city-contribution', 3, 'crib-levels', 3, 1.0),
  ('framework-targets', 2, 'smart-city-contribution', 3, 1.0),
  ('smart-city-contribution', 3, 'framework-targets', 2, 1.0),

  ('crib-levels', 2, 'framework-targets', 1, 1.0),
  ('framework-targets', 1, 'crib-levels', 2, 1.0),
  ('crib-levels', 2, 'smart-city-contribution', 2, 1.0),
  ('smart-city-contribution', 2, 'crib-levels', 2, 1.0),
  ('framework-targets', 1, 'smart-city-contribution', 2, 1.0),
  ('smart-city-contribution', 2, 'framework-targets', 1, 1.0)
) AS x(from_slug, from_ord, to_slug, to_ord, equivalence)
JOIN rating_scale sa ON sa.slug = x.from_slug
JOIN rating_level a ON a.scale_id = sa.id AND a.ordinal = x.from_ord
JOIN rating_scale sb ON sb.slug = x.to_slug
JOIN rating_level b ON b.scale_id = sb.id AND b.ordinal = x.to_ord
ON CONFLICT (from_level_id, to_level_id) DO NOTHING;

COMMIT;

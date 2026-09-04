-- Synonyms for taxonomy_term, derived from vocabulary actually present in the
-- corpus (knowledge_item.statement, chunk.text, doc_node.title, criterion
-- titles) -- not from guessed architect-speak. See tools/classify_facets.py,
-- which reads these same rows at runtime so the lexicon has one source of
-- truth instead of drifting between SQL and Python.
--
-- Owned by tools/classify_facets.py. Idempotent: every statement below sets
-- the full desired array, so re-running this file is a no-op, not an append.
-- Applied to both the live DB and arch_test (see workflows/classify_facets.md).

-- 1. topic -------------------------------------------------------------
UPDATE taxonomy_term SET synonyms = ARRAY['EUI','ENERGY USE INTENSITY','OPERATIONAL ENERGY']
  WHERE id = 'topic.operational_carbon';
UPDATE taxonomy_term SET synonyms = ARRAY['U VALUE','U-VALUE','FORM FACTOR','AIRTIGHTNESS','BUILDING FABRIC']
  WHERE id = 'topic.operational_carbon.passive_design.building_fabric';
UPDATE taxonomy_term SET synonyms = ARRAY['WWR','WINDOW-TO-WALL RATIO','WINDOW TO WALL RATIO','GLAZING %','SOLAR GAIN']
  WHERE id = 'topic.operational_carbon.passive_design.shading_glazing';
UPDATE taxonomy_term SET synonyms = ARRAY['LOW CARBON ACTIVE SYSTEMS','ACTIVE SYSTEMS']
  WHERE id = 'topic.operational_carbon.active_systems';
UPDATE taxonomy_term SET synonyms = ARRAY['RENEWABLE ENERGY GENERATION','RENEWABLE ENERGY FRACTION','SMART GRID']
  WHERE id = 'topic.operational_carbon.renewable_energy';
UPDATE taxonomy_term SET synonyms = ARRAY['UPFRONT EMBODIED CARBON','EPD','A1-A5','A1-A3','KGCO2E/M2GIA']
  WHERE id = 'topic.embodied_carbon_circularity';
UPDATE taxonomy_term SET synonyms = ARRAY['LOW CARBON MATERIALS','LOCAL MATERIALS','BIO-MATERIALS','MATERIAL REUSE','SUSTAINABLE MATERIAL SPECIFICATION']
  WHERE id = 'topic.embodied_carbon_circularity.materials_specification';
UPDATE taxonomy_term SET synonyms = ARRAY['ZERO WASTE PLANNING','DESIGN FOR DECONSTRUCTION','MATERIAL PASSPORTS','CIRCULARITY STRATEGY','WASTE DIVERSION RATE','WASTE MANAGEMENT']
  WHERE id = 'topic.embodied_carbon_circularity.circular_design';
UPDATE taxonomy_term SET synonyms = ARRAY['POTABLE WATER USE','L/P/DAY','WATER DEMAND REDUCTION']
  WHERE id = 'topic.water.potable_water';
UPDATE taxonomy_term SET synonyms = ARRAY['SUDS','STORMWATER MANAGEMENT','FLOOD MITIGATION','WATER RETENTION, TREATMENT, AND REUSE','SMART WATER SYSTEM']
  WHERE id = 'topic.water.surface_water_drainage';
UPDATE taxonomy_term SET synonyms = ARRAY['BIODIVERSITY NET GAIN','BNG','URBAN GREEN FACTOR','HABITAT','WILDLIFE HABITATS','ECOLOGIST']
  WHERE id = 'topic.biodiversity_pollution_land.biodiversity';
UPDATE taxonomy_term SET synonyms = ARRAY['SOIL POLLUTION','LIGHT POLLUTION','WATER POLLUTION','AIR POLLUTION','NOISE POLLUTION','GROUNDWATER CONTAMINATION']
  WHERE id = 'topic.biodiversity_pollution_land.pollution';
UPDATE taxonomy_term SET synonyms = ARRAY['LAND TRANSFORMATION']
  WHERE id = 'topic.biodiversity_pollution_land.land_transformation';
UPDATE taxonomy_term SET synonyms = ARRAY['UTCI','UNIVERSAL THERMAL CLIMATE INDEX','THERMAL COMFORT']
  WHERE id = 'topic.health_wellbeing.thermal_comfort';
UPDATE taxonomy_term SET synonyms = ARRAY['INDOOR AIR QUALITY','IAQ','PM2.5','VOC','CO2 CONCENTRATION']
  WHERE id = 'topic.health_wellbeing.air_quality';
UPDATE taxonomy_term SET synonyms = ARRAY['ACOUSTIC','ACOUSTICS','REVERBERATION TIME']
  WHERE id = 'topic.health_wellbeing.acoustics';
UPDATE taxonomy_term SET synonyms = ARRAY['DAYLIGHT FACTOR','DAYLIGHTING','ILLUMINANCE']
  WHERE id = 'topic.health_wellbeing.daylight_views';
UPDATE taxonomy_term SET synonyms = ARRAY['SOCIAL VALUE','STRENGTHENING COMMUNITIES','QUALITY OF LIFE','JUST TRANSITION','EQUITY AND OPPORTUNITY','CULTURE & TRADITIONS','EDUCATION & LITERACY']
  WHERE id = 'topic.social_value';
UPDATE taxonomy_term SET synonyms = ARRAY['ACTIVE MOBILITY','MICROMOBILITY','MULTIMODAL TRANSIT','STREET CONNECTIVITY INDEX','SCI','MOBILITY AND TRANSPORTATION']
  WHERE id = 'topic.transport_mobility';
UPDATE taxonomy_term SET synonyms = ARRAY['SMART CITY','DIGITAL SOLUTIONS','SMART CITY UNIT','METAVERSE GOVERNANCE','DATA GOVERNANCE']
  WHERE id = 'topic.smart_city_digital';
UPDATE taxonomy_term SET synonyms = ARRAY['F.A.R.','FAR','PLOT COVERAGE','DENSITY & LAND USE','MASSING','CONNECTED CITY PATTERNS']
  WHERE id = 'topic.urban_design_typology';
UPDATE taxonomy_term SET synonyms = ARRAY['CLIMATE HAZARD','CLIMATE RISK','CLIMATE ADAPTATION','SURFACE TEMPERATURE REDUCTION','ITERATIVE DESIGN']
  WHERE id = 'topic.climate_resilience';
UPDATE taxonomy_term SET synonyms = ARRAY['FEE CALCULATION','CASHFLOW','BUDGET AND COST RATE']
  WHERE id = 'topic.practice_management_fees';

-- 2. scale --------------------------------------------------------------
-- masterplan_district, neighbourhood_block and building already carry the
-- spellings the schema author seeded; add the one more found in the
-- crib-biodiversity criterion titles.
UPDATE taxonomy_term SET synonyms = ARRAY['URBAN / WIDER SITE CONNECTIONS','WIDER SITE/ NEIGHBORHOOD LEVEL','NEIGHBORHOOD LEVEL','CITY LAND WIDER SITE CONNECTIONS']
  WHERE id = 'scale.neighbourhood_block';

-- 3. building_use ---------------------------------------------------------
-- benchmark.building_use_id already carries a normalised free-text token per
-- row (handled structurally in classify_facets.py); these synonyms cover the
-- same vocabulary for keyword matching against guidance/pattern statements.
UPDATE taxonomy_term SET synonyms = ARRAY['GENERAL OFFICES']
  WHERE id = 'building_use.office';
UPDATE taxonomy_term SET synonyms = ARRAY['OFFICE SHELL & CORE','OFFICE OFFICE SHELL & CORE']
  WHERE id = 'building_use.office.shell_core';
UPDATE taxonomy_term SET synonyms = ARRAY['RETAIL DEPT STORE','DEPARTMENT STORE']
  WHERE id = 'building_use.retail.dept_store';
UPDATE taxonomy_term SET synonyms = ARRAY['SINGLE FAMILY HOMES','RESIDENTIAL SINGLE FAMILY HOMES','SINGLE-FAMILY HOMES']
  WHERE id = 'building_use.residential.single_family';
UPDATE taxonomy_term SET synonyms = ARRAY['MULTIFAMILY','MULTI-FAMILY']
  WHERE id = 'building_use.residential.flats';
UPDATE taxonomy_term SET synonyms = ARRAY['WITHOUT CATERING','F&B WITHOUT CATERING']
  WHERE id = 'building_use.f_and_b.without_catering';
UPDATE taxonomy_term SET synonyms = ARRAY['WITH CATERING','F&B WITH CATERING']
  WHERE id = 'building_use.f_and_b.with_catering';

-- 4. discipline -----------------------------------------------------------
UPDATE taxonomy_term SET synonyms = ARRAY['MEP','MEP CONSULTANTS','MEP AND CORE']
  WHERE id = 'discipline.mep';
UPDATE taxonomy_term SET synonyms = ARRAY['ECOLOGIST']
  WHERE id = 'discipline.ecology';
UPDATE taxonomy_term SET synonyms = ARRAY['LANDSCAPE DESIGNER','LANDSCAPE ARCHITECT']
  WHERE id = 'discipline.landscape';
UPDATE taxonomy_term SET synonyms = ARRAY['ACOUSTIC','ACOUSTICIAN']
  WHERE id = 'discipline.acoustics';

-- 5. region -----------------------------------------------------------
UPDATE taxonomy_term SET synonyms = ARRAY['UK NZCBS']
  WHERE id = 'region.geography.uk';

-- 6. level ------------------------------------------------------------
-- The crib-sheet ladder (L1-L4) and the two framework ladders (baseline/
-- stretch/pioneering, minimal/significant/transformational) are handled
-- structurally in classify_facets.py via rating_level.ordinal; these
-- synonyms are the unambiguous 1:1 labels for keyword search/UI.
UPDATE taxonomy_term SET synonyms = ARRAY['BASELINE','MINIMAL']
  WHERE id = 'level.baseline';
UPDATE taxonomy_term SET synonyms = ARRAY['STRETCH','SIGNIFICANT','CTO CONTRIBUTIVE']
  WHERE id = 'level.enhanced';
UPDATE taxonomy_term SET synonyms = ARRAY['HIGH PERFORMANCE']
  WHERE id = 'level.best_practice';
UPDATE taxonomy_term SET synonyms = ARRAY['EXEMPLAR PERFORMANCE','PIONEERING','TRANSFORMATIONAL']
  WHERE id = 'level.exemplar';

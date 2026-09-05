"""Dissolve decoupage_.geojson communes into a single boundary polygon for osmium extract."""

import geopandas as gpd

DATA_DIR = "/Users/antoinechevre/Desktop/Dossier_accessibility_index/Data"

cda = gpd.read_file(f"{DATA_DIR}/decoupage_cda.geojson")
cda = cda.set_crs("EPSG:4326") if cda.crs is None else cda
cda.geometry = cda.geometry.buffer(0)

boundary = gpd.GeoDataFrame(geometry=[cda.union_all()], crs=cda.crs)
boundary.to_file(f"{DATA_DIR}/cda_boundary.geojson", driver="GeoJSON")
print("wrote cda_boundary.geojson, bounds:", boundary.total_bounds)

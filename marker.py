import os
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from geopy.distance import geodesic
import time
import xml.etree.ElementTree as ET
import json
import requests
import certifi
import osmnx as ox
import geopandas as gpd
from shapely.geometry import Point
import pandas as pd
import xml.etree.ElementTree as ET

start_time = time.time()
ox.settings.use_cache = False

geolocator = Nominatim(user_agent="my_osm_app", timeout=10)

def geocode_ort(ort, buffer):
    # 1. Suchen in edges
    polygon = edges_gdf[edges_gdf['name'].astype(str).str.lower() == ort.lower()]
    if not polygon.empty:
        # print("straße")
        unioned = polygon.union_all().buffer(buffer)
        buffered_gdf = gpd.GeoDataFrame(geometry=[unioned], crs="EPSG:32633")
        buffered_gdf = buffered_gdf.to_crs("EPSG:4326")
        return buffered_gdf

    # 2. Fallback: buildings
    mask = (
        (buildings['name'].astype(str).str.lower() == ort.lower()) |
        (buildings['old_name'].astype(str).str.lower() == ort.lower())
    )
    polygon = buildings[mask]
    if not polygon.empty:
        # print("gebäude")
        unioned = polygon.union_all().buffer(buffer)
        buffered_gdf = gpd.GeoDataFrame(geometry=[unioned], crs="EPSG:32633")
        buffered_gdf = buffered_gdf.to_crs("EPSG:4326")
        return buffered_gdf
    
    location = geolocator.geocode(f"{ort}, Graz")
    if not location is None:
        # print("osm")
        point = Point(location.longitude, location.latitude)
        tags = {"building": True, "highway": True, "railway": True, "leisure": True, "amenity": "hospital", "landuse": "cemetery", "landuse": "forest"}
        gdf_buildings = ox.features_from_point((location.latitude, location.longitude), tags=tags, dist=100)
        polygon = gdf_buildings[gdf_buildings.geometry.contains(point)]
        if polygon.empty:
            return None
        polygon = polygon.to_crs(utm_crs)
        unioned = polygon.union_all().buffer(buffer)
        buffered_gdf = gpd.GeoDataFrame(geometry=[unioned], crs="EPSG:32633")
        buffered_gdf = buffered_gdf.to_crs("EPSG:4326")
        return buffered_gdf

    return None

def process_xml_from_url(gams_id):
    url = f"https://gams.uni-graz.at/archive/objects/o:{gams_id}/datastreams/LIDO_SOURCE/content"
    try:
        response = requests.get(url, timeout=(20,20), verify=certifi.where())
        if response.status_code == 200:
            return process_xml_content(response.content)
        else:
            print(f"⚠️ Datei nicht gefunden oder Fehler beim Abruf: {gams_id}")
            return None
    except Exception as e:
        print(f"❌ Fehler beim Abrufen der XML-Datei {gams_id}: {e}")
        return None  

fehler = []

def process_xml_content(xml_content):
    root = ET.fromstring(xml_content)
    ns = {"lido": "http://www.lido-schema.org"}
    
    ansicht_elem = root.findall(".//lido:termMaterialsTech[@lido:type='decor']/lido:term", ns)
    ansichten = [elem.text.strip() for elem in ansicht_elem if elem is not None and elem.text]
    for ansicht in ansichten:
        print(ansicht)
        if ansicht == "Mehrbildkarte":
            return None
        
    typ_elem = root.find(".//lido:objectWorkType/lido:term", ns)
    typ = typ_elem.text.strip()
    if typ != "Ansichtspostkarte":
        return None

    title_elem = root.find(".//lido:titleSet/lido:appellationValue", ns)
    title = title_elem.text.strip() if title_elem is not None else "Ohne Titel"

    id_elem = root.find(".//lido:lidoRecID", ns)
    id = id_elem.text.strip() if id_elem is not None else "Unbekannt"

    earliest_elem = root.find(".//lido:earliestDate[@lido:type='timeCoverageFrom']", ns)
    latest_elem = root.find(".//lido:latestDate[@lido:type='timeCoverageTo']", ns)
    earliest_date = int(earliest_elem.text.strip()) if earliest_elem is not None and earliest_elem.text else None
    latest_date = int(latest_elem.text.strip()) if latest_elem is not None and latest_elem.text else None

    # Alle Orte sammeln
    orte = set()
    for place in root.findall(".//lido:subjectSet/lido:subject[@lido:type='imagePlace']/lido:subjectPlace/lido:place/lido:namePlaceSet/lido:appellationValue", ns):
        if place.text:
            orte.add(place.text.strip())

    polygons = []
    max_buffer = 100
    buffer = 10

    while buffer <= max_buffer:
        polygons = []
        for ort in orte:
            # print(f"{ort} (Buffer: {buffer})")
            polygon = geocode_ort(ort, buffer)
            if polygon is not None and not polygon.empty:
                polygons.append(polygon)
        
        if not polygons:
            # Wenn keine Polygone gefunden wurden, sofort zurückgeben oder abbrechen
            fehler.append(f"⚠️ Keine Polygone für {id} bei Buffer {buffer}, {orte}")
            print(f"⚠️ Keine Polygone für {id} bei Buffer {buffer}, {orte}")
            return None
        
        if polygons:
            geoms = [gdf.geometry.iloc[0] for gdf in polygons]
            cross = geoms[0]
            for geom in geoms[1:]:
                cross = cross.intersection(geom)

            if not cross.is_empty:
                center = cross.centroid
                return {
                    "title": title,
                    "id": id,
                    "earliestDate": earliest_date,
                    "latestDate": latest_date,
                    "latitude": center.y,
                    "longitude": center.x
                }

        buffer += 10  # nächster Versuch mit größerem Puffer

    # Kein gültiger Schnittpunkt gefunden
    fehler.append(f"⚠️ Kein Schnittpunkt für {id} mit max. Buffer {max_buffer}, {orte}")
    print(f"⚠️ Kein Schnittpunkt für {id} mit max. Buffer {max_buffer}, {orte}")
    return None

        
# IDs der Objekte
# nummern = [8316]
# nummern = [1782,470,5155,7228,7295,1299,1758,2517,3082,10,7170,708,623,5509,7218,7651,1381,7387,2126,7221,6230,7060,7514,8454,6761]
# gams_ids = [f"gm.{num}" for num in nummern]
num_max = 8820
# num_max = 500
gams_ids = [f"gm.{num}" for num in range(1, num_max+1)]
# print(gams_ids)

# OSM-Daten für Graz laden
edges = ox.graph_from_place("Graz, Austria", network_type="all", simplify=True)
edges_gdf = ox.graph_to_gdfs(edges, nodes=False, edges=True)
buildings = ox.features_from_place("Graz, Austria", tags={"building": True, "historic": True, "bridge": True, "railway": True, "leisure": True, "amenity": "hospital", "landuse": "cemetery", "landuse": "forest"})

utm_crs = "EPSG:32633"
edges_gdf = edges_gdf.to_crs(utm_crs)
buildings = buildings.to_crs(utm_crs)

alle_koordinaten = []

for gams_id in gams_ids:
    eintrag = process_xml_from_url(gams_id)
    if eintrag:
        alle_koordinaten.append(eintrag)

# Abschließendes Speichern (für Reste unterhalb von 500)
pfad_final = "C:/Users/Jasmin/Documents/Uni Graz/Semester 4/Masterarbeit/marker_graz_osmnx.json"
with open(pfad_final, "w", encoding="utf-8") as f:
    json.dump(alle_koordinaten, f, ensure_ascii=False, indent=4)
print(f"✅ Endgültig gespeichert in: {pfad_final}")

pfad_final_fehler = "C:/Users/Jasmin/Documents/Uni Graz/Semester 4/Masterarbeit/marker_graz_osmnx_fehler.txt"
with open(pfad_final_fehler, "w", encoding="utf-8") as f:
    f.writelines([zeile + "\n" for zeile in fehler])
print(f"✅ Endgültig gespeichert in: {pfad_final_fehler}")

end_time = time.time()
elapsed_time = end_time - start_time

print(f"⏱️ Gesamtlaufzeit: {elapsed_time:.2f} Sekunden ({elapsed_time/60:.2f} Minuten)")
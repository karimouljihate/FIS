// Irrigation Planner - Map JavaScript
// Uses Leaflet.js with Leaflet.draw for editing

let map = null;
let drawLayer = null;
let featureLayer = null;
let drawnItems = null;

function initMap(elementId, geojsonUrl, options = {}) {
    const mapElement = document.getElementById(elementId);
    if (!mapElement) return null;

    const mapHeight = options.height || '500px';
    mapElement.style.height = mapHeight;

    map = L.map(elementId).setView(options.center || [34.0, -6.0], options.zoom || 13);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19
    }).addTo(map);

    // Add scale bar
    L.control.scale().addTo(map);

    // Drawn items layer
    drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    // Feature layer for loaded data
    featureLayer = L.geoJSON(null, {
        style: function(feature) {
            const color = feature.properties?.color || '#3388ff';
            return { color: color, weight: 2, fillOpacity: 0.2 };
        },
        pointToLayer: function(feature, latlng) {
            const color = feature.properties?.color || '#3388ff';
            const type = feature.properties?.type || '';
            let icon;
            if (type === 'tree') {
                icon = L.divIcon({
                    className: 'tree-marker',
                    html: `<div style="background:${color};width:8px;height:8px;border-radius:50%;border:1px solid white"></div>`,
                    iconSize: [10, 10]
                });
            } else if (type.includes('valve') || type.includes('pipe') || type === 'dripline' || type.includes('air') || type.includes('pressure') || type === 'other') {
                icon = L.divIcon({
                    className: 'network-marker',
                    html: `<div style="background:${color};width:14px;height:14px;border-radius:3px;border:2px solid white"></div>`,
                    iconSize: [18, 18]
                });
            } else {
                icon = L.divIcon({
                    className: 'point-marker',
                    html: `<div style="background:${color};width:12px;height:12px;border-radius:50%;border:2px solid white"></div>`,
                    iconSize: [16, 16]
                });
            }
            return L.marker(latlng, { icon: icon });
        },
        onEachFeature: function(feature, layer) {
            if (feature.properties) {
                const name = feature.properties.name || '';
                const nameAr = feature.properties.name_ar || '';
                const type = feature.properties.type || '';
                const validated = feature.properties.validated;
                let popupContent = `<b>${name}</b>`;
                if (nameAr) popupContent += ` / <span class="ar-text">${nameAr}</span>`;
                popupContent += `<br><small>Type: ${type}</small>`;
                if (validated !== undefined) {
                    popupContent += `<br><small>Status: ${validated ? '✓ Validated' : 'Pending'}</small>`;
                }
                layer.bindPopup(popupContent);
            }
        }
    }).addTo(map);

    // Load GeoJSON data if URL provided
    if (geojsonUrl) {
        fetch(geojsonUrl)
            .then(response => response.json())
            .then(data => {
                featureLayer.addData(data);
                if (data.features && data.features.length > 0) {
                    map.fitBounds(featureLayer.getBounds(), { padding: [50, 50] });
                }
            })
            .catch(err => console.error('Error loading geometry:', err));
    }

    return map;
}

function initDrawTools(drawType, onDrawComplete) {
    if (!map) return;

    const drawOptions = {
        polygon: {
            allowIntersection: false,
            showArea: true
        },
        polyline: {
            allowIntersection: false
        },
        point: {}
    };

    const drawHandler = {
        polygon: new L.Draw.Polygon(map, drawOptions.polygon),
        polyline: new L.Draw.Polyline(map, drawOptions.polyline),
        point: new L.Draw.Marker(map, drawOptions.point)
    };

    if (drawHandler[drawType]) {
        drawHandler[drawType].enable();
    }

    map.on(L.Draw.Event.CREATED, function(e) {
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);

        const geojson = e.layer.toGeoJSON();

        if (onDrawComplete) {
            let coords;
            if (drawType === 'point') {
                coords = geojson.geometry.coordinates;
            } else if (drawType === 'polyline') {
                coords = geojson.geometry.coordinates;
            } else if (drawType === 'polygon') {
                coords = geojson.geometry.coordinates[0];
            }
            onDrawComplete(coords, JSON.stringify(coords));
        }
    });
}

function clearDrawn() {
    if (drawnItems) {
        drawnItems.clearLayers();
    }
}

function addLegend(legendItems) {
    if (!map) return;
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = function() {
        const div = L.DomUtil.create('div', 'map-legend');
        let html = '<h6>Legend / مفتاح الخريطة</h6>';
        legendItems.forEach(item => {
            html += `<div class="legend-item"><span class="legend-color" style="background:${item.color}"></span> ${item.label}</div>`;
        });
        div.innerHTML = html;
        return div;
    };
    legend.addTo(map);
}

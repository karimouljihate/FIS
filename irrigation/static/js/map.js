// Irrigation Planner - Map JavaScript
// Uses Leaflet.js with Leaflet.draw for editing

let map = null;
let drawLayer = null;
let featureLayer = null;
let drawnItems = null;
let mapSelectionMode = false;
let mapRulerActive = false;

// ---------- Geodesic helpers ----------
function ringAreaM2(ring) {
    // ring: [[lng, lat], ...] ; spherical excess approximation (same method as Leaflet.draw)
    if (!ring || ring.length < 3) return 0;
    const d2r = Math.PI / 180;
    let area = 0;
    for (let i = 0; i < ring.length; i++) {
        const p1 = ring[i];
        const p2 = ring[(i + 1) % ring.length];
        area += ((p2[0] - p1[0]) * d2r) * (2 + Math.sin(p1[1] * d2r) + Math.sin(p2[1] * d2r));
    }
    return Math.abs(area * 6378137 * 6378137 / 2);
}

function ringLengthM(ring, closed) {
    let total = 0;
    for (let i = 1; i < ring.length; i++) {
        total += L.latLng(ring[i - 1][1], ring[i - 1][0]).distanceTo(L.latLng(ring[i][1], ring[i][0]));
    }
    if (closed && ring.length > 2) {
        const a = ring[ring.length - 1], b = ring[0];
        if (a[0] !== b[0] || a[1] !== b[1]) {
            total += L.latLng(a[1], a[0]).distanceTo(L.latLng(b[1], b[0]));
        }
    }
    return total;
}

function geometryAreaStats(geometry) {
    // Returns { area, perimeter } in m² / m for Polygon & MultiPolygon, otherwise null
    if (!geometry) return null;
    let polygons;
    if (geometry.type === 'Polygon') polygons = [geometry.coordinates];
    else if (geometry.type === 'MultiPolygon') polygons = geometry.coordinates;
    else return null;

    let area = 0, perimeter = 0;
    polygons.forEach(function(rings) {
        rings.forEach(function(ring, index) {
            const ringArea = ringAreaM2(ring);
            area += index === 0 ? ringArea : -ringArea;   // holes are subtracted
            perimeter += ringLengthM(ring, true);
        });
    });
    return { area: Math.max(area, 0), perimeter: perimeter };
}

function formatAreaM2(m2) {
    const fmt = (value, digits) => value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return fmt(m2, 0) + ' m² · ' + fmt(m2 / 10000, 2) + ' ha';
}

function formatDistanceM(m) {
    if (m >= 1000) return (m / 1000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' km';
    return m.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' m';
}

const BASE_LAYER_STORAGE_KEY = 'fis.baseLayer';

function addBaseLayers(targetMap, options) {
    const esriAttribution = 'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community';
    const esriImagery = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
    const esriLabels = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';

    // Separate tile-layer instances per base layer so the layer control never marks two as active
    const baseLayers = {
        'Street / خريطة': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        }),
        'Satellite / قمر صناعي': L.tileLayer(esriImagery, {
            attribution: esriAttribution,
            maxNativeZoom: 18,
            maxZoom: 19
        }),
        'Hybrid / هجين': L.layerGroup([
            L.tileLayer(esriImagery, { attribution: esriAttribution, maxNativeZoom: 18, maxZoom: 19 }),
            L.tileLayer(esriLabels, { maxNativeZoom: 18, maxZoom: 19 })
        ]),
        'Terrain / تضاريس': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)',
            maxNativeZoom: 17,
            maxZoom: 19
        })
    };

    let saved = null;
    try { saved = window.localStorage.getItem(BASE_LAYER_STORAGE_KEY); } catch (e) { /* storage unavailable */ }
    const initialName = saved && baseLayers[saved] ? saved : Object.keys(baseLayers)[0];
    baseLayers[initialName].addTo(targetMap);

    if (options.layerSwitcher === false) return;

    L.control.layers(baseLayers, null, { position: 'topright', collapsed: true }).addTo(targetMap);
    targetMap.on('baselayerchange', function(event) {
        try { window.localStorage.setItem(BASE_LAYER_STORAGE_KEY, event.name); } catch (e) { /* ignore */ }
    });
}

function initMap(elementId, geojsonUrl, options = {}) {
    const mapElement = document.getElementById(elementId);
    if (!mapElement) return null;

    const mapHeight = options.height || '500px';
    mapElement.style.height = mapHeight;

    map = L.map(elementId).setView(options.center || [34.0, -6.0], options.zoom || 13);

    addBaseLayers(map, options);

    // Add scale bar
    L.control.scale().addTo(map);

    // Drawn items layer
    drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    if (options.editor) {
        const drawControl = new L.Control.Draw({
            position: 'topleft',
            draw: {
                polygon: { allowIntersection: false, showArea: true },
                polyline: { allowIntersection: false },
                marker: true,
                rectangle: false,
                circle: false,
                circlemarker: false
            },
            edit: {
                featureGroup: drawnItems,
                edit: false,
                remove: true
            }
        });
        map.addControl(drawControl);
        map.on(L.Draw.Event.CREATED, function(event) {
            drawnItems.addLayer(event.layer);
            if (options.onDrawCreated) {
                options.onDrawCreated(event.layer, event.layerType);
            }
        });

        if (options.ruler) {
            initRulerTool(drawControl);
        }
    }

    // Popups should not compete with the ruler while measuring
    map.on('popupopen', function() {
        if (mapRulerActive) map.closePopup();
    });

    mapSelectionMode = false;
    if (options.selectable) {
        const selectionControl = L.control({ position: 'topright' });
        selectionControl.onAdd = function() {
            const container = L.DomUtil.create('div', 'leaflet-bar geometry-map-select');
            const button = L.DomUtil.create('button', 'geometry-map-select-button', container);
            button.type = 'button';
            button.title = 'Select elements on map';
            button.setAttribute('aria-label', 'Select elements on map');
            button.setAttribute('aria-pressed', 'false');
            button.innerHTML = '<i class="bi bi-cursor"></i>';
            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.on(button, 'click', function(event) {
                L.DomEvent.stop(event);
                mapSelectionMode = !mapSelectionMode;
                button.classList.toggle('active', mapSelectionMode);
                button.setAttribute('aria-pressed', String(mapSelectionMode));
                map.getContainer().classList.toggle('geometry-map-select-mode', mapSelectionMode);
            });
            return container;
        };
        selectionControl.addTo(map);

        // Action buttons (Split / Merge / Swap) live in their own group below Select
        const actions = buildMapActionButtons(options);
        if (actions.length) {
            const actionsControl = L.control({ position: 'topright' });
            actionsControl.onAdd = function() {
                const group = L.DomUtil.create('div', 'leaflet-bar geometry-map-select geometry-map-actions');
                L.DomEvent.disableClickPropagation(group);
                actions.forEach(function(action) {
                    const actionButton = L.DomUtil.create('button', 'geometry-map-select-button geometry-map-action-button', group);
                    actionButton.type = 'button';
                    actionButton.title = action.title;
                    actionButton.setAttribute('aria-label', action.title);
                    actionButton.innerHTML = '<i class="bi ' + action.icon + '"></i>';
                    L.DomEvent.on(actionButton, 'click', function(event) {
                        L.DomEvent.stop(event);
                        action.onClick();
                    });
                });
                return group;
            };
            actionsControl.addTo(map);
            createMapActionPanel(options);
        }
    }

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
            } else if (type.includes('valve') || type.includes('pipe') || type === 'dripline' || type.includes('air') || type.includes('pressure') || type === 'reduction' || type === 'other') {
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
                const properties = feature.properties;
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
                if (options.showArea) {
                    const stats = geometryAreaStats(feature.geometry);
                    if (stats) {
                        popupContent += `<br><small><b>Area / المساحة:</b> ${formatAreaM2(stats.area)}</small>`;
                        popupContent += `<br><small><b>Perimeter / المحيط:</b> ${formatDistanceM(stats.perimeter)}</small>`;
                    }
                }
                layer.bindPopup(popupContent);

                if (options.selectable && type === options.selectable.type && properties.id !== undefined) {
                    const checkbox = Array.from(document.querySelectorAll(options.selectable.checkboxSelector))
                        .find((input) => input.value === String(properties.id));
                    const setSelectedStyle = (selected) => {
                        layer._geometrySelected = selected;
                        if (layer.setStyle) {
                            layer.setStyle({
                                color: selected ? '#f08c00' : properties.color || '#3388ff',
                                weight: selected ? 4 : 2,
                                fillOpacity: selected ? 0.4 : 0.2
                            });
                        }
                    };

                    if (checkbox) {
                        setSelectedStyle(checkbox.checked);
                        checkbox.addEventListener('change', () => setSelectedStyle(checkbox.checked));
                    }

                    layer.on('click', function(event) {
                        if (!mapSelectionMode || mapRulerActive) return;
                        if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
                        if (checkbox) {
                            checkbox.checked = !checkbox.checked;
                            checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                        } else {
                            setSelectedStyle(!layer._geometrySelected);
                        }
                    });
                }
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

// ---------- Map actions: Split / Merge / Swap on the selected features ----------
// options.mapActions = {
//   idField: 'sector_ids',                     // form field used for Split / Merge
//   item: { en: 'sector', ar: 'قطاع' },        // wording used in the panel
//   urls: { split: '...', merge: '...', swap: '...' },   // omit one to hide its button
//   splitOptions: [2, 3, 4]
// }
const MAP_ACTION_DEFS = {
    split: { icon: 'bi-scissors', btn: 'btn-warning', label: 'Split / تقسيم', title: 'Split / تقسيم', min: 1, max: Infinity, parts: true,
             hint: 'Select at least 1 / اختر واحدا على الأقل' },
    merge: { icon: 'bi-union', btn: 'btn-primary', label: 'Merge / دمج', title: 'Merge / دمج', min: 2, max: Infinity, parts: false,
             hint: 'Select at least 2 / اختر اثنين على الأقل' },
    swap:  { icon: 'bi-arrow-left-right', btn: 'btn-primary', label: 'Swap / تبديل', title: 'Swap / تبديل', min: 2, max: 2, parts: false,
             hint: 'Select exactly 2 / اختر اثنين بالضبط' }
};

let mapActionPanel = null;
let mapActionMode = null;
let mapActionConfig = null;

function buildMapActionButtons(options) {
    const config = options.mapActions;
    if (!config || !config.urls) return [];
    return ['split', 'merge', 'swap']
        .filter(function(mode) { return config.urls[mode]; })
        .map(function(mode) {
            const def = MAP_ACTION_DEFS[mode];
            return { icon: def.icon, title: def.title, onClick: function() { toggleMapActionPanel(mode); } };
        });
}

function createMapActionPanel(options) {
    mapActionConfig = options.mapActions;
    mapActionConfig.checkboxSelector = options.selectable.checkboxSelector;
    const splitOptions = mapActionConfig.splitOptions || [2, 3, 4];

    const control = L.control({ position: 'topright' });
    control.onAdd = function() {
        const panel = L.DomUtil.create('div', 'geometry-map-split-panel');
        panel.hidden = true;
        panel.innerHTML = `
            <div class="fw-bold mb-1" data-role="title"></div>
            <div class="small mb-2" data-role="info"></div>
            <div data-role="parts-wrap">
                <label class="form-label small mb-1">Parts / الأجزاء</label>
                <select class="form-select form-select-sm mb-2" data-role="parts">
                    ${splitOptions.map(n => `<option value="${n}">${n}</option>`).join('')}
                </select>
            </div>
            <div class="d-flex gap-1">
                <button type="button" class="btn btn-sm flex-fill" data-role="apply"></button>
                <button type="button" class="btn btn-secondary btn-sm" data-role="cancel" aria-label="Close">&times;</button>
            </div>`;
        L.DomEvent.disableClickPropagation(panel);
        L.DomEvent.disableScrollPropagation(panel);
        panel.querySelector('[data-role="cancel"]').addEventListener('click', closeMapActionPanel);
        panel.querySelector('[data-role="apply"]').addEventListener('click', submitMapAction);
        mapActionPanel = panel;
        return panel;
    };
    control.addTo(map);
}

function closeMapActionPanel() {
    if (mapActionPanel) mapActionPanel.hidden = true;
    mapActionMode = null;
}

function selectedMapItems() {
    return Array.from(document.querySelectorAll(mapActionConfig.checkboxSelector + ':checked'));
}

function toggleMapActionPanel(mode) {
    if (!mapActionPanel) return;
    if (!mapActionPanel.hidden && mapActionMode === mode) { closeMapActionPanel(); return; }

    const def = MAP_ACTION_DEFS[mode];
    const item = mapActionConfig.item || { en: 'item', ar: 'عنصر' };
    const count = selectedMapItems().length;
    const ok = count >= def.min && count <= def.max;

    mapActionMode = mode;
    mapActionPanel.querySelector('[data-role="title"]').innerHTML = `<i class="bi ${def.icon}"></i> ${def.title}`;
    mapActionPanel.querySelector('[data-role="info"]').textContent =
        ok ? `${count} ${item.en}(s) selected / ${count} ${item.ar} محدد`
           : `${def.hint}. Use the Select button first / استخدم زر التحديد أولا`;
    mapActionPanel.querySelector('[data-role="parts-wrap"]').hidden = !def.parts;
    const apply = mapActionPanel.querySelector('[data-role="apply"]');
    apply.className = `btn btn-sm flex-fill ${def.btn}`;
    apply.textContent = def.label;
    apply.disabled = !ok;
    mapActionPanel.hidden = false;
}

function submitMapAction() {
    const def = MAP_ACTION_DEFS[mapActionMode];
    const selected = selectedMapItems();
    if (!def || selected.length < def.min || selected.length > def.max) return;

    const form = document.createElement('form');
    form.method = 'POST';
    form.action = mapActionConfig.urls[mapActionMode];
    const add = (name, value) => {
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = name; input.value = value;
        form.appendChild(input);
    };
    if (mapActionMode === 'swap') {
        add('id1', selected[0].value);
        add('id2', selected[1].value);
    } else {
        selected.forEach(cb => add(mapActionConfig.idField, cb.value));
    }
    if (def.parts) add('parts', mapActionPanel.querySelector('[data-role="parts"]').value);
    document.body.appendChild(form);
    form.submit();
}

function initRulerTool(drawControl) {
    const rulerLayer = L.layerGroup().addTo(map);
    const state = { points: [], line: null, preview: null, cursor: null, finished: false };

    // Button joins the Leaflet.draw toolbar (polyline / polygon / marker / ruler)
    const toolbar = drawControl._container.querySelector('.leaflet-draw-toolbar');
    const button = L.DomUtil.create('a', 'geometry-ruler-button');
    button.href = '#';
    button.title = 'Measure distance (double-click to finish) / قياس المسافة';
    button.setAttribute('role', 'button');
    button.setAttribute('aria-label', 'Measure distance');
    button.innerHTML = '<i class="bi bi-rulers"></i>';
    const marker = toolbar.querySelector('.leaflet-draw-draw-marker');
    if (marker) marker.insertAdjacentElement('afterend', button);
    else toolbar.appendChild(button);
    L.DomEvent.disableClickPropagation(button);

    function totalLength(points) {
        let total = 0;
        for (let i = 1; i < points.length; i++) total += points[i - 1].distanceTo(points[i]);
        return total;
    }

    function resetMeasurement() {
        rulerLayer.clearLayers();
        state.points = [];
        state.line = null;
        state.preview = null;
        state.cursor = null;
        state.finished = false;
    }

    function addVertexLabel(latlng, text, permanentClass) {
        const dot = L.circleMarker(latlng, {
            radius: 4, color: '#c92a2a', weight: 2, fillColor: '#fff', fillOpacity: 1, interactive: false
        }).addTo(rulerLayer);
        dot.bindTooltip(text, {
            permanent: true, direction: 'right', offset: [8, 0],
            className: 'geometry-ruler-tooltip ' + (permanentClass || '')
        }).openTooltip();
        return dot;
    }

    function setActive(active) {
        mapRulerActive = active;
        button.classList.toggle('active', active);
        map.getContainer().classList.toggle('geometry-map-ruler-mode', active);
        if (active) {
            // Stop any running draw tool and selection mode interference
            Object.values(drawControl._toolbars || {}).forEach(function(tb) {
                if (tb && typeof tb.disable === 'function') tb.disable();
            });
            map.doubleClickZoom.disable();
            map.closePopup();
        } else {
            map.doubleClickZoom.enable();
            resetMeasurement();
        }
    }

    function finish() {
        if (state.finished || !state.points.length) return;
        state.finished = true;
        if (state.preview) { rulerLayer.removeLayer(state.preview); state.preview = null; }
        if (state.cursor) { rulerLayer.removeLayer(state.cursor); state.cursor = null; }
        const last = state.points[state.points.length - 1];
        if (state.points.length > 1) {
            addVertexLabel(last, 'Total / المجموع: ' + formatDistanceM(totalLength(state.points)), 'total');
        }
    }

    L.DomEvent.on(button, 'click', function(event) {
        L.DomEvent.stop(event);
        setActive(!mapRulerActive);
    });

    // A Leaflet.draw tool starting cancels the ruler
    map.on(L.Draw.Event.DRAWSTART, function() { if (mapRulerActive) setActive(false); });

    map.on('click', function(event) {
        if (!mapRulerActive) return;
        if (state.finished) resetMeasurement();

        const latlng = event.latlng;
        const last = state.points[state.points.length - 1];
        if (last && last.distanceTo(latlng) < 0.05) return;   // ignore the duplicate click of a double-click

        state.points.push(latlng);
        if (!state.line) {
            state.line = L.polyline(state.points, { color: '#c92a2a', weight: 3, interactive: false }).addTo(rulerLayer);
        } else {
            state.line.setLatLngs(state.points);
        }
        addVertexLabel(latlng, state.points.length === 1 ? 'Start / البداية' : formatDistanceM(totalLength(state.points)));
    });

    map.on('mousemove', function(event) {
        if (!mapRulerActive || state.finished || !state.points.length) return;
        const last = state.points[state.points.length - 1];
        if (!state.preview) {
            state.preview = L.polyline([last, event.latlng], {
                color: '#c92a2a', weight: 2, dashArray: '6 6', interactive: false
            }).addTo(rulerLayer);
            state.cursor = L.circleMarker(event.latlng, { radius: 0, opacity: 0, fillOpacity: 0, interactive: false }).addTo(rulerLayer);
            state.cursor.bindTooltip('', {
                permanent: true, direction: 'right', offset: [12, 0], className: 'geometry-ruler-tooltip live'
            }).openTooltip();
        }
        state.preview.setLatLngs([last, event.latlng]);
        state.cursor.setLatLng(event.latlng);
        const segment = last.distanceTo(event.latlng);
        state.cursor.setTooltipContent(
            formatDistanceM(totalLength(state.points) + segment) + ' (+' + formatDistanceM(segment) + ')'
        );
    });

    map.on('dblclick', function() { if (mapRulerActive) finish(); });

    document.addEventListener('keydown', function(event) {
        if (!mapRulerActive) return;
        if (event.key === 'Enter') finish();
        if (event.key === 'Escape') setActive(false);
    });
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

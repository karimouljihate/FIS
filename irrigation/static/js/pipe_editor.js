// Irrigation Planner - Pipe (Main Pipe / Zone Pipe) map editor
// Adds in-map controls: Add, (optional) Trace Main/Zone Pipe, Edit, Rename, Remove.
// Selection-aware enable/disable for Add / Edit / Rename / Remove.
// Depends on map.js globals (map, drawnItems) and the shared pipe editor modals.

(function () {
    let cfg = null;
    let activeCreatedHandler = null;
    const btnRefs = {};

    function el(id) {
        return id ? document.getElementById(id) : null;
    }

    function checkboxSelector() {
        return (cfg && cfg.checkboxSelector) || '.pipe-checkbox';
    }

    function allCheckboxes() {
        return Array.from(document.querySelectorAll(checkboxSelector()));
    }

    function checkedCheckboxes() {
        return allCheckboxes().filter(function (cb) { return cb.checked; });
    }

    function itemLabel() {
        return (cfg && cfg.item) || { en: 'pipe', ar: 'أنبوب' };
    }

    function showModal(id) {
        const modal = el(id);
        if (modal && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    }

    function tracePolyline(onComplete) {
        if (typeof map === 'undefined' || !map) return;
        if (activeCreatedHandler) {
            map.off(L.Draw.Event.CREATED, activeCreatedHandler);
            activeCreatedHandler = null;
        }
        const drawer = new L.Draw.Polyline(map, { allowIntersection: false });
        drawer.enable();
        activeCreatedHandler = function (event) {
            map.off(L.Draw.Event.CREATED, activeCreatedHandler);
            activeCreatedHandler = null;
            if (typeof drawnItems !== 'undefined' && drawnItems) {
                drawnItems.clearLayers();
                drawnItems.addLayer(event.layer);
            }
            const coords = event.layer.toGeoJSON().geometry.coordinates;
            onComplete(coords, JSON.stringify(coords));
        };
        map.on(L.Draw.Event.CREATED, activeCreatedHandler);
    }

    function openAdd() {
        if (!cfg) return;
        showModal(cfg.addModalId);
    }

    function traceMain() {
        if (!cfg || !cfg.trace || !cfg.trace.main) return;
        const target = cfg.trace.main;
        tracePolyline(function (coords, coordsStr) {
            const field = el(target.fieldId);
            if (field) field.value = coordsStr;
            showModal(target.modalId);
        });
    }

    function traceZone() {
        if (!cfg || !cfg.trace || !cfg.trace.zone) return;
        const target = cfg.trace.zone;
        tracePolyline(function (coords, coordsStr) {
            const field = el(target.fieldId);
            if (field) field.value = coordsStr;
            showModal(target.modalId);
        });
    }

    function openEdit() {
        if (!cfg) return;
        const selected = checkedCheckboxes();
        if (selected.length !== 1) {
            alert('Select exactly one ' + itemLabel().en + ' to edit. / اختر عنصرا واحدا للتعديل.');
            return;
        }
        const cb = selected[0];
        el('pipeEditId').value = cb.value;
        el('pipeEditDiameter').value = cb.dataset.diameter || '';
        el('pipeEditCoords').value = cb.dataset.coords || '';
        el('pipeEditForm').action = cfg.urls.update;
        showModal('pipeEditModal');
    }

    function openRenameById(id) {
        if (!cfg) return;
        const cb = allCheckboxes().find(function (input) { return input.value === String(id); });
        el('pipeRenameId').value = id;
        el('pipeRenameNameEn').value = cb ? (cb.dataset.nameEn || '') : '';
        el('pipeRenameNameAr').value = cb ? (cb.dataset.nameAr || '') : '';
        el('pipeRenameForm').action = cfg.urls.rename;
        showModal('pipeRenameModal');
    }

    function openRename() {
        const selected = checkedCheckboxes();
        if (selected.length !== 1) {
            alert('Select exactly one ' + itemLabel().en + ' to rename. / اختر عنصرا واحدا لإعادة التسمية.');
            return;
        }
        openRenameById(selected[0].value);
    }

    function removeByIds(ids) {
        if (!cfg || !ids.length) return;
        if (cfg.removeAjax === false) {
            const form = el('pipeRemoveForm');
            form.action = cfg.urls.remove;
            form.querySelectorAll('input[name="pipe_ids"]').forEach(function (input) { input.remove(); });
            ids.forEach(function (id) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'pipe_ids';
                input.value = id;
                form.appendChild(input);
            });
            form.requestSubmit();
            return;
        }
        askConfirm(
            'Remove the selected pipe(s)? This cannot be undone. / إزالة الأنبوب (الأنابيب) المحددة؟ لا يمكن التراجع.',
            function () { ajaxRemove(ids); }
        );
    }

    // Reuse the global confirmation modal from base.html.
    function askConfirm(message, onConfirm) {
        const modalEl = el('confirmActionModal');
        const messageEl = el('confirmActionMessage');
        const acceptEl = el('confirmActionAccept');
        if (!modalEl || !messageEl || !acceptEl || !window.bootstrap) {
            if (window.confirm(message)) onConfirm();
            return;
        }
        messageEl.textContent = message;
        let handled = false;
        const handleAccept = function () {
            if (handled) return;
            handled = true;
            acceptEl.removeEventListener('click', handleAccept);
            onConfirm();
        };
        acceptEl.addEventListener('click', handleAccept);
        modalEl.addEventListener('hidden.bs.modal', function onHidden() {
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            if (!handled) {
                handled = true;
                acceptEl.removeEventListener('click', handleAccept);
            }
        });
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

    function ajaxRemove(ids) {
        const body = new URLSearchParams();
        ids.forEach(function (id) { body.append('pipe_ids', id); });
        fetch(cfg.urls.remove, {
            method: 'POST',
            body: body,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
        .then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        })
        .then(function () { applyPipeRemoval(ids); })
        .catch(function () { window.location.reload(); });
    }

    function applyPipeRemoval(ids) {
        const idSet = {};
        ids.forEach(function (id) { idSet[String(id)] = true; });

        allCheckboxes().forEach(function (cb) {
            if (idSet[String(cb.value)]) {
                const tr = cb.closest('tr');
                if (tr) tr.remove();
            }
        });

        if (typeof featureLayer !== 'undefined' && featureLayer) {
            featureLayer.eachLayer(function (layer) {
                const props = layer.feature && layer.feature.properties;
                if (props && idSet[String(props.id)]) featureLayer.removeLayer(layer);
            });
        }

        const selectAll = el('selectAllPipes');
        if (selectAll) selectAll.checked = false;

        if (!allCheckboxes().length) showEmptyTableState();
        updateSelectionState();
    }

    function showEmptyTableState() {
        const table = el('pipeTable');
        if (!table) return;
        const tbody = table.querySelector('tbody');
        if (!tbody || tbody.querySelector('tr')) return;
        const colspan = table.querySelectorAll('thead th').length || 1;
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = colspan;
        cell.className = 'text-center text-muted py-3';
        cell.textContent = emptyMessage();
        row.appendChild(cell);
        tbody.appendChild(row);
    }

    function emptyMessage() {
        return (cfg && cfg.emptyLabel) || 'No pipes yet / لا توجد أنابيب بعد';
    }

    function removeSelected() {
        const selected = checkedCheckboxes();
        if (!selected.length) {
            alert('Select at least one ' + itemLabel().en + ' first. / اختر عنصرا واحدا على الأقل أولا.');
            return;
        }
        removeByIds(selected.map(function (cb) { return cb.value; }));
    }

    function setButtonEnabled(role, enabled) {
        const mapButton = btnRefs[role];
        if (mapButton) {
            mapButton.disabled = !enabled;
        }
        const selector = cfg.selectors && cfg.selectors[role];
        if (selector) {
            const toolbarButton = document.querySelector(selector);
            if (toolbarButton) {
                toolbarButton.disabled = !enabled;
            }
        }
    }

    function updateSelectionState() {
        if (!cfg) return;
        const count = checkedCheckboxes().length;
        setButtonEnabled('add', count === 0);
        setButtonEnabled('edit', count === 1);
        setButtonEnabled('rename', count === 1);
        setButtonEnabled('remove', count >= 1);
    }

    function buildControl() {
        if (typeof map === 'undefined' || !map) return;

        const buttons = [
            { role: 'add', icon: 'bi-plus-circle', title: 'Add / إضافة', onClick: openAdd }
        ];

        if (cfg.traceButtons !== false) {
            if (cfg.trace && cfg.trace.main) {
                buttons.push({ icon: 'bi-water', title: 'Trace Main Pipe / رسم الأنبوب الرئيسي', onClick: traceMain });
            }
            if (cfg.trace && cfg.trace.zone) {
                buttons.push({ icon: 'bi-droplet', title: 'Trace Zone Pipe / رسم أنبوب المنطقة', onClick: traceZone });
            }
        }

        buttons.push(
            { role: 'edit', icon: 'bi-pencil', title: 'Edit / Change / تعديل', onClick: openEdit },
            { role: 'rename', icon: 'bi-input-cursor-text', title: 'Rename / إعادة تسمية', onClick: openRename },
            { role: 'remove', icon: 'bi-trash', title: 'Remove / إزالة', onClick: removeSelected }
        );

        const control = L.control({ position: 'topright' });
        control.onAdd = function () {
            const group = L.DomUtil.create('div', 'leaflet-bar geometry-map-select geometry-map-actions pipe-map-actions');
            L.DomEvent.disableClickPropagation(group);
            L.DomEvent.disableScrollPropagation(group);
            buttons.forEach(function (button) {
                const actionButton = L.DomUtil.create('button', 'geometry-map-select-button geometry-map-action-button', group);
                actionButton.type = 'button';
                actionButton.title = button.title;
                actionButton.setAttribute('aria-label', button.title);
                actionButton.innerHTML = '<i class="bi ' + button.icon + '"></i>';
                if (button.role) {
                    btnRefs[button.role] = actionButton;
                }
                L.DomEvent.on(actionButton, 'click', function (event) {
                    L.DomEvent.stop(event);
                    if (!actionButton.disabled) button.onClick();
                });
            });
            return group;
        };
        control.addTo(map);
    }

    window.initPipeEditor = function (config) {
        cfg = config || {};
        buildControl();

        document.addEventListener('change', function (event) {
            if (event.target && event.target.matches && event.target.matches(checkboxSelector())) {
                updateSelectionState();
            }
        });

        updateSelectionState();
    };

    window.toggleAllPipes = function (source) {
        allCheckboxes().forEach(function (cb) {
            cb.checked = source.checked;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
    };

    window.openPipeRename = function (id) { openRenameById(id); };
    window.removePipeById = function (id) { removeByIds([id]); };
    window.pipeEditorRenameSelected = function () { openRename(); };
    window.pipeEditorRemoveSelected = function () { removeSelected(); };
})();
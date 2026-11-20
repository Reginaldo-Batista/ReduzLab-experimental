(() => {
    const config = JSON.parse(document.getElementById('comparison-config').textContent);
    const cards = new Map(Array.from(document.querySelectorAll('[data-algorithm]'),
        card => [card.dataset.algorithm, card]));
    const progress = document.getElementById('comparison-progress');
    const connectionError = document.getElementById('comparison-connection-error');
    const terminal = new Set(['completed', 'failed', 'interrupted']);
    const grid = document.querySelector('.comparison-grid');
    const columnInputs = Array.from(document.querySelectorAll('input[name="comparison-columns"]'));
    const storageKey = 'reduzlab.comparison.columns';
    const validColumns = new Set(['1', '2', '3']);
    const syncInput = document.getElementById('comparison-sync');
    const visibilityKey = `reduzlab.comparison.visibility.${config.statusUrl}`;
    const modeKey = `reduzlab.comparison.visibility-mode.${config.statusUrl}`;
    const hiddenClasses = new Set();
    const localClasses = new Map();
    let syncEnabled = true;
    let waitingForInteraction = false;
    const plots = new Map();
    const frameLoads = new WeakMap();
    try {
        const saved = JSON.parse(sessionStorage.getItem(visibilityKey) || '[]');
        if (Array.isArray(saved) && saved.every(name => typeof name === 'string')) {
            for (const name of saved) hiddenClasses.add(name);
        }
        const mode = JSON.parse(sessionStorage.getItem(modeKey) || 'null');
        if (mode && typeof mode.enabled === 'boolean' && typeof mode.waiting === 'boolean' && Array.isArray(mode.plots)) {
            syncEnabled = mode.enabled;
            waitingForInteraction = syncEnabled && mode.waiting;
            for (const entry of mode.plots) {
                if (Array.isArray(entry) && typeof entry[0] === 'string' && Array.isArray(entry[1]) &&
                    entry[1].every(name => typeof name === 'string')) localClasses.set(entry[0], new Set(entry[1]));
            }
        }
    } catch (_error) {
        // Storage can be disabled; synchronization still works for this page.
    }
    syncInput.checked = syncEnabled;

    function saveVisibility() {
        try {
            sessionStorage.setItem(visibilityKey, JSON.stringify(Array.from(hiddenClasses)));
            sessionStorage.setItem(modeKey, JSON.stringify({enabled: syncEnabled, waiting: waitingForInteraction,
                plots: Array.from(localClasses, ([name, classes]) => [name, Array.from(classes)])}));
        } catch (_error) {
            // Keep the selection in memory when storage is unavailable.
        }
    }

    function visible(value) {
        return value !== false && value !== 'legendonly';
    }

    function signature(update, indices) {
        if (!update || !Object.prototype.hasOwnProperty.call(update, 'visible') || !Array.isArray(indices)) return null;
        const values = Array.isArray(update.visible) ? update.visible : [update.visible];
        if (!values.length) return null;
        return JSON.stringify(indices.map((index, i) => [index, visible(values[i % values.length])]));
    }

    function reconcile(plot) {
        if (plot.busy || !plot.iframe.isConnected) return;
        const indices = [];
        const values = [];
        const selection = syncEnabled && !waitingForInteraction ? hiddenClasses : localClasses.get(plot.name);
        plot.graph.data.forEach((trace, index) => {
            if (typeof trace.name !== 'string') return;
            const desired = !selection.has(trace.name);
            if (visible(trace.visible) !== desired) {
                indices.push(index);
                values.push(desired ? true : 'legendonly');
            }
        });
        if (!indices.length) return;
        const update = {visible: values};
        plot.busy = true;
        plot.expected = signature(update, indices);
        try {
            Promise.resolve(plot.window.Plotly.restyle(plot.graph, update, indices)).then(() => {
                plot.busy = false;
                plot.expected = null;
                reconcile(plot);
            }, () => {
                plot.busy = false;
                plot.expected = null;
            });
        } catch (_error) {
            plot.busy = false;
            plot.expected = null;
        }
    }

    function reconcileAll() {
        for (const [iframe, plot] of plots) {
            if (!iframe.isConnected) plots.delete(iframe);
            else reconcile(plot);
        }
    }

    syncInput.addEventListener('change', () => {
        if (!syncInput.checked && syncEnabled && !waitingForInteraction) {
            for (const plot of plots.values()) {
                // Capture the desired shared state, including any redraw still in flight.
                localClasses.set(plot.name, new Set(hiddenClasses));
            }
        }
        syncEnabled = syncInput.checked;
        waitingForInteraction = syncEnabled;
        saveVisibility();
        reconcileAll();
    });

    function watchPlot(iframe) {
        const token = {};
        frameLoads.set(iframe, token);
        plots.delete(iframe);
        let attempts = 0;
        function attach() {
            if (!iframe.isConnected || frameLoads.get(iframe) !== token) return;
            try {
                const frameWindow = iframe.contentWindow;
                const graph = frameWindow.document.querySelector('.plotly-graph-div');
                if (frameWindow.Plotly && graph && typeof graph.on === 'function' &&
                    Array.isArray(graph.data) && graph._fullLayout && graph._fullLayout._replotting === false) {
                    const name = iframe.closest('[data-algorithm]').dataset.algorithm;
                    if (!localClasses.has(name)) localClasses.set(name, new Set());
                    const plot = {iframe, window: frameWindow, graph, name, busy: false, expected: null};
                    plots.set(iframe, plot);
                    graph.on('plotly_restyle', event => {
                        if (plots.get(iframe) !== plot || !Array.isArray(event)) return;
                        const [update, indices] = event;
                        const changed = signature(update, indices);
                        if (!changed) return;
                        if (plot.expected === changed) {
                            plot.expected = null;
                            return;
                        }
                        const values = Array.isArray(update.visible) ? update.visible : [update.visible];
                        const local = localClasses.get(name);
                        indices.forEach((index, i) => {
                            const trace = graph.data[index];
                            if (!trace || typeof trace.name !== 'string') return;
                            const shown = visible(values[i % values.length]);
                            if (shown) local.delete(trace.name);
                            else local.add(trace.name);
                            if (syncEnabled && !waitingForInteraction) {
                                if (shown) hiddenClasses.delete(trace.name);
                                else hiddenClasses.add(trace.name);
                            }
                        });
                        if (syncEnabled && waitingForInteraction) {
                            hiddenClasses.clear();
                            for (const className of local) hiddenClasses.add(className);
                            waitingForInteraction = false;
                        }
                        saveVisibility();
                        reconcileAll();
                    });
                    reconcile(plot);
                    return;
                }
            } catch (_error) {
                // A failed or inaccessible iframe must not stop the other results.
                return;
            }
            if (++attempts < 300) setTimeout(attach, 100);
        }
        attach();
    }

    function applyColumns(value) {
        const columns = validColumns.has(value) ? value : '2';
        grid.dataset.columns = columns;
        for (const input of columnInputs) input.checked = input.value === columns;
        // Apply the new dimensions before asking embedded Plotly charts to resize.
        for (const iframe of grid.querySelectorAll('iframe')) {
            iframe.getBoundingClientRect();
            resizePlot(iframe);
        }
    }

    try {
        applyColumns(localStorage.getItem(storageKey));
    } catch (_error) {
        applyColumns('2');
    }
    for (const input of columnInputs) {
        input.addEventListener('change', () => {
            if (!input.checked) return;
            applyColumns(input.value);
            try {
                localStorage.setItem(storageKey, input.value);
            } catch (_error) {
                // The chosen layout still applies for this page when storage is blocked.
            }
        });
    }

    function resizePlot(iframe) {
        try {
            const frameWindow = iframe.contentWindow;
            if (!frameWindow) return;
            const graph = frameWindow.document.querySelector('.plotly-graph-div');
            if (graph && graph._fullLayout && frameWindow.Plotly) {
                const resizing = frameWindow.Plotly.Plots.resize(graph);
                if (resizing && typeof resizing.catch === 'function') resizing.catch(() => {});
            } else {
                frameWindow.dispatchEvent(new frameWindow.Event('resize'));
            }
        } catch (_error) {
            // The embedded document may no longer be same-origin or available.
        }
    }

    function preparePlot(iframe) {
        try {
            const document = iframe.contentDocument;
            const graph = document && document.querySelector('.plotly-graph-div');
            if (graph) {
                graph.parentElement.classList.add('reduzlab-plot-container');
                const style = document.createElement('style');
                style.textContent = 'html, body, .reduzlab-plot-container { width: 100%; height: 100%; margin: 0; }' +
                    'html, body { overflow: hidden; }';
                document.head.appendChild(style);
            }
        } catch (_error) {
            // Leave documents from other origins unchanged.
        }
        resizePlot(iframe);
        watchPlot(iframe);
    }

    const resizeObserver = 'ResizeObserver' in window ? new ResizeObserver(entries => {
        for (const entry of entries) {
            const iframe = entry.target.querySelector('iframe');
            if (iframe) resizePlot(iframe);
        }
    }) : null;

    function link(url, text) {
        const a = document.createElement('a');
        a.href = url;
        a.textContent = text;
        a.className = 'btn btn-outline-secondary btn-sm me-2 mb-2';
        a.setAttribute('download', '');
        return a;
    }

    function renderResult(card, item, job) {
        const output = card.querySelector('.algorithm-output');
        if (output.dataset.rendered === item.plot_url) return;
        output.replaceChildren();
        if (item.metrics && Object.keys(item.metrics).length) {
            const heading = document.createElement('h3');
            heading.className = 'h6';
            heading.textContent = config.labels.metrics;
            const list = document.createElement('ul');
            list.className = 'list-unstyled small';
            for (const [key, value] of Object.entries(item.metrics)) {
                const li = document.createElement('li');
                const formatted = typeof value === 'number' ? value.toFixed(4) : value;
                li.textContent = `${config.labels[key] || key}: ${formatted}`;
                list.appendChild(li);
            }
            output.append(heading, list);
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'comparison-plot';
        if (item.plot_url.endsWith('.html')) {
            const iframe = document.createElement('iframe');
            iframe.src = item.plot_url;
            iframe.title = `${item.algorithm} ${config.labels.plot}`;
            iframe.addEventListener('load', () => preparePlot(iframe));
            wrapper.appendChild(iframe);
            output.appendChild(wrapper);
            if (resizeObserver) resizeObserver.observe(wrapper);
        } else {
            const image = document.createElement('img');
            image.src = item.plot_url;
            image.alt = `${item.algorithm} ${config.labels.plot}`;
            wrapper.appendChild(image);
            output.appendChild(wrapper);
        }
        const dataset = document.createElement('p');
        dataset.className = 'mb-1';
        dataset.textContent = `${config.labels.dataset} ${job.dataset_name || config.labels.unavailable}`;
        const attributes = document.createElement('p');
        attributes.className = 'text-break mb-3';
        attributes.textContent = `${config.labels.attributes} ${job.attributes ? job.attributes.join('; ') : config.labels.unavailable}`;
        output.append(dataset, attributes);
        output.appendChild(link(item.plot_url, config.labels.downloadPlot));
        if (item.csv_url) output.appendChild(link(item.csv_url, config.labels.downloadCsv));
        output.dataset.rendered = item.plot_url;
    }

    async function update() {
        try {
            const response = await fetch(config.statusUrl, {cache: 'no-store'});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const job = await response.json();
            connectionError.classList.add('d-none');
            let done = 0;
            for (const item of job.algorithms) {
                const card = cards.get(item.algorithm);
                card.querySelector('.algorithm-status').textContent = config.labels[item.status] || item.status;
                const error = card.querySelector('.algorithm-error');
                error.classList.toggle('d-none', !item.error);
                error.textContent = item.error || '';
                if (item.status === 'completed' && item.plot_url) renderResult(card, item, job);
                if (terminal.has(item.status)) done++;
            }
            progress.textContent = config.labels.progress.replace('{done}', done).replace('{total}', job.algorithms.length);
            if (['queued', 'running'].includes(job.status)) setTimeout(update, 1500);
        } catch (_error) {
            connectionError.classList.remove('d-none');
            setTimeout(update, 3000);
        }
    }
    update();
})();

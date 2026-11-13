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
            if (document && document.querySelector('.plotly-graph-div')) {
                const style = document.createElement('style');
                style.textContent = 'html, body, body > div { width: 100%; height: 100%; margin: 0; }' +
                    'html, body { overflow: hidden; }';
                document.head.appendChild(style);
            }
        } catch (_error) {
            // Leave documents from other origins unchanged.
        }
        resizePlot(iframe);
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

    function renderResult(card, item) {
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
                if (item.status === 'completed' && item.plot_url) renderResult(card, item);
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

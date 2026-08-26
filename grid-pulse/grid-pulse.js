"use strict";

const DATA_ROOT = "data";
const SVG_NS = "http://www.w3.org/2000/svg";
const FUEL_COLORS = {
    battery: "var(--fuel-battery)",
    coal: "var(--fuel-coal)",
    geothermal: "var(--fuel-geothermal)",
    hydro: "var(--fuel-hydro)",
    natural_gas: "var(--fuel-natural-gas)",
    nuclear: "var(--fuel-nuclear)",
    petroleum: "var(--fuel-petroleum)",
    solar: "var(--fuel-solar)",
    wind: "var(--fuel-wind)",
    other: "var(--fuel-other)",
};

/**
 * @typedef {{timestamp: string, actual_mwh: number|null, forecast_mwh: number|null, net_generation_mwh: number|null, net_interchange_mwh: number|null}} DemandRow
 * @typedef {{timestamp: string, total_mwh: number|null, fuels: Object<string, number|null>}} MixRow
 * @typedef {{code: string, id: string, label: string, renewable: boolean}} FuelDefinition
 * @typedef {{value: number|null, timestamp: string|null}} Metric
 * @typedef {{id: string, source_id: string, slug: string, name: string}} Region
 * @typedef {{region: Region, generated_at: string, coverage: {start: string, end: string}, kpis: {demand_mwh: Metric, forecast_error_pct: Metric, renewable_share_pct: Metric, net_interchange_mwh: Metric}, demand: DemandRow[], fuel_catalog: FuelDefinition[], generation_mix: MixRow[]}} GridSnapshot
 * @typedef {{slug: string, data_file: string}} ManifestRegion
 * @typedef {{generated_at: string, regions: ManifestRegion[], pipeline: {status: string, rows_processed: {total: number}, quality_checks: {passed: number, warning: number, failed: number, total: number}}}} GridManifest
 */

/** @type {{manifest: GridManifest|null, region: string, hours: number, snapshots: Map<string, GridSnapshot>}} */
const state = {
    manifest: null,
    region: "miso",
    hours: 168,
    snapshots: new Map(),
};

const elements = {
    themeToggle: document.querySelector("#theme-toggle"),
    themeColorMeta: document.querySelector("#theme-color-meta"),
    regionSwitcher: document.querySelector("#region-switcher"),
    rangeSwitcher: document.querySelector("#range-switcher"),
    refreshButton: document.querySelector("#refresh-data"),
    message: document.querySelector("#data-message"),
    pipelinePill: document.querySelector("#pipeline-pill"),
    pipelineLabel: document.querySelector("#pipeline-label"),
    insight: document.querySelector("#grid-insight"),
    demandValue: document.querySelector("#demand-value"),
    demandNote: document.querySelector("#demand-note"),
    forecastValue: document.querySelector("#forecast-value"),
    forecastNote: document.querySelector("#forecast-note"),
    renewableValue: document.querySelector("#renewable-value"),
    renewableNote: document.querySelector("#renewable-note"),
    interchangeValue: document.querySelector("#interchange-value"),
    interchangeNote: document.querySelector("#interchange-note"),
    demandSubtitle: document.querySelector("#demand-subtitle"),
    demandCaption: document.querySelector("#demand-caption"),
    demandChart: document.querySelector("#demand-chart"),
    mixSubtitle: document.querySelector("#mix-subtitle"),
    mixTotal: document.querySelector("#mix-total"),
    mixChart: document.querySelector("#mix-chart"),
    mixLegend: document.querySelector("#mix-legend"),
    freshnessValue: document.querySelector("#freshness-value"),
    rowsValue: document.querySelector("#rows-value"),
    checksValue: document.querySelector("#checks-value"),
    healthValue: document.querySelector("#health-value"),
    healthIcon: document.querySelector("#health-icon"),
};

function savedTheme() {
    try {
        const theme = localStorage.getItem("theme");
        return theme === "light" || theme === "dark" ? theme : null;
    } catch (error) {
        return null;
    }
}

function applyTheme(theme, persist = false) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    elements.themeColorMeta.setAttribute("content", theme === "dark" ? "#0d1518" : "#f4f7f7");
    const darkMode = theme === "dark";
    const nextTheme = darkMode ? "light" : "dark";
    elements.themeToggle.setAttribute("aria-pressed", String(darkMode));
    elements.themeToggle.setAttribute("aria-label", `Switch to ${nextTheme} mode`);
    elements.themeToggle.setAttribute("title", `Switch to ${nextTheme} mode`);
    if (persist) {
        try {
            localStorage.setItem("theme", theme);
        } catch (error) {
            // The selected theme still applies when storage is unavailable.
        }
    }
}

function configureTheme() {
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    applyTheme(document.documentElement.getAttribute("data-bs-theme") || "light");
    elements.themeToggle.addEventListener("click", () => {
        const current = document.documentElement.getAttribute("data-bs-theme");
        applyTheme(current === "dark" ? "light" : "dark", true);
    });
    systemTheme.addEventListener("change", (event) => {
        if (!savedTheme()) {
            applyTheme(event.matches ? "dark" : "light");
        }
    });
}

function showMessage(message, isError = false) {
    elements.message.textContent = message;
    elements.message.classList.add("is-visible");
    elements.message.classList.toggle("is-error", isError);
}

function hideMessage() {
    elements.message.classList.remove("is-visible", "is-error");
}

async function fetchJson(path, version = "") {
    const separator = path.includes("?") ? "&" : "?";
    const response = await fetch(
        `${path}${version ? `${separator}v=${encodeURIComponent(version)}` : ""}`,
        {cache: "no-store"},
    );
    if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
    }
    return response.json();
}

function formatInteger(value) {
    return Number.isFinite(value) ? Math.round(value).toLocaleString() : "—";
}

function formatPercent(value) {
    return Number.isFinite(value) ? value.toFixed(1) : "—";
}

function formatCompact(value) {
    if (!Number.isFinite(value)) {
        return "—";
    }
    const absolute = Math.abs(value);
    if (absolute >= 1000000) {
        return `${(value / 1000000).toFixed(absolute >= 10000000 ? 0 : 1)}M`;
    }
    if (absolute >= 1000) {
        return `${(value / 1000).toFixed(absolute >= 100000 ? 0 : 1)}K`;
    }
    return Math.round(value).toLocaleString();
}

function formatTimestamp(value, options = {}) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return "unknown time";
    }
    return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        ...options,
    }).format(date);
}

function relativeTime(value) {
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) {
        return "unknown";
    }
    const elapsedMinutes = Math.max(0, Math.round((Date.now() - timestamp) / 60000));
    if (elapsedMinutes < 2) {
        return "just now";
    }
    if (elapsedMinutes < 60) {
        return `${elapsedMinutes} min ago`;
    }
    const elapsedHours = Math.round(elapsedMinutes / 60);
    if (elapsedHours < 48) {
        return `${elapsedHours} hr ago`;
    }
    return `${Math.round(elapsedHours / 24)} days ago`;
}

function rangeLabel() {
    return state.hours === 24 ? "24 hours" : state.hours === 168 ? "7 days" : "30 days";
}

function filterWindow(rows) {
    if (!rows.length) {
        return [];
    }
    const latest = Math.max(...rows.map((row) => new Date(row.timestamp).getTime()));
    const cutoff = latest - state.hours * 60 * 60 * 1000;
    return rows.filter((row) => new Date(row.timestamp).getTime() >= cutoff);
}

function svgElement(name, attributes = {}) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
}

function addSvgText(svg, text, x, y, className, anchor = "start") {
    const node = svgElement("text", {x, y, class: className, "text-anchor": anchor});
    node.textContent = text;
    svg.appendChild(node);
    return node;
}

function buildPath(rows, field, xScale, yScale) {
    let path = "";
    let drawing = false;
    rows.forEach((row) => {
        const value = row[field];
        if (!Number.isFinite(value)) {
            drawing = false;
            return;
        }
        const x = xScale(new Date(row.timestamp).getTime());
        const y = yScale(value);
        path += `${drawing ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
        drawing = true;
    });
    return path;
}

function niceMaximum(value) {
    if (!Number.isFinite(value) || value <= 0) {
        return 1;
    }
    const magnitude = 10 ** Math.floor(Math.log10(value));
    const normalized = value / magnitude;
    const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return nice * magnitude;
}

function drawAxes(svg, bounds, yMin, yMax, xStart, xEnd) {
    const yTicks = 5;
    for (let index = 0; index <= yTicks; index += 1) {
        const ratio = index / yTicks;
        const y = bounds.bottom - ratio * (bounds.bottom - bounds.top);
        const value = yMin + ratio * (yMax - yMin);
        svg.appendChild(svgElement("line", {
            x1: bounds.left,
            x2: bounds.right,
            y1: y,
            y2: y,
            class: "chart-grid-line",
        }));
        addSvgText(svg, formatCompact(value), bounds.left - 12, y + 4, "chart-axis-text", "end");
    }

    const xTicks = 6;
    for (let index = 0; index <= xTicks; index += 1) {
        const ratio = index / xTicks;
        const x = bounds.left + ratio * (bounds.right - bounds.left);
        const timestamp = xStart + ratio * (xEnd - xStart);
        const label = new Intl.DateTimeFormat(undefined, {
            month: state.hours > 24 ? "short" : undefined,
            day: state.hours > 24 ? "numeric" : undefined,
            hour: state.hours <= 24 ? "numeric" : undefined,
        }).format(new Date(timestamp));
        const anchor = index === 0 ? "start" : index === xTicks ? "end" : "middle";
        addSvgText(svg, label, x, bounds.bottom + 25, "chart-axis-text", anchor);
    }
}

function attachLineTooltip(container, svg, rows, bounds, xStart, xEnd, xScale, yScale, yMin, yMax) {
    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.hidden = true;
    container.appendChild(tooltip);
    const crosshair = svgElement("line", {
        y1: bounds.top,
        y2: bounds.bottom,
        class: "chart-crosshair",
        visibility: "hidden",
    });
    svg.appendChild(crosshair);

    function hideTooltip() {
        tooltip.hidden = true;
        crosshair.setAttribute("visibility", "hidden");
    }

    svg.addEventListener("pointerleave", hideTooltip);
    svg.addEventListener("pointermove", (event) => {
        const rectangle = svg.getBoundingClientRect();
        const viewX = (event.clientX - rectangle.left) / rectangle.width * 1200;
        if (viewX < bounds.left || viewX > bounds.right) {
            hideTooltip();
            return;
        }
        const hoveredTime = xStart + (viewX - bounds.left) / (bounds.right - bounds.left) * (xEnd - xStart);
        const nearest = rows.reduce((best, row) => (
            Math.abs(new Date(row.timestamp).getTime() - hoveredTime) < Math.abs(new Date(best.timestamp).getTime() - hoveredTime)
                ? row
                : best
        ));
        const x = xScale(new Date(nearest.timestamp).getTime());
        crosshair.setAttribute("x1", x);
        crosshair.setAttribute("x2", x);
        crosshair.setAttribute("visibility", "visible");

        tooltip.replaceChildren();
        const heading = document.createElement("strong");
        heading.textContent = formatTimestamp(nearest.timestamp);
        const actual = document.createElement("span");
        actual.textContent = `Actual: ${formatInteger(nearest.actual_mwh)} MWh`;
        const forecast = document.createElement("span");
        forecast.textContent = `Forecast: ${formatInteger(nearest.forecast_mwh)} MWh`;
        tooltip.append(heading, actual, forecast);
        tooltip.hidden = false;
        tooltip.style.left = `${x / 1200 * 100}%`;
        const tooltipValue = Number.isFinite(nearest.actual_mwh)
            ? nearest.actual_mwh
            : Number.isFinite(nearest.forecast_mwh) ? nearest.forecast_mwh : (yMin + yMax) / 2;
        tooltip.style.top = `${yScale(tooltipValue) / 360 * 100}%`;
    });
}

function renderDemandChart(snapshot) {
    const rows = filterWindow(snapshot.demand);
    elements.demandChart.replaceChildren();
    if (!rows.length) {
        elements.demandChart.innerHTML = '<div class="chart-empty">No demand observations are available.</div>';
        return;
    }

    const width = 1200;
    const height = 360;
    const bounds = {left: 72, right: 1175, top: 24, bottom: 315};
    const timestamps = rows.map((row) => new Date(row.timestamp).getTime());
    const values = rows.flatMap((row) => [row.actual_mwh, row.forecast_mwh]).filter(Number.isFinite);
    const dataStart = Math.min(...timestamps);
    const latest = Math.max(...timestamps);
    const now = Date.now();
    const xEnd = Math.max(latest, now);
    const xStart = dataStart;
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const yPadding = Math.max((rawMax - rawMin) * 0.12, rawMax * 0.04, 1);
    const yMin = Math.max(0, rawMin - yPadding);
    const yMax = rawMax + yPadding;
    const xScale = (value) => bounds.left + (value - xStart) / Math.max(xEnd - xStart, 1) * (bounds.right - bounds.left);
    const yScale = (value) => bounds.bottom - (value - yMin) / Math.max(yMax - yMin, 1) * (bounds.bottom - bounds.top);

    const svg = svgElement("svg", {
        viewBox: `0 0 ${width} ${height}`,
        role: "img",
        "aria-label": `Actual and forecast demand for ${snapshot.region.id} over ${rangeLabel()}`,
    });
    drawAxes(svg, bounds, yMin, yMax, xStart, xEnd);
    svg.appendChild(svgElement("path", {
        d: buildPath(rows, "forecast_mwh", xScale, yScale),
        class: "demand-forecast-path",
    }));
    svg.appendChild(svgElement("path", {
        d: buildPath(rows, "actual_mwh", xScale, yScale),
        class: "demand-actual-path",
    }));

    const nowX = xScale(now);
    if (nowX >= bounds.left && nowX <= bounds.right) {
        svg.appendChild(svgElement("line", {
            x1: nowX,
            x2: nowX,
            y1: bounds.top + 12,
            y2: bounds.bottom,
            class: "now-line",
        }));
        svg.appendChild(svgElement("circle", {cx: nowX, cy: bounds.top + 12, r: 4, class: "now-dot"}));
        const anchor = nowX > bounds.right - 50 ? "end" : "middle";
        addSvgText(svg, "Now", Math.min(nowX, bounds.right - 4), bounds.top + 2, "now-label", anchor);
    }

    elements.demandChart.appendChild(svg);
    attachLineTooltip(elements.demandChart, svg, rows, bounds, xStart, xEnd, xScale, yScale, yMin, yMax);
}

function renderMixChart(snapshot) {
    const rows = filterWindow(snapshot.generation_mix);
    elements.mixChart.replaceChildren();
    elements.mixLegend.replaceChildren();
    if (!rows.length || !snapshot.fuel_catalog.length) {
        elements.mixChart.innerHTML = '<div class="chart-empty">No generation-mix observations are available.</div>';
        return;
    }

    const catalog = snapshot.fuel_catalog.filter((fuel) => rows.some((row) => Number(row.fuels[fuel.id]) > 0));
    const width = 1200;
    const height = 390;
    const bounds = {left: 72, right: 1175, top: 20, bottom: 340};
    const timestamps = rows.map((row) => new Date(row.timestamp).getTime());
    const totals = rows.map((row) => catalog.reduce((sum, fuel) => sum + Math.max(Number(row.fuels[fuel.id]) || 0, 0), 0));
    const xStart = Math.min(...timestamps);
    const xEnd = Math.max(...timestamps);
    const yMax = niceMaximum(Math.max(...totals));
    const xScale = (value) => bounds.left + (value - xStart) / Math.max(xEnd - xStart, 1) * (bounds.right - bounds.left);
    const yScale = (value) => bounds.bottom - value / yMax * (bounds.bottom - bounds.top);
    const svg = svgElement("svg", {
        viewBox: `0 0 ${width} ${height}`,
        role: "img",
        "aria-label": `Stacked generation mix for ${snapshot.region.id} over ${rangeLabel()}`,
    });
    drawAxes(svg, bounds, 0, yMax, xStart, xEnd);

    const cumulative = rows.map(() => 0);
    catalog.forEach((fuel) => {
        const bottoms = [...cumulative];
        const tops = rows.map((row, index) => {
            cumulative[index] += Math.max(Number(row.fuels[fuel.id]) || 0, 0);
            return cumulative[index];
        });
        const topPath = rows.map((row, index) => `${index ? "L" : "M"}${xScale(timestamps[index]).toFixed(2)},${yScale(tops[index]).toFixed(2)}`).join("");
        const bottomPath = rows.map((row, index) => ({index, timestamp: timestamps[index]})).reverse()
            .map(({index, timestamp}) => `L${xScale(timestamp).toFixed(2)},${yScale(bottoms[index]).toFixed(2)}`).join("");
        svg.appendChild(svgElement("path", {
            d: `${topPath}${bottomPath}Z`,
            class: "mix-area",
            fill: FUEL_COLORS[fuel.id] || "var(--fuel-other)",
        }));
    });
    elements.mixChart.appendChild(svg);

    const latest = rows.at(-1);
    const latestTotal = catalog.reduce((sum, fuel) => sum + Math.max(Number(latest.fuels[fuel.id]) || 0, 0), 0);
    elements.mixTotal.querySelector("strong").textContent = formatInteger(latestTotal);
    catalog.forEach((fuel) => {
        const value = Math.max(Number(latest.fuels[fuel.id]) || 0, 0);
        const percent = latestTotal ? value / latestTotal * 100 : 0;
        const item = document.createElement("div");
        item.className = "fuel-key";
        const swatch = document.createElement("i");
        swatch.className = "fuel-swatch";
        swatch.style.setProperty("--fuel-color", FUEL_COLORS[fuel.id] || "var(--fuel-other)");
        const label = document.createElement("span");
        label.textContent = fuel.label;
        const amount = document.createElement("strong");
        amount.textContent = `${percent.toFixed(0)}%`;
        item.append(swatch, label, amount);
        elements.mixLegend.appendChild(item);
    });
}

function updatePipelineHealth(manifest, snapshot) {
    const pipeline = manifest.pipeline;
    const status = pipeline.status;
    elements.pipelinePill.dataset.status = status;
    elements.pipelineLabel.textContent = status === "passed"
        ? "Pipeline healthy"
        : status === "warning"
            ? "Pipeline healthy · expected source lag"
            : "Pipeline needs attention";

    const latestDemand = snapshot.kpis.demand_mwh.timestamp;
    elements.freshnessValue.textContent = latestDemand ? relativeTime(latestDemand) : "Unavailable";
    elements.rowsValue.textContent = Number(pipeline.rows_processed.total).toLocaleString();
    const checks = pipeline.quality_checks;
    elements.checksValue.textContent = checks.warning
        ? `${checks.passed} passed · ${checks.warning} warning${checks.warning === 1 ? "" : "s"}`
        : `${checks.passed} / ${checks.total} passed`;

    const summary = elements.healthValue.closest("article");
    summary.dataset.status = status;
    elements.healthIcon.textContent = status === "passed" ? "✓" : status === "warning" ? "!" : "×";
    elements.healthValue.textContent = status === "passed"
        ? "All checks passed"
        : status === "warning" ? "Valid with warnings" : "Checks failed";
}

function updateKpis(snapshot) {
    const kpis = snapshot.kpis;
    const demand = kpis.demand_mwh;
    const forecast = kpis.forecast_error_pct;
    const renewable = kpis.renewable_share_pct;
    const interchange = kpis.net_interchange_mwh;

    elements.demandValue.textContent = formatInteger(demand.value);
    elements.demandNote.textContent = demand.timestamp ? `Observed ${relativeTime(demand.timestamp)}` : "No observation";
    elements.forecastValue.textContent = formatPercent(forecast.value);
    elements.forecastNote.textContent = forecast.timestamp ? `Measured ${formatTimestamp(forecast.timestamp)}` : "No matched observation";
    elements.renewableValue.textContent = formatPercent(renewable.value);
    elements.renewableNote.textContent = renewable.timestamp ? `Mix updated ${relativeTime(renewable.timestamp)}` : "No mix observation";
    elements.interchangeValue.textContent = formatInteger(interchange.value);
    elements.interchangeNote.textContent = Number.isFinite(interchange.value)
        ? interchange.value < 0 ? "Net exporting" : interchange.value > 0 ? "Net importing" : "Balanced interchange"
        : "No interchange observation";

    if (Number.isFinite(forecast.value) && Number.isFinite(renewable.value)) {
        elements.insight.textContent = `${snapshot.region.id} demand is within ${formatPercent(forecast.value)}% of forecast while renewables supply ${formatPercent(renewable.value)}% of positive reported generation.`;
    } else {
        elements.insight.textContent = `A current view of ${snapshot.region.id} electricity demand, generation, and grid health.`;
    }
}

function updateLabels(snapshot) {
    elements.demandSubtitle.textContent = `${snapshot.region.name} · ${rangeLabel()} · hourly MWh`;
    elements.demandCaption.textContent = `Actual demand and day-ahead forecast. Times are displayed in ${Intl.DateTimeFormat().resolvedOptions().timeZone}.`;
    elements.mixSubtitle.textContent = `${snapshot.region.name} · ${rangeLabel()} · positive reported generation`;
}

function updateControls() {
    elements.regionSwitcher.querySelectorAll("button").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.region === state.region));
    });
    elements.rangeSwitcher.querySelectorAll("button").forEach((button) => {
        button.setAttribute("aria-pressed", String(Number(button.dataset.hours) === state.hours));
    });
}

function updateUrl() {
    const url = new URL(window.location.href);
    url.searchParams.set("region", state.region);
    url.searchParams.set("range", String(state.hours));
    history.replaceState(null, "", url);
}

function render(snapshot) {
    updateControls();
    updateUrl();
    updateKpis(snapshot);
    updateLabels(snapshot);
    updatePipelineHealth(state.manifest, snapshot);
    renderDemandChart(snapshot);
    renderMixChart(snapshot);
}

async function loadRegion(region, force = false) {
    const manifest = state.manifest;
    if (!manifest) {
        throw new Error("The Grid Pulse manifest has not loaded.");
    }
    const entry = manifest.regions.find((candidate) => candidate.slug === region);
    if (!entry) {
        throw new Error(`The ${region.toUpperCase()} snapshot is not listed in the manifest.`);
    }
    if (!force && state.snapshots.has(region)) {
        return state.snapshots.get(region);
    }
    const snapshot = await fetchJson(`${DATA_ROOT}/${entry.data_file}`, manifest.generated_at);
    state.snapshots.set(region, snapshot);
    return snapshot;
}

function chooseInitialState() {
    const manifest = state.manifest;
    if (!manifest || !manifest.regions.length) {
        throw new Error("The Grid Pulse manifest does not list any regions.");
    }
    const parameters = new URLSearchParams(window.location.search);
    const requestedRegion = parameters.get("region");
    const requestedRange = Number(parameters.get("range"));
    const availableRegions = new Set(manifest.regions.map((region) => region.slug));
    if (requestedRegion && availableRegions.has(requestedRegion)) {
        state.region = requestedRegion;
    } else if (!availableRegions.has(state.region)) {
        state.region = manifest.regions[0].slug;
    }
    if ([24, 168, 720].includes(requestedRange)) {
        state.hours = requestedRange;
    }
}

async function loadDashboard(force = false) {
    elements.refreshButton.classList.add("is-loading");
    elements.refreshButton.disabled = true;
    showMessage(force ? "Refreshing the latest static snapshot…" : "Loading the latest Grid Pulse snapshot…");
    try {
        if (force || !state.manifest) {
            state.manifest = await fetchJson(`${DATA_ROOT}/manifest.json`, String(Date.now()));
            state.snapshots.clear();
            chooseInitialState();
        }
        const snapshot = await loadRegion(state.region, force);
        render(snapshot);
        hideMessage();
    } catch (error) {
        console.error(error);
        showMessage("Grid Pulse data could not be loaded. Run the data build and serve the site over HTTP before trying again.", true);
        elements.pipelinePill.dataset.status = "failed";
        elements.pipelineLabel.textContent = "Data unavailable";
    } finally {
        elements.refreshButton.classList.remove("is-loading");
        elements.refreshButton.disabled = false;
    }
}

elements.regionSwitcher.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-region]");
    if (!button || button.dataset.region === state.region) {
        return;
    }
    state.region = button.dataset.region;
    updateControls();
    showMessage(`Loading ${button.textContent}…`);
    try {
        render(await loadRegion(state.region));
        hideMessage();
    } catch (error) {
        console.error(error);
        showMessage(`The ${button.textContent} snapshot could not be loaded.`, true);
    }
});

elements.rangeSwitcher.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-hours]");
    if (!button) {
        return;
    }
    state.hours = Number(button.dataset.hours);
    const snapshot = state.snapshots.get(state.region);
    if (snapshot) {
        render(snapshot);
    }
});

elements.refreshButton.addEventListener("click", () => loadDashboard(true));
window.addEventListener("resize", () => {
    const snapshot = state.snapshots.get(state.region);
    if (snapshot) {
        renderDemandChart(snapshot);
        renderMixChart(snapshot);
    }
});

configureTheme();
void loadDashboard();

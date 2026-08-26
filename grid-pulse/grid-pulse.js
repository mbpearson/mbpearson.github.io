"use strict";

const DATA_ROOT = "data";
const chartLibrary = globalThis["Highcharts"];
const FUEL_COLOR_VARIABLES = {
    battery: "--fuel-battery",
    coal: "--fuel-coal",
    geothermal: "--fuel-geothermal",
    hydro: "--fuel-hydro",
    natural_gas: "--fuel-natural-gas",
    nuclear: "--fuel-nuclear",
    petroleum: "--fuel-petroleum",
    solar: "--fuel-solar",
    wind: "--fuel-wind",
    other: "--fuel-other",
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

/** @type {{manifest: GridManifest|null, region: string, hours: number, snapshots: Map<string, GridSnapshot>, charts: {demand: Object|null, mix: Object|null}, visibility: {demand: Map<string, boolean>, mix: Map<string, boolean>}}} */
const state = {
    manifest: null,
    region: "miso",
    hours: 168,
    snapshots: new Map(),
    charts: {demand: null, mix: null},
    visibility: {demand: new Map(), mix: new Map()},
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
    const snapshot = state.snapshots.get(state.region);
    if (snapshot) {
        renderDemandChart(snapshot);
        renderMixChart(snapshot);
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

function cssValue(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartTheme() {
    return {
        accent: cssValue("--accent"),
        background: cssValue("--surface-solid"),
        border: cssValue("--border-strong"),
        blue: cssValue("--blue"),
        cyan: cssValue("--cyan"),
        grid: cssValue("--chart-grid"),
        text: cssValue("--text"),
        textSoft: cssValue("--text-soft"),
        textFaint: cssValue("--text-faint"),
    };
}

function fuelColor(fuelId) {
    return cssValue(FUEL_COLOR_VARIABLES[fuelId] || FUEL_COLOR_VARIABLES.other);
}

function destroyChart(chartName, container) {
    if (state.charts[chartName]) {
        state.charts[chartName].destroy();
        state.charts[chartName] = null;
    }
    container.replaceChildren();
}

function seriesVisibility(chartName, seriesId) {
    const visibility = state.visibility[chartName];
    return {
        visible: visibility.has(seriesId) ? visibility.get(seriesId) : true,
        events: {
            hide() { visibility.set(seriesId, false); },
            show() { visibility.set(seriesId, true); },
        },
    };
}

function baseChartOptions(description) {
    const theme = chartTheme();
    return {
        chart: {
            animation: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
            backgroundColor: "transparent",
            reflow: true,
            spacing: [8, 8, 8, 8],
            style: {fontFamily: getComputedStyle(document.body).fontFamily},
            zooming: {
                mouseWheel: {enabled: false},
                type: "x",
                resetButton: {
                    position: {align: "right", x: -8, y: 8},
                    theme: {
                        fill: theme.background,
                        stroke: theme.border,
                        r: 6,
                        style: {color: theme.text, fontWeight: "600"},
                        states: {hover: {fill: cssValue("--surface-muted")}},
                    },
                },
            },
        },
        accessibility: {description},
        credits: {enabled: false},
        title: {text: null},
        legend: {
            align: "center",
            itemDistance: 20,
            itemHiddenStyle: {color: theme.textFaint},
            itemHoverStyle: {color: theme.accent},
            itemStyle: {color: theme.textSoft, cursor: "pointer", fontSize: "12px", fontWeight: "600"},
            symbolRadius: 3,
            verticalAlign: "bottom",
        },
        xAxis: {
            crosshair: {color: theme.border, dashStyle: "ShortDash", width: 1},
            gridLineWidth: 0,
            labels: {style: {color: theme.textFaint, fontSize: "11px"}},
            lineColor: theme.border,
            tickColor: theme.border,
            type: "datetime",
        },
        yAxis: {
            endOnTick: false,
            gridLineColor: theme.grid,
            gridLineDashStyle: "ShortDash",
            labels: {
                formatter() { return formatCompact(this.value); },
                style: {color: theme.textFaint, fontSize: "11px"},
            },
            min: 0,
            startOnTick: false,
            title: {text: "MWh", style: {color: theme.textFaint, fontSize: "11px"}},
        },
        tooltip: {
            animation: false,
            backgroundColor: theme.background,
            borderColor: theme.border,
            borderRadius: 10,
            outside: true,
            padding: 10,
            shadow: true,
            shared: true,
            style: {color: theme.text, fontSize: "12px"},
            useHTML: true,
        },
        plotOptions: {
            series: {
                animation: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
                connectNulls: false,
                marker: {enabled: false},
                states: {inactive: {opacity: 0.35}},
            },
        },
    };
}

function renderDemandChart(snapshot) {
    const rows = filterWindow(snapshot.demand);
    destroyChart("demand", elements.demandChart);
    if (!rows.length) {
        elements.demandChart.innerHTML = '<div class="chart-empty">No demand observations are available.</div>';
        return;
    }

    const timestamps = rows.map((row) => new Date(row.timestamp).getTime());
    const dataStart = Math.min(...timestamps);
    const latest = Math.max(...timestamps);
    const now = Date.now();
    const xEnd = Math.max(latest, now);
    const theme = chartTheme();
    const options = baseChartOptions(`Actual and forecast demand for ${snapshot.region.id} over ${rangeLabel()}. Select legend items to toggle series and drag horizontally to zoom.`);
    options.xAxis.min = dataStart;
    options.xAxis.max = xEnd;
    options.xAxis.plotLines = now >= dataStart && now <= xEnd ? [{
        color: theme.accent,
        label: {align: "right", rotation: 0, style: {color: theme.accent, fontSize: "11px", fontWeight: "700"}, text: "Now", y: 12},
        value: now,
        width: 2,
        zIndex: 5,
    }] : [];
    options.tooltip.formatter = function () {
        const points = this.points || [];
        const values = points.map((point) => `<div class="chart-tooltip-row"><span><i style="background:${point.color}"></i>${point.series.name}</span><strong>${formatInteger(point.y)} MWh</strong></div>`).join("");
        return `<div class="chart-tooltip-content"><strong class="chart-tooltip-heading">${formatTimestamp(this.x)}</strong>${values}</div>`;
    };
    options.series = [
        {
            ...seriesVisibility("demand", "actual"),
            color: theme.blue,
            data: rows.map((row) => [new Date(row.timestamp).getTime(), row.actual_mwh]),
            id: "actual",
            lineWidth: 2.5,
            name: "Actual",
            type: "line",
        },
        {
            ...seriesVisibility("demand", "forecast"),
            color: theme.cyan,
            dashStyle: "Dash",
            data: rows.map((row) => [new Date(row.timestamp).getTime(), row.forecast_mwh]),
            id: "forecast",
            lineWidth: 2,
            name: "Forecast",
            type: "line",
        },
    ];
    state.charts.demand = chartLibrary.chart(elements.demandChart, options);
}

function renderMixChart(snapshot) {
    const rows = filterWindow(snapshot.generation_mix);
    destroyChart("mix", elements.mixChart);
    if (!rows.length || !snapshot.fuel_catalog.length) {
        elements.mixChart.innerHTML = '<div class="chart-empty">No generation-mix observations are available.</div>';
        return;
    }

    const catalog = snapshot.fuel_catalog.filter((fuel) => rows.some((row) => Number(row.fuels[fuel.id]) > 0));
    const latest = rows.at(-1);
    const latestTotal = catalog.reduce((sum, fuel) => sum + Math.max(Number(latest.fuels[fuel.id]) || 0, 0), 0);
    elements.mixTotal.querySelector("strong").textContent = formatInteger(latestTotal);
    const options = baseChartOptions(`Stacked generation mix for ${snapshot.region.id} over ${rangeLabel()}. Select legend items to toggle fuels and drag horizontally to zoom.`);
    options.chart.type = "area";
    options.yAxis.gridZIndex = 4;
    options.plotOptions.area = {
        fillOpacity: 0.92,
        lineWidth: 0.75,
        stacking: "normal",
    };
    options.tooltip.formatter = function () {
        const points = this.points || [];
        const total = points.reduce((sum, point) => sum + (Number(point.y) || 0), 0);
        const values = points.slice().reverse().map((point) => {
            const percent = total ? point.y / total * 100 : 0;
            return `<div class="chart-tooltip-row"><span><i style="background:${point.color}"></i>${point.series.name}</span><strong>${percent.toFixed(1)}% <small>${formatInteger(point.y)} MWh</small></strong></div>`;
        }).join("");
        return `<div class="chart-tooltip-content mix-tooltip"><strong class="chart-tooltip-heading">${formatTimestamp(this.x)}</strong><div class="chart-tooltip-total">Visible generation: ${formatInteger(total)} MWh</div>${values}</div>`;
    };
    options.series = catalog.map((fuel) => ({
        ...seriesVisibility("mix", fuel.id),
        color: fuelColor(fuel.id),
        data: rows.map((row) => [new Date(row.timestamp).getTime(), Math.max(Number(row.fuels[fuel.id]) || 0, 0)]),
        id: fuel.id,
        name: fuel.label,
        type: "area",
    }));
    state.charts.mix = chartLibrary.chart(elements.mixChart, options);
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

configureTheme();
void loadDashboard();

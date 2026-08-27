"use strict";

const DATA_ROOT = "data";
const US_MAP_URL = "https://cdn.jsdelivr.net/npm/@highcharts/map-collection@2.3.3/countries/us/us-all.topo.json";
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
const REGION_DESCRIPTIONS = {
    miso: "MISO (Midcontinent Independent System Operator) operates the central U.S. grid across parts of 15 states and Manitoba, Canada.",
    pjm: "PJM (Pennsylvania–New Jersey–Maryland Interconnection) coordinates the grid across all or parts of 13 states and Washington, D.C., from Illinois to the Mid-Atlantic.",
    caiso: "CAISO (California Independent System Operator) manages the grid serving about 80% of California and a small portion of Nevada.",
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

/** @type {{manifest: GridManifest|null, region: string, hours: number, snapshots: Map<string, GridSnapshot>, resources: Object|null, mapTopology: Object|null, resourceMetric: string, mapMode: string, selectedState: string|null, charts: Object<string, Object|null>, visibility: {demand: Map<string, boolean>, mix: Map<string, boolean>}}} */
const state = {
    manifest: null,
    region: "miso",
    hours: 168,
    snapshots: new Map(),
    resources: null,
    mapTopology: null,
    resourceMetric: "solar",
    mapMode: "opportunity",
    selectedState: null,
    charts: {demand: null, mix: null, resource: null, opportunity: null, stateTrend: null},
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
    regionDescription: document.querySelector("#region-description"),
    demandValue: document.querySelector("#demand-value"),
    demandNote: document.querySelector("#demand-note"),
    forecastValue: document.querySelector("#forecast-value"),
    forecastNote: document.querySelector("#forecast-note"),
    renewableValue: document.querySelector("#renewable-value"),
    renewableNote: document.querySelector("#renewable-note"),
    interchangeValue: document.querySelector("#interchange-value"),
    interchangeNote: document.querySelector("#interchange-note"),
    demandSubtitle: document.querySelector("#demand-subtitle"),
    demandCaption: document.querySelector("#demand-caption-text"),
    demandChart: document.querySelector("#demand-chart"),
    mixSubtitle: document.querySelector("#mix-subtitle"),
    mixTotal: document.querySelector("#mix-total"),
    mixChart: document.querySelector("#mix-chart"),
    resourceCard: document.querySelector(".resource-map-card"),
    resourceSwitcher: document.querySelector("#resource-switcher"),
    mapModeSwitcher: document.querySelector("#map-mode-switcher"),
    resourceSubtitle: document.querySelector("#resource-map-subtitle"),
    resourceMap: document.querySelector("#resource-map"),
    resourceStateName: document.querySelector("#resource-state-name"),
    resourceStateValue: document.querySelector("#resource-state-value"),
    resourceStateUnit: document.querySelector("#resource-state-unit"),
    resourceStateRank: document.querySelector("#resource-state-rank"),
    resourceMethod: document.querySelector("#resource-method"),
    opportunityChart: document.querySelector("#opportunity-chart"),
    stateSelect: document.querySelector("#state-select"),
    stateRenewableShare: document.querySelector("#state-renewable-share"),
    stateRenewableGrowth: document.querySelector("#state-renewable-growth"),
    stateCarbon: document.querySelector("#state-carbon"),
    stateTrendChart: document.querySelector("#state-trend-chart"),
    stateMix: document.querySelector("#state-mix"),
    opportunityTableBody: document.querySelector("#opportunity-table-body"),
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
    if (state.resources && state.mapTopology) {
        renderStateExplorer();
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
        timeZone: "UTC",
        timeZoneName: "short",
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
        // time: {
        //     useUTC: false,
        //     timezone: 'America/Chicago',
        // },
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
    // options.xAxis.plotLines = now >= dataStart && now <= xEnd ? [{
    //     color: theme.accent,
    //     // label: {align: "right", rotation: 0, style: {color: theme.accent, fontSize: "11px", fontWeight: "700"}, text: "Now", y: 12},
    //     value: now,
    //     width: 1,
    //     zIndex: 5,
    // }] : [];
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

function updateResourceDetail(postalCode = state.selectedState) {
    const artifact = state.resources;
    if (!artifact) {
        return;
    }
    const definition = artifact.metrics[state.resourceMetric];
    const stateRow = artifact.states.find((candidate) => candidate.postal_code === postalCode);
    if (!stateRow) {
        elements.resourceStateName.textContent = "Select a state";
        elements.resourceStateValue.textContent = "—";
        elements.resourceStateUnit.textContent = definition.unit;
        elements.resourceStateRank.textContent = "Click any state to compare its resource.";
        return;
    }
    const metric = stateRow.metrics[state.resourceMetric];
    state.selectedState = stateRow.postal_code;
    elements.resourceStateName.textContent = `${stateRow.name} (${stateRow.postal_code})`;
    elements.resourceStateValue.textContent = metric.value.toFixed(2);
    elements.resourceStateUnit.textContent = definition.unit;
    elements.resourceStateRank.textContent = `Ranks #${metric.rank} of 50 · ${metric.percentile}th percentile nationally`;
}

function renderResourceMap() {
    const artifact = state.resources;
    if (!artifact || !state.mapTopology || typeof chartLibrary.mapChart !== "function") {
        return;
    }
    destroyChart("resource", elements.resourceMap);
    const metricName = state.resourceMetric;
    const definition = artifact.metrics[metricName];
    const theme = chartTheme();
    const isSolar = metricName === "solar";
    const values = artifact.states.map((stateRow) => stateRow.metrics[metricName].value);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    elements.resourceCard.dataset.resource = metricName;
    elements.resourceSubtitle.textContent = `Long-term average ${definition.label.toLowerCase()} by state`;
    elements.resourceMethod.textContent = `${artifact.source.nasa}; ${artifact.source.method.toLowerCase()}.`;
    elements.resourceSwitcher.querySelectorAll("button[data-resource]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.resource === metricName));
    });

    state.charts.resource = chartLibrary.mapChart(elements.resourceMap, {
        chart: {
            animation: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
            backgroundColor: "transparent",
            map: state.mapTopology,
            spacing: [8, 8, 8, 8],
            style: {fontFamily: getComputedStyle(document.body).fontFamily},
        },
        accessibility: {
            description: `Interactive U.S. map of ${definition.label.toLowerCase()}. Select a state to display its value and rank.`,
        },
        colorAxis: {
            min: minimum,
            max: maximum,
            minColor: isSolar ? "#f2e9d0" : "#dcebea",
            maxColor: isSolar ? "#812f78" : "#31568e",
            stops: isSolar
                ? [[0, "#f2e9d0"], [0.42, "#f5bd4f"], [0.72, "#e45b32"], [1, "#812f78"]]
                : [[0, "#dcebea"], [0.4, "#70c5c2"], [0.72, "#238da8"], [1, "#31568e"]],
            labels: {format: `{value:.1f}`, style: {color: theme.textFaint, fontSize: "10px"}},
        },
        credits: {enabled: false},
        legend: {
            align: "center",
            itemStyle: {color: theme.textSoft, fontSize: "11px"},
            layout: "horizontal",
            symbolHeight: 10,
            symbolWidth: 260,
            verticalAlign: "bottom",
        },
        mapNavigation: {
            enabled: true,
            enableMouseWheelZoom: true,
            buttonOptions: {
                align: "left",
                theme: {fill: theme.background, stroke: theme.border, "stroke-width": 1, r: 7, style: {color: theme.text}},
                verticalAlign: "bottom",
            },
        },
        title: {text: null},
        tooltip: {
            backgroundColor: theme.background,
            borderColor: theme.border,
            borderRadius: 10,
            outside: true,
            padding: 10,
            shadow: true,
            useHTML: true,
            formatter() {
                const custom = this.point.options.custom;
                return `<div class="chart-tooltip-content"><strong class="chart-tooltip-heading">${custom.name}</strong><div class="chart-tooltip-row"><span>${definition.label}</span><strong>${this.point.value.toFixed(2)} ${definition.unit}</strong></div><div class="chart-tooltip-row"><span>National rank</span><strong>#${custom.rank} of 50</strong></div></div>`;
            },
        },
        plotOptions: {
            map: {
                borderColor: theme.background,
                borderWidth: 1,
                cursor: "pointer",
                nullColor: cssValue("--surface-muted"),
                states: {
                    hover: {borderColor: theme.text, borderWidth: 1.5, brightness: 0.08},
                    select: {borderColor: theme.text, borderWidth: 2, color: undefined},
                },
            },
        },
        series: [{
            allAreas: false,
            data: artifact.states.map((stateRow) => {
                const metric = stateRow.metrics[metricName];
                return {
                    "hc-key": stateRow.hc_key,
                    value: metric.value,
                    custom: {name: stateRow.name, postalCode: stateRow.postal_code, rank: metric.rank, percentile: metric.percentile},
                    selected: stateRow.postal_code === state.selectedState,
                };
            }),
            dataLabels: {enabled: false},
            joinBy: "hc-key",
            name: definition.label,
            point: {
                events: {
                    click() { updateResourceDetail(this.options.custom.postalCode); },
                },
            },
            allowPointSelect: true,
        }],
    });
    updateResourceDetail();
}

async function loadResourceMap(force = false) {
    if (force || !state.resources) {
        state.resources = await fetchJson(`${DATA_ROOT}/us-renewable-resources.json`, force ? String(Date.now()) : "");
    }
    if (!state.mapTopology) {
        state.mapTopology = await fetchJson(US_MAP_URL);
    }
    renderResourceMap();
}

function showResourceMapError() {
    destroyChart("resource", elements.resourceMap);
    elements.resourceMap.innerHTML = '<div class="chart-empty">Renewable resource map data is unavailable.</div>';
}

function stateMapMetric(row) {
    const technology = state.resourceMetric;
    const resource = row.resource[technology];
    const opportunity = row.opportunity[technology];
    if (state.mapMode === "resource") {
        const definition = state.resources.metric_definitions[technology];
        return {value: resource.value, unit: definition.unit, decimals: 2, rank: resource.rank,
            detail: `Resource rank #${resource.rank} of 50 · ${resource.percentile}th percentile`};
    }
    if (state.mapMode === "deployment") {
        const share = Number(row.current[`${technology}_share_pct`]) || 0;
        return {value: share, unit: "% generation", decimals: 1, rank: opportunity.deployment_rank,
            detail: `${technology[0].toUpperCase() + technology.slice(1)} supplies ${share.toFixed(1)}% of generation · P${opportunity.deployment_percentile} deployment`};
    }
    return {value: opportunity.score, unit: "points", decimals: 0, rank: opportunity.rank,
        detail: `Opportunity rank #${opportunity.rank} of 50 · resource P${opportunity.resource_percentile} vs. deployment P${opportunity.deployment_percentile}`};
}

function updateStateDetail() {
    const row = state.resources?.states.find((candidate) => candidate.postal_code === state.selectedState);
    if (!row) return;
    const metric = stateMapMetric(row);
    elements.resourceStateName.textContent = `${row.name} (${row.postal_code})`;
    elements.resourceStateValue.textContent = Number(metric.value).toFixed(metric.decimals);
    elements.resourceStateUnit.textContent = metric.unit;
    elements.resourceStateRank.textContent = metric.detail;
    elements.stateSelect.value = row.postal_code;
}

function renderStateResourceMap() {
    const artifact = state.resources;
    if (!artifact || !state.mapTopology || typeof chartLibrary.mapChart !== "function") return;
    destroyChart("resource", elements.resourceMap);
    const technology = state.resourceMetric;
    const theme = chartTheme();
    const values = artifact.states.map((row) => stateMapMetric(row).value);
    const opportunityMode = state.mapMode === "opportunity";
    const isSolar = technology === "solar";
    elements.resourceCard.dataset.resource = technology;
    elements.resourceSubtitle.textContent = `${technology[0].toUpperCase() + technology.slice(1)} ${state.mapMode} by state · ${artifact.comparison_year}`;
    elements.resourceMethod.textContent = `${artifact.source.nasa}; ${String(artifact.source.resource_method).toLowerCase()}.`;
    elements.resourceSwitcher.querySelectorAll("button[data-resource]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.resource === technology)));
    elements.mapModeSwitcher.querySelectorAll("button[data-map-mode]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.mapMode === state.mapMode)));
    state.charts.resource = chartLibrary.mapChart(elements.resourceMap, {
        chart: {animation: false, backgroundColor: "transparent", map: state.mapTopology, spacing: [8, 8, 8, 8], style: {fontFamily: getComputedStyle(document.body).fontFamily}},
        accessibility: {description: `Interactive U.S. map of state ${technology} ${state.mapMode}.`},
        colorAxis: {
            min: Math.min(...values), max: Math.max(...values),
            stops: opportunityMode
                ? [[0, "#6b395f"], [0.5, "#e9eceb"], [1, "#36b6a8"]]
                : isSolar ? [[0, "#f2e9d0"], [0.42, "#f5bd4f"], [0.72, "#e45b32"], [1, "#812f78"]]
                    : [[0, "#dcebea"], [0.4, "#70c5c2"], [0.72, "#238da8"], [1, "#31568e"]],
            labels: {format: state.mapMode === "deployment" ? "{value:.0f}%" : "{value:.1f}", style: {color: theme.textFaint, fontSize: "10px"}},
        },
        credits: {enabled: false},
        legend: {align: "center", layout: "horizontal", symbolHeight: 10, symbolWidth: 260, verticalAlign: "bottom", itemStyle: {color: theme.textSoft, fontSize: "11px"}},
        mapNavigation: {enabled: true, enableMouseWheelZoom: true, buttonOptions: {align: "left", verticalAlign: "bottom", theme: {fill: theme.background, stroke: theme.border, "stroke-width": 1, r: 7, style: {color: theme.text}}}},
        title: {text: null},
        tooltip: {useHTML: true, outside: true, formatter() { const custom = this.point.options.custom; const metric = custom.metric; return `<div class="chart-tooltip-content"><strong class="chart-tooltip-heading">${custom.name}</strong><div class="chart-tooltip-row"><span>${state.mapMode}</span><strong>${Number(metric.value).toFixed(metric.decimals)} ${metric.unit}</strong></div><div class="chart-tooltip-row"><span>National rank</span><strong>#${metric.rank} of 50</strong></div></div>`; }},
        plotOptions: {map: {borderColor: theme.background, borderWidth: 1, cursor: "pointer", nullColor: cssValue("--surface-muted"), states: {hover: {borderColor: theme.text, borderWidth: 1.5, brightness: 0.08}, select: {borderColor: theme.text, borderWidth: 2, color: undefined}}}},
        series: [{
            allAreas: false, joinBy: "hc-key", name: `${technology} ${state.mapMode}`, allowPointSelect: true,
            data: artifact.states.map((row) => ({"hc-key": row.hc_key, value: stateMapMetric(row).value, custom: {name: row.name, postalCode: row.postal_code, metric: stateMapMetric(row)}, selected: row.postal_code === state.selectedState})),
            point: {events: {click() { selectExplorerState(this.options.custom.postalCode); }}},
        }],
    });
    updateStateDetail();
}

function renderOpportunityChart() {
    if (!state.resources) return;
    destroyChart("opportunity", elements.opportunityChart);
    const theme = chartTheme();
    const technology = state.resourceMetric;
    state.charts.opportunity = chartLibrary.chart(elements.opportunityChart, {
        chart: {type: "scatter", backgroundColor: "transparent", animation: false, spacing: [8, 8, 8, 8], style: {fontFamily: getComputedStyle(document.body).fontFamily}},
        accessibility: {description: `${technology} resource percentile versus deployment percentile for all states.`},
        credits: {enabled: false}, legend: {enabled: false}, title: {text: null},
        xAxis: {min: 0, max: 100, title: {text: "Resource percentile", style: {color: theme.textSoft}}, labels: {style: {color: theme.textFaint}}, gridLineWidth: 1, gridLineColor: theme.grid, plotLines: [{value: 50, color: theme.border, width: 1, dashStyle: "Dash"}]},
        yAxis: {min: 0, max: 100, title: {text: "Deployment percentile", style: {color: theme.textSoft}}, labels: {style: {color: theme.textFaint}}, gridLineColor: theme.grid, plotLines: [{value: 50, color: theme.border, width: 1, dashStyle: "Dash"}], plotBands: [{from: 0, to: 50, color: "rgba(54,182,168,0.08)"}]},
        tooltip: {useHTML: true, outside: true, formatter() { const custom = this.point.options.custom; return `<div class="chart-tooltip-content"><strong class="chart-tooltip-heading">${custom.name}</strong><div class="chart-tooltip-row"><span>Resource percentile</span><strong>${this.x}</strong></div><div class="chart-tooltip-row"><span>Deployment percentile</span><strong>${this.y}</strong></div><div class="chart-tooltip-row"><span>Opportunity score</span><strong>${custom.score > 0 ? "+" : ""}${custom.score}</strong></div></div>`; }},
        plotOptions: {scatter: {cursor: "pointer", marker: {radius: 5, lineWidth: 1, lineColor: theme.background}, point: {events: {click() { selectExplorerState(this.options.custom.postalCode); }}}}},
        series: [{color: technology === "solar" ? "#e45b32" : "#238da8", data: state.resources.states.map((row) => ({
            x: row.opportunity[technology].resource_percentile, y: row.opportunity[technology].deployment_percentile,
            marker: {radius: row.postal_code === state.selectedState ? 9 : 5, lineWidth: row.postal_code === state.selectedState ? 3 : 1},
            custom: {name: `${row.name} (${row.postal_code})`, postalCode: row.postal_code, score: row.opportunity[technology].score},
        }))}],
    });
}

function renderStateProfile() {
    const row = state.resources?.states.find((candidate) => candidate.postal_code === state.selectedState);
    if (!row) return;
    elements.stateSelect.value = row.postal_code;
    elements.stateRenewableShare.textContent = `${Number(row.current.renewable_share_pct).toFixed(1)}%`;
    const growth = row.growth.renewable_cagr_pct;
    elements.stateRenewableGrowth.textContent = growth == null ? "n/a" : `${growth > 0 ? "+" : ""}${Number(growth).toFixed(1)}% CAGR`;
    elements.stateCarbon.textContent = row.current.carbon_intensity_lbs_mwh == null ? "n/a" : `${formatInteger(row.current.carbon_intensity_lbs_mwh)} lbs/MWh`;
    destroyChart("stateTrend", elements.stateTrendChart);
    const theme = chartTheme();
    state.charts.stateTrend = chartLibrary.chart(elements.stateTrendChart, {
        chart: {type: "line", backgroundColor: "transparent", height: 220, spacing: [8, 8, 4, 8], style: {fontFamily: getComputedStyle(document.body).fontFamily}},
        credits: {enabled: false}, title: {text: null},
        xAxis: {categories: row.history.map((item) => String(item.year)), labels: {style: {color: theme.textFaint}}, lineColor: theme.border},
        yAxis: {min: 0, title: {text: "Share of generation", style: {color: theme.textSoft}}, labels: {format: "{value}%", style: {color: theme.textFaint}}, gridLineColor: theme.grid},
        legend: {itemStyle: {color: theme.textSoft, fontSize: "11px"}}, tooltip: {shared: true, valueSuffix: "%"},
        series: [
            {name: "Renewables", color: "#36b6a8", data: row.history.map((item) => item.renewable_share_pct)},
            {name: "Solar", color: "#e45b32", data: row.history.map((item) => item.solar_share_pct)},
            {name: "Wind", color: "#238da8", data: row.history.map((item) => item.wind_share_pct)},
        ],
    });
    const mixRows = row.current.generation_mix.filter((fuel) => fuel.share_pct >= 1).sort((a, b) => b.share_pct - a.share_pct);
    elements.stateMix.innerHTML = `<h3>${row.current.year} generation mix</h3><div class="state-mix-bar">${mixRows.map((fuel) => `<span style="width:${fuel.share_pct}%;background:${fuelColor(fuel.id)}" title="${fuel.label}: ${fuel.share_pct}%"></span>`).join("")}</div><div class="state-mix-legend">${mixRows.map((fuel) => `<span><i style="background:${fuelColor(fuel.id)}"></i>${fuel.label} <strong>${fuel.share_pct.toFixed(1)}%</strong></span>`).join("")}</div>`;
}

function renderOpportunityTable() {
    if (!state.resources) return;
    const technology = state.resourceMetric;
    const rows = [...state.resources.states].sort((a, b) => a.opportunity[technology].rank - b.opportunity[technology].rank).slice(0, 10);
    elements.opportunityTableBody.innerHTML = rows.map((row) => {
        const opportunity = row.opportunity[technology];
        const growth = row.growth.renewable_cagr_pct;
        return `<tr data-state="${row.postal_code}" tabindex="0" class="${row.postal_code === state.selectedState ? "is-selected" : ""}"><td>#${opportunity.rank}</td><th>${row.name} <span>${row.postal_code}</span></th><td>P${opportunity.resource_percentile}</td><td>${Number(row.current[`${technology}_share_pct`]).toFixed(1)}% <small>P${opportunity.deployment_percentile}</small></td><td><strong>${opportunity.score > 0 ? "+" : ""}${opportunity.score}</strong></td><td>${growth == null ? "n/a" : `${growth > 0 ? "+" : ""}${growth.toFixed(1)}%`}</td><td>${row.current.carbon_intensity_lbs_mwh == null ? "n/a" : `${formatInteger(row.current.carbon_intensity_lbs_mwh)} lbs/MWh`}</td></tr>`;
    }).join("");
}

function selectExplorerState(postalCode) {
    state.selectedState = postalCode;
    renderStateExplorer();
}

function renderStateExplorer() {
    renderStateResourceMap();
    renderOpportunityChart();
    renderStateProfile();
    renderOpportunityTable();
}

async function loadStateExplorer(force = false) {
    if (force || !state.resources) {
        state.resources = await fetchJson(`${DATA_ROOT}/state-energy.json`, force ? String(Date.now()) : "");
        if (!state.selectedState) {
            state.selectedState = [...state.resources.states].sort((a, b) => a.opportunity.solar.rank - b.opportunity.solar.rank)[0]?.postal_code || "CA";
        }
        elements.stateSelect.innerHTML = state.resources.states.map((row) => `<option value="${row.postal_code}">${row.name} (${row.postal_code})</option>`).join("");
    }
    if (!state.mapTopology) state.mapTopology = await fetchJson(US_MAP_URL);
    renderStateExplorer();
}

function showStateExplorerError() {
    destroyChart("resource", elements.resourceMap);
    destroyChart("opportunity", elements.opportunityChart);
    destroyChart("stateTrend", elements.stateTrendChart);
    elements.resourceMap.innerHTML = '<div class="chart-empty">State explorer data is unavailable.</div>';
    elements.opportunityChart.innerHTML = '<div class="chart-empty">Opportunity data is unavailable.</div>';
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
    elements.regionDescription.textContent = REGION_DESCRIPTIONS[snapshot.region.slug]
        || `${snapshot.region.id} is the selected grid region.`;
    elements.demandSubtitle.textContent = `${snapshot.region.name} · ${rangeLabel()} · hourly MWh`;
    elements.demandCaption.textContent = "Actual demand and day-ahead forecast. Times are displayed in UTC.";
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
        const resourcePromise = loadStateExplorer(force).catch((error) => {
            console.error("State explorer could not be loaded.", error);
            showStateExplorerError();
        });
        const snapshot = await loadRegion(state.region, force);
        render(snapshot);
        await resourcePromise;
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

elements.resourceSwitcher.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-resource]");
    if (!button || button.dataset.resource === state.resourceMetric) {
        return;
    }
    state.resourceMetric = button.dataset.resource;
    renderStateExplorer();
});

elements.mapModeSwitcher.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-map-mode]");
    if (!button || button.dataset.mapMode === state.mapMode) return;
    state.mapMode = button.dataset.mapMode;
    renderStateResourceMap();
});

elements.stateSelect.addEventListener("change", (event) => selectExplorerState(event.target.value));

elements.opportunityTableBody.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-state]");
    if (row) selectExplorerState(row.dataset.state);
});

elements.opportunityTableBody.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("tr[data-state]");
    if (row) {
        event.preventDefault();
        selectExplorerState(row.dataset.state);
    }
});

elements.refreshButton.addEventListener("click", () => loadDashboard(true));

configureTheme();
void loadDashboard();

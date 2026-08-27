"""Join annual EIA state metrics with NASA POWER renewable resources."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pipeline.eia_state_client import STATE_CODES
from pipeline.validate import require_valid, validate_state_energy


FUEL_GROUPS = (
    ("coal", "Coal", "COW", False),
    ("natural_gas", "Natural gas", "NGO", False),
    ("nuclear", "Nuclear", "NUC", False),
    ("petroleum", "Petroleum", "PET", False),
    ("hydro", "Hydropower", "HYC", True),
    ("wind", "Wind", "WND", True),
    ("solar", "Solar", "TSN", True),
    ("geothermal", "Geothermal", "GEO", True),
    ("bioenergy", "Bioenergy", "BIO", True),
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _percentile(values: Mapping[str, float], *, reverse: bool = True) -> dict[str, dict[str, int]]:
    ordered = sorted(values, key=lambda code: ((-1 if reverse else 1) * values[code], code))
    count = len(ordered)
    return {
        code: {"rank": rank, "percentile": round((count - rank) / max(count - 1, 1) * 100)}
        for rank, code in enumerate(ordered, start=1)
    }


def _cagr(start: float, end: float, years: int) -> float | None:
    if start <= 0 or end < 0 or years <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 2)


def build_state_energy_artifact(
    generation_document: Mapping[str, Any],
    emissions_document: Mapping[str, Any],
    resource_document: Mapping[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a browser-ready state comparison artifact."""
    values: dict[tuple[str, int], dict[str, float]] = defaultdict(dict)
    names: dict[str, str] = {}
    for row in generation_document.get("records", []):
        state = str(row.get("stateid") or row.get("location") or "").upper()
        fuel = str(row.get("fueltypeid", row.get("fuelid", ""))).upper()
        try:
            year = int(row.get("period"))
        except (TypeError, ValueError):
            continue
        generation = _number(row.get("generation"))
        if state in STATE_CODES and fuel and generation is not None:
            values[(state, year)][fuel] = generation * 1_000  # EIA field is thousand MWh.
            names[state] = str(row.get("stateDescription") or row.get("stateName") or state)

    carbon: dict[str, float] = {}
    for row in emissions_document.get("records", []):
        state = str(row.get("stateid", "")).upper()
        rate = _number(row.get("co2-rate-lbs-mwh"))
        if state in STATE_CODES and rate is not None:
            carbon[state] = rate

    resources = {row["postal_code"]: row for row in resource_document.get("states", [])}
    resource_definitions = resource_document.get("metrics", {})
    years = sorted({year for state, year in values if state in STATE_CODES})
    if not years:
        raise ValueError("No usable annual state generation rows were found.")
    start_year, end_year = min(years), max(years)

    preliminary: list[dict[str, Any]] = []
    for code in STATE_CODES:
        resource = resources.get(code)
        if resource is None:
            raise ValueError(f"NASA resource data is missing {code}.")
        history = []
        year_metrics: dict[int, dict[str, float]] = {}
        for year in years:
            fuels = values.get((code, year), {})
            total = max(fuels.get("ALL", 0), 0) + max(fuels.get("DPV", 0), 0)
            renewable = max(fuels.get("REN", 0), 0) + max(fuels.get("DPV", 0), 0)
            solar, wind = max(fuels.get("TSN", 0), 0), max(fuels.get("WND", 0), 0)
            year_metrics[year] = {"total": total, "renewable": renewable, "solar": solar, "wind": wind}
            history.append({
                "year": year,
                "renewable_share_pct": round(renewable / total * 100, 2) if total else None,
                "solar_share_pct": round(solar / total * 100, 2) if total else None,
                "wind_share_pct": round(wind / total * 100, 2) if total else None,
            })
        current_fuels = values.get((code, end_year), {})
        mix = []
        represented = 0.0
        for fuel_id, label, eia_id, renewable in FUEL_GROUPS:
            amount = max(current_fuels.get(eia_id, 0), 0)
            represented += amount
            mix.append({"id": fuel_id, "label": label, "generation_mwh": round(amount), "renewable": renewable})
        inclusive_total = max(current_fuels.get("ALL", 0), 0) + max(current_fuels.get("DPV", 0), 0)
        other = max(inclusive_total - represented, 0)
        mix.append({"id": "other", "label": "Other", "generation_mwh": round(other), "renewable": False})
        mix_total = sum(row["generation_mwh"] for row in mix)
        for row in mix:
            row["share_pct"] = round(row["generation_mwh"] / mix_total * 100, 2) if mix_total else 0
        first, last = year_metrics[start_year], year_metrics[end_year]
        preliminary.append({
            "postal_code": code,
            "hc_key": f"us-{code.lower()}",
            "name": resource.get("name") or names.get(code, code),
            "resource": resource["metrics"],
            "current": {
                "year": end_year,
                "total_generation_mwh": round(inclusive_total),
                "generation_mix": mix,
                "renewable_generation_mwh": round(last["renewable"]),
                "renewable_share_pct": history[-1]["renewable_share_pct"],
                "solar_generation_mwh": round(last["solar"]),
                "solar_share_pct": history[-1]["solar_share_pct"],
                "wind_generation_mwh": round(last["wind"]),
                "wind_share_pct": history[-1]["wind_share_pct"],
                "carbon_intensity_lbs_mwh": round(carbon[code], 1) if code in carbon else None,
            },
            "growth": {
                "start_year": start_year, "end_year": end_year,
                "renewable_cagr_pct": _cagr(first["renewable"], last["renewable"], end_year - start_year),
                "renewable_share_change_pp": round((history[-1]["renewable_share_pct"] or 0) - (history[0]["renewable_share_pct"] or 0), 2),
                "solar_cagr_pct": _cagr(first["solar"], last["solar"], end_year - start_year),
                "wind_cagr_pct": _cagr(first["wind"], last["wind"], end_year - start_year),
            },
            "history": history,
        })

    deployment = {
        metric: {row["postal_code"]: float(row["current"][f"{metric}_share_pct"] or 0) for row in preliminary}
        for metric in ("solar", "wind")
    }
    deployment_standings = {metric: _percentile(values) for metric, values in deployment.items()}
    opportunity_scores: dict[str, dict[str, float]] = {"solar": {}, "wind": {}}
    for row in preliminary:
        code = row["postal_code"]
        row["opportunity"] = {}
        for metric in ("solar", "wind"):
            resource_percentile = row["resource"][metric]["percentile"]
            deployment_percentile = deployment_standings[metric][code]["percentile"]
            score = resource_percentile - deployment_percentile
            opportunity_scores[metric][code] = score
            row["opportunity"][metric] = {
                "resource_percentile": resource_percentile,
                "deployment_percentile": deployment_percentile,
                "deployment_rank": deployment_standings[metric][code]["rank"],
                "score": score,
            }
    for metric in ("solar", "wind"):
        ranks = _percentile(opportunity_scores[metric])
        for row in preliminary:
            row["opportunity"][metric]["rank"] = ranks[row["postal_code"]]["rank"]

    timestamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "generated_at": timestamp,
        "comparison_year": end_year,
        "growth_start_year": start_year,
        "source": {
            "eia_generation": "EIA Electric Power Operational Data",
            "eia_emissions": "EIA State Electricity Profiles",
            "nasa": resource_document.get("source", {}).get("nasa", "NASA POWER"),
            "boundaries": resource_document.get("source", {}).get("boundaries", "U.S. Census Bureau"),
            "resource_method": resource_document.get("source", {}).get("method"),
            "carbon_scope": "Generation-based operational CO2 emissions rate",
        },
        "metric_definitions": {
            "solar": resource_definitions.get("solar", {}),
            "wind": resource_definitions.get("wind", {}),
            "opportunity": {"label": "Resource opportunity score", "unit": "percentile points", "range": [-100, 100]},
        },
        "states": sorted(preliminary, key=lambda row: row["name"]),
    }


def build_state_energy(generation: Path, emissions: Path, resources: Path, output: Path) -> dict[str, Any]:
    artifact = build_state_energy_artifact(
        json.loads(generation.read_text(encoding="utf-8")),
        json.loads(emissions.read_text(encoding="utf-8")),
        json.loads(resources.read_text(encoding="utf-8")),
    )
    require_valid(validate_state_energy(artifact))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, default=Path("grid-pulse/data/source/state-generation.json"))
    parser.add_argument("--emissions", type=Path, default=Path("grid-pulse/data/source/state-emissions.json"))
    parser.add_argument("--resources", type=Path, default=Path("grid-pulse/data/us-renewable-resources.json"))
    parser.add_argument("--output", type=Path, default=Path("grid-pulse/data/state-energy.json"))
    args = parser.parse_args()
    artifact = build_state_energy(args.generation, args.emissions, args.resources, args.output)
    print(f"Wrote {len(artifact['states'])} state energy profiles to {args.output}")


if __name__ == "__main__":
    main()

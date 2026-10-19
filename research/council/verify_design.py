"""Offline research-spec consistency and example arithmetic; no market/desk imports or performance claims."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def main() -> None:
    spec = json.loads((ROOT / "specifications.json").read_text())
    schema = json.loads((ROOT / "advisory.schema.json").read_text())
    assert spec["advisor_count"] == len(spec["advisors"]) == 2
    assert {a["id"] for a in spec["advisors"]} == {"C1_BRC", "C2_ECDR"}
    assert spec["mode"] == "SHADOW" and spec["influence"] == "NONE"
    assert not spec["install_as_runtime_config"] and not spec["empirical_performance_measured"]
    assert spec["common"]["mvp_additional_network_requests"] == 0
    assert not spec["common"]["sizing_authority"] and not spec["common"]["new_entry_authority"]
    assert not spec["common"]["override_stop_security_guardian_or_live_gates"]
    for profile in spec["profiles"].values():
        brc = profile["brc"]
        assert brc["required_closed_prices"] == brc["train_returns"] + brc["signal_returns"] + brc["skip_returns"] + 1
        assert not profile["execution_adapter_ready"]
    meme = spec["profiles"]["solana_meme_15m"]
    assert meme["brc"]["required_closed_prices"] * 3 == meme["brc"]["required_native_5m_bars"] == 918
    assert (meme["brc"]["required_closed_prices"] - 1) * 15 == 4575
    baseline_requests_hour = meme["ecdr"]["intensive_watch_max"] * 2 * 3600 // meme["ecdr"]["baseline_cadence_seconds"]
    assert baseline_requests_hour == 16
    assert baseline_requests_hour + 14 == spec["common"]["future_research_max_additional_quote_requests_per_rolling_hour"]
    manifests = []
    for file in sorted((ROOT / "sources").glob("*.json")):
        manifest = json.loads(file.read_text())
        assert manifest["sources"]
        for source in manifest["sources"]:
            digest = source["sha256"]
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
            path = source.get("local_research_file")
            if path and Path(path).is_file():
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        manifests.append({"file": file.name, "verified_document_records": len(manifest["sources"])})

    with localcontext() as context:
        context.prec = 40
        d = Decimal
        residual = d("0.12") - d("1.5") * d("0.04") - 16 * d("0.0002")
        z = residual / (d("0.006") * d(16).sqrt())
        assert residual == d("0.0568") and z > 2
        a_cost = (d("0.1") - d("0.098") + d("0.0001")) / d("0.1")
        assert a_cost == d("0.021")
        shock = d("0.90").ln() / d("0.025")
        assert shock < -3
        depth_ratio = (d(1_000_000) * d(9_200)).sqrt() / (d(1_000_000) * d(10_000)).sqrt()
        assert depth_ratio > d("0.95")
        assert d(9_200) / d(1_000_000) * 100 == d("0.92")
        recovery_ceiling = min(d("1.2") * d("0.015"), d("0.75") * d("0.03"), d("0.03"))
        assert d("0.018") <= recovery_ceiling and d("0.016") <= recovery_ceiling
        b_cost = (d("0.1") - d("0.0985") + d("0.0001")) / d("0.1")
        assert b_cost == d("0.016")
        target = (d("0.92") * d("1.00")).sqrt()
        target_distance = target / d("0.92") - 1
        assert target_distance > b_cost + d("0.02")
        arithmetic = {"brc_residual_log_sum": str(residual), "brc_diagnostic_z": str(z),
                      "brc_current_roundtrip_cost": str(a_cost), "ecdr_shock_diagnostic_z": str(shock),
                      "ecdr_depth_ratio": str(depth_ratio), "ecdr_current_roundtrip_cost": str(b_cost),
                      "ecdr_reference_target": str(target), "ecdr_target_distance": str(target_distance)}

    schema_checked = False
    try:
        import jsonschema
    except ModuleNotFoundError:
        pass  # Stdlib arithmetic checks still run; do not claim optional schema validation happened.
    else:
        jsonschema.Draft202012Validator.check_schema(schema)
        example = {
            "schema_version": "kestrel-advisory-1", "advisory_id": "DESIGN_EXAMPLE_NOT_RUNTIME_OUTPUT",
            "advisor_id": "C1_BRC", "spec_version": 1, "spec_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
            "code_commit": spec["audited_commit"], "mode": "SHADOW", "influence": "NONE",
            "primary_decision_hash": None, "primary_action": "NO_TRADE", "reference_action": "PROSPECTIVE_LONG",
            "asset": {"profile": "solana_meme_15m", "instrument_id": "illustration-only", "pool_or_venue": "none",
                      "source": "design-example", "price_currency": "USD", "account_numeraire": "SOL",
                      "membership_id": "not-enrolled"},
            "decision_at": "2026-10-19T12:00:00Z", "computed_at": "2026-10-19T12:00:01Z",
            "latest_event_end": None, "latest_input_available_at": None, "expires_at": "2026-10-19T12:00:00Z",
            "signal_direction": "UNKNOWN", "stance": "ABSTAIN", "candidate_signal": {"residual_z": str(z)},
            "horizon": {"maximum": 28800, "unit": "SECONDS"},
            "rationale_codes": ["DESIGN_EXAMPLE_NOT_RUNTIME_OUTPUT", "INPUT_MISSING"], "inputs": [],
            "freshness": {"status": "MISSING", "missing": ["real-market-inputs"], "stale": []},
            "evidence": {"training_bars": 0, "signal_bars": 0, "quote_pairs": 0, "forward_closed_trades": 0,
                         "effective_observations": None},
            "uncertainty": {"calibrated": False, "interval": None, "reason": "No empirical validation"},
            "regime": {"status": "MISSING", "label": None}, "shared_dependencies": ["prices", "SOL"],
            "economics": {"status": "UNMEASURED", "ticket_raw": None, "roundtrip_cost_fraction": None,
                          "expected_net_return": None}, "input_sha256": hashlib.sha256(canonical({})).hexdigest(),
        }
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        validator.validate(example)
        invalid_examples = []
        for key, value in (("mode", "LIVE"), ("influence", "SIZING"), ("advisor_id", "C3")):
            changed = copy.deepcopy(example)
            changed[key] = value
            invalid_examples.append(changed)
        changed = copy.deepcopy(example)
        changed["candidate_signal"] = {"win_probability": "0.99"}
        invalid_examples.append(changed)
        changed = copy.deepcopy(example)
        changed["candidate_signal"] = {"residual_z": {"nested_probability": 0.99}}
        invalid_examples.append(changed)
        for changed in invalid_examples:
            assert not validator.is_valid(changed)
        schema_checked = True

    power = ((2.13 + 0.84) * 0.10 / 0.02) ** 2
    assert 220 < power < 221
    print(json.dumps({"status": "PASS", "scope": "Design arithmetic and contracts only; not a backtest",
                      "schema_validated_with_available_jsonschema": schema_checked,
                      "strategy_ids": [a["id"] for a in spec["advisors"]], "source_manifests": manifests,
                      "brc_closed_prices_meme": 306, "brc_native_5m_bars_meme": 918,
                      "future_baseline_quote_requests_hour": baseline_requests_hour,
                      "future_total_research_quote_requests_hour_max": 30,
                      "illustrative_independent_power_observations_ceil": math.ceil(power),
                      "illustrative_arithmetic": arithmetic, "forward_strategy_trades_tested": 0,
                      "production_behavior_changed": False}, indent=2))


if __name__ == "__main__":
    main()

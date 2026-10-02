"""Price one search. HTTP routes and the in-process eval both call this."""

import httpx

from src import clock, config
from src.bre.rules import BusinessRulesEngine
from src.data.database import get_latest_price, save_price_with_outbox
from src.pricing import quote_fare
from src.service.schemas import PriceResponse, SearchRequest


def build_causal_payload(
    driver_id: str,
    distance_km: float,
    duration_sec: float,
    hour: float,
    past_trips: float,
    avg_surge: float,
) -> dict:
    """Features known before the surcharge. Price and surge_bonus are not sent.

    ``hour`` is the virtual hour DPE used to pick the arm. The score CPE returns
    is the effect of an additive surcharge versus the base fare on whether this
    driver accepts. It is not a discount, and it is not the multiplicative arm.
    """
    return {
        "driver_id": driver_id,
        "features": {
            "distance_km": float(distance_km),
            "duration_sec": float(duration_sec),
            "hour_of_day": float(hour),
            "past_trips": float(past_trips),
            "avg_surge": float(avg_surge),
        },
    }


def price_search(feature_store, driver_history_store, payload: SearchRequest) -> PriceResponse:
    """Graph fare, switchback surge, business rules, optional causal override.

    Level 1 is the isochrone. Levels 2–4 are fallbacks. A causal timeout
    does not fail the quote.
    """
    node_id = None
    h3_cell = None
    explanation = ""

    vh_raw = feature_store.redis_client.get("sim:virtual_hour")
    if vh_raw is not None:
        virtual_hour = float(vh_raw)
    else:
        virtual_hour = (clock.now() / 3600.0) % 24
    current_hour = int(virtual_hour)
    is_additive = (current_hour // config.SWITCHBACK_WINDOW_HOURS) % 2 == 1
    test_group = "ADDITIVE" if is_additive else "MULTIPLICATIVE"

    trip_duration_sec = 900.0
    trip_dist_km = 7.0
    base_fare = config.BASE_PRICE
    hour = current_hour % 24

    causal_uplift_score: float | None = None
    causal_override = False
    causal_recommended_treatment: str | None = None

    def respond(
        *,
        cell: str,
        price: float,
        fare: float,
        multiplier: float,
        text: str,
        fail_static: bool,
        bonus: float,
        formula: str,
    ) -> PriceResponse:
        return PriceResponse(
            h3_index=cell,
            price=price,
            base_price=fare,
            surge_multiplier=multiplier,
            explanation=text,
            is_fail_static=fail_static,
            node_id=node_id,
            test_group=test_group,
            surge_bonus=bonus,
            payout_formula=formula,
            causal_uplift_score=causal_uplift_score,
            causal_override=causal_override,
            causal_recommended_treatment=causal_recommended_treatment,
            distance_km=trip_dist_km,
            duration_sec=trip_duration_sec,
        )

    try:
        if config.FAULT_INJECTION_ACTIVE:
            raise RuntimeError("CRITICAL ERROR: Simulated ML-microservice failure / crash!")

        node_id = feature_store.graph.snap_to_node(payload.lat, payload.lon)
        h3_cell = feature_store.graph.nodes[node_id]["h3_cell"]
        feature_store.register_search_request(payload.search_id, payload.lat, payload.lon)

        edge_speeds = feature_store.get_dynamic_edge_weights()
        if payload.dest_lat is not None and payload.dest_lon is not None:
            dest_node_id = feature_store.graph.snap_to_node(payload.dest_lat, payload.dest_lon)
            d_time, d_dist = feature_store.graph.shortest_path_od(node_id, dest_node_id, edge_speeds)
            if d_time != float("inf") and d_time > 0.0:
                trip_duration_sec = d_time
                trip_dist_km = d_dist

        base_fare = max(
            trip_duration_sec * config.RATE_PER_SECOND + trip_dist_km * config.RATE_PER_KILOMETER,
            config.MIN_FARE,
        )

        def compute_surge_price(ds: float) -> tuple[float, float, str]:
            quoted = quote_fare(trip_duration_sec, trip_dist_km, ds, test_group)
            return quoted.price, quoted.surge_bonus, quoted.payout_formula

        try:
            features = feature_store.get_features_graph(node_id, edge_speeds)
            ds_ratio_raw = features["demand_supply_ratio"]

            ds_ratios = [ds_ratio_raw]
            weights = [1.0]
            for neighbor in feature_store.graph.adj[node_id]:
                neighbor_features = feature_store.get_features_graph(neighbor, edge_speeds)
                ds_ratios.append(neighbor_features["demand_supply_ratio"])
                weights.append(0.2)
            smoothed_ds_ratio = sum(r * w for r, w in zip(ds_ratios, weights, strict=True)) / sum(weights)

            proposed_price, surge_bonus, payout_formula = compute_surge_price(smoothed_ds_ratio)

            # CPE knows one contrast: additive surcharge versus the base fare.
            # The multiplicative hour is a different quote, and a search without
            # a driver has no driver-level history to score.
            if config.CAUSAL_ENABLED and test_group == "ADDITIVE" and payload.driver_id:
                try:
                    causal_url = config.CAUSAL_ENGINE_URL
                    timeout_sec = config.CAUSAL_ENGINE_TIMEOUT_SEC

                    p_trips, a_surge = 0.0, 0.0
                    if driver_history_store is not None:
                        p_trips, a_surge = driver_history_store.get_driver_features(
                            payload.driver_id, h3_cell=h3_cell
                        )

                    resp = httpx.post(
                        f"{causal_url}/predict_uplift",
                        json=build_causal_payload(
                            payload.driver_id,
                            trip_dist_km,
                            trip_duration_sec,
                            hour,
                            p_trips,
                            a_surge,
                        ),
                        timeout=timeout_sec,
                    )
                    if resp.status_code == 200:
                        cdata = resp.json()
                        causal_uplift_score = cdata.get("uplift_score")
                        causal_recommended_treatment = cdata.get("recommended_treatment")
                        # A score under the threshold changes the fare only when CPE
                        # says the holdout Qini interval is entirely above zero.
                        # Missing or false means the cut would follow noise.
                        supports_decision = cdata.get("ranking_supports_decision") is True
                        chosen = cdata.get("score_threshold")
                        # Version 2: the cutoff is the calibration-chosen theta.
                        # A missing theta does not fall back to 0.05.
                        if (
                            supports_decision
                            and chosen is not None
                            and causal_uplift_score is not None
                            and float(causal_uplift_score) < -float(chosen)
                        ):
                            causal_override = True
                            surge_bonus = 0.0
                            proposed_price = base_fare
                            test_group = "CAUSAL_NO_SURGE"
                            payout_formula = f"{round(base_fare, 1)} + 0.0 (no surcharge)"
                except Exception:
                    pass

            prev_price_record = get_latest_price(h3_cell)
            previous_price = prev_price_record["price"] if prev_price_record else None

            final_price, bre_explanation = BusinessRulesEngine.apply_rules(
                h3_cell=h3_cell,
                proposed_price=proposed_price,
                previous_price=previous_price,
            )
            explanation = f"Graph-based pricing. {bre_explanation}"
            if causal_override:
                score_str = f"{causal_uplift_score:.4f}" if causal_uplift_score is not None else "N/A"
                chosen_str = f"{float(chosen):.2f}" if chosen is not None else "n/a"
                explanation = (
                    f"{explanation} Causal override: "
                    f"uplift={score_str} < -{chosen_str}."
                )
            fallback_level = 0

        except Exception as level1_error:
            print(f"[pricing] [WARN] Графовый расчет Уровня 1 не удался ({level1_error}). Откат на Уровень 2.")
            try:
                adj_nodes = list(feature_store.graph.adj[node_id].keys()) + [node_id]
                now = clock.now()
                ts_bucket = int(now // 60) * 60

                active_drivers = set()
                buckets_for_drivers = [ts_bucket, ts_bucket - 60]
                for node in adj_nodes:
                    for bucket in buckets_for_drivers:
                        key = f"node:{node}:ts:{bucket}:drivers"
                        members = feature_store.redis_client.zrangebyscore(
                            key, now - config.DRIVER_TTL_SEC, now
                        )
                        for member in members:
                            parts = member.split(":")
                            if len(parts) == 4:
                                active_drivers.add(parts[0])

                drivers_in_isochrone = len(active_drivers)

                searches_count = 0
                for i in range(5):
                    bucket = ts_bucket - (i * 60)
                    key = f"node:{node_id}:ts:{bucket}:searches"
                    searches_count += feature_store.redis_client.zcard(key)

                searches_normalized = searches_count * (config.DRIVER_TTL_SEC / config.SEARCH_TTL_SEC)
                ds_ratio = searches_normalized / max(drivers_in_isochrone, 0.5)

                proposed_price, surge_bonus, payout_formula = compute_surge_price(ds_ratio)

                prev_price_record = get_latest_price(h3_cell)
                previous_price = prev_price_record["price"] if prev_price_record else None

                final_price, bre_explanation = BusinessRulesEngine.apply_rules(
                    h3_cell=h3_cell,
                    proposed_price=proposed_price,
                    previous_price=previous_price,
                )
                explanation = f"Fallback Level 2 (Node k-Ring). {bre_explanation}"
                fallback_level = 1

            except Exception as level2_error:
                print(f"[pricing] [WARN] Расчет Уровня 2 не удался ({level2_error}). Откат на Уровень 3.")
                features = feature_store.get_features(h3_cell)
                ds_ratio = features["demand_supply_ratio"]

                proposed_price, surge_bonus, payout_formula = compute_surge_price(ds_ratio)

                prev_price_record = get_latest_price(h3_cell)
                previous_price = prev_price_record["price"] if prev_price_record else None

                final_price, bre_explanation = BusinessRulesEngine.apply_rules(
                    h3_cell=h3_cell,
                    proposed_price=proposed_price,
                    previous_price=previous_price,
                )
                explanation = f"Fallback Level 3 (Flat H3). {bre_explanation}"
                fallback_level = 2

        surge_multiplier = round(final_price / base_fare, 2)

        save_price_with_outbox(
            h3_index=h3_cell,
            price=final_price,
            base_price=base_fare,
            surge_multiplier=surge_multiplier,
            explanation=f"[{fallback_level}] {explanation}",
            test_group=test_group,
            surge_bonus=surge_bonus,
            payout_formula=payout_formula,
        )

        if driver_history_store is not None:
            driver_history_store.record_trip(
                driver_id=payload.driver_id,
                surge_bonus=surge_bonus,
                h3_cell=h3_cell,
            )

        return respond(
            cell=h3_cell,
            price=final_price,
            fare=base_fare,
            multiplier=surge_multiplier,
            text=explanation,
            fail_static=False,
            bonus=surge_bonus,
            formula=payout_formula,
        )

    except Exception as error:
        print(f"[pricing] [WARN] Fail-Static сработал (Уровень 4): {error}")
        explanation = f"Fallback Level 4 (Fail-Static). DB/Redis/ML error: {error}"
        fallback_h3 = h3_cell if h3_cell else "unknown_h3"
        try:
            save_price_with_outbox(
                h3_index=fallback_h3,
                price=base_fare,
                base_price=base_fare,
                surge_multiplier=1.0,
                explanation=explanation,
                test_group=test_group,
                surge_bonus=0.0,
                payout_formula=f"{round(base_fare, 1)} (Fail-Static)",
            )
        except Exception as db_err:
            print(f"[pricing] [ERROR] Не удалось записать лог сбоя в БД: {db_err}")

        return respond(
            cell=fallback_h3,
            price=base_fare,
            fare=base_fare,
            multiplier=1.0,
            text=explanation,
            fail_static=True,
            bonus=0.0,
            formula=f"{round(base_fare, 1)} (Fail-Static)",
        )

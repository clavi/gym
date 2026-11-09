"""Pure metric calculations on frozen SQLite and frozen, validated evidence."""
from __future__ import annotations

import json
import math
from collections import Counter

if __package__:
    from .analysis_semantics import interpret, numeric
    from .analysis_sources import dates
else:
    from analysis_semantics import interpret, numeric
    from analysis_sources import dates

METRICS_VERSION = "analysis-metrics-v2.1"
DEFINITIONS = {
    "training.sessions": {"source": "training_session", "filter": "datestr in inclusive window", "formula": "COUNT(*)", "unit": "次"},
    "training.days": {"source": "training_session.datestr", "filter": "inclusive window", "formula": "COUNT(DISTINCT datestr)", "unit": "天"},
    "training.completed_segments": {"source": "training_set JOIN training_session", "filter": "done=1 AND is_warmup=0", "formula": "COUNT(*)", "unit": "normalized_row"},
    "training.work_sets": {"source": "Raw derived logical groups", "filter": "complete resistance non-warmup groups; full structure known", "formula": "COUNT(logical group keys)", "unit": "logical_set"},
    "training.volume": {"source": "Raw derived independent segments and non-segment records", "filter": "verified complete non-warmup external positive finite kg and reps", "formula": "SUM(weight_kg * reps), status adjudicated over all sources", "unit": "kg·次"},
    "training.volume_coverage": {"source": "verified segment facts", "filter": "complete non-warmup candidates", "formula": "calculable/candidate; unknown structure or zero denominator => null", "unit": "ratio"},
    "training.rpe_mean": {"source": "independent segment RPE", "filter": "done, non-warmup resistance, valid 6..10 half step", "formula": "SUM(rpe)/valid independent samples", "unit": "RPE"},
    "training.rpe_coverage": {"source": "independent segment RPE", "filter": "same candidate scope as rpe_mean", "formula": "valid independent scores/candidates", "unit": "ratio"},
    "training.high_rpe_share": {"source": "independent valid segment RPE", "filter": "same scope as rpe_mean", "formula": "COUNT(rpe>=9)/COUNT(valid independent rpe)", "unit": "ratio"},
    "training.max_weight": {"source": "verified independent segments", "filter": "done, non-warmup, positive external kg; movement grouped; no progression claim", "formula": "MAX(weight_kg)", "unit": "kg"},
    "training.session_duration": {"source": "training_session.duration_s", "filter": "finite nonnegative", "formula": "SUM(duration_s), recorded duration sum, not deduplicated time", "unit": "s"},
    "food.recorded_day_mean": {"source": "food_day", "filter": "each nutrient independently valid nonnegative intake", "formula": "SUM(intake)/valid days", "unit": "nutrient-dependent/day"},
    "food.target_delta": {"source": "food_day totals and same-day foods.limits", "filter": "valid intake and positive or evidenced zero target", "formula": "intake-target; signed daily values and paired mean", "unit": "g or kcal"},
    "food.target_delta_ratio": {"source": "same-day food targets", "filter": "valid intake and target>0", "formula": "mean of daily (intake-target)/target; separate dates", "unit": "ratio"},
    "food.target_attainment": {"source": "same-day food pairs and confirmed completeness", "filter": "explicit user tolerance and completeness evidence required", "formula": "within tolerance days/eligible paired days", "unit": "ratio"},
    "body.measurement_delta": {"source": "body_record", "filter": "same type and unit, two valid real points", "formula": "last value-first value; include exact dates", "unit": "source unit"},
    "body.window_mean": {"source": "body_record", "filter": "preregistered minimum frequency, max gap and comparable conditions", "formula": "mean of real points only; no filling or interpolation", "unit": "source unit"},
    "template.execution_ratio": {"source": "historical schedule/version and unique target mapping", "filter": "not yet implemented; current template is not historical schedule", "formula": "null until mapping and schedule are confirmed", "unit": "ratio"},
}


def volume_summary(segments, other, coverage):
    """Merge sufficient evidence, not daily values or ordered status labels.

    The arguments are additive, so direct weekly and daily-then-weekly
    adjudication use exactly the same evidence and reason counts.
    """
    counts = Counter(x["volume_reason"] for x in segments)
    counts.update(x["reason"] for x in other)
    values = [x["volume"] for x in segments if x["volume_reason"] == "calculable"]
    known = all(c["status"] in ("synced_empty", "synced_records") for c in coverage) and bool(coverage)
    if not known:
        counts["coverage_unknown"] += sum(c["status"] not in ("synced_empty", "synced_records") for c in coverage)
    structure_known = known and not any(x["reason"] == "unverified_semantics" for x in other) and all(
        s["done"] is not None and s["warmup"] is not None for s in segments) and not any(
        x.get("detail") == "native_cardio_without_segments" for x in other)
    candidate_n = sum(x["candidate"] for x in segments) if structure_known else None
    return _volume_result(counts, len(values), math.fsum(values), candidate_n, len(coverage),
                          sum(c["status"] in ("synced_empty", "synced_records") for c in coverage),
                          all(c["status"] == "synced_empty" for c in coverage))


def _volume_result(counts, calculable_n, total, candidate_n, source_days, covered_days, all_empty):
    known = source_days > 0 and source_days == covered_days
    relevant_unknown = any(counts[r] for r in ("unverified_semantics", "unit_unknown", "zero_load_unknown"))
    bad = any(n for r, n in counts.items() if r not in ("calculable", "not_completed", "warmup"))
    if calculable_n:
        value = total
        status = "complete" if known and not bad else "partial"
    elif known and all_empty and not any(counts.values()):
        value, status = 0, "no_training"
    elif known and any(counts.values()) and not bad:
        value, status = 0, "no_completed_work"
    else:
        value = None
        status = ("coverage_unknown" if not known else "unverified_semantics" if relevant_unknown
                  else "insufficient_data" if counts["missing_fields"] or counts["invalid_fields"]
                  else "not_applicable")
    return {"value": value, "status": status, "unit": "kg·次", "sample_n": calculable_n,
            "numerator": calculable_n, "denominator": candidate_n,
            "coverage": calculable_n / candidate_n if candidate_n else None,
            "reasons": {k: v for k, v in sorted(counts.items()) if v},
            "source_days": source_days, "covered_days": covered_days,
            "aggregation_evidence": {"calculable_sum": total, "all_sources_empty": all_empty}}


def merge_volume_summaries(summaries):
    """Aggregate evidence/counters, never SUM(null) or sort daily status labels."""
    counts = Counter()
    for s in summaries:
        counts.update(s["reasons"])
    candidate_n = sum(s["denominator"] for s in summaries) if all(s["denominator"] is not None for s in summaries) else None
    return _volume_result(counts, sum(s["sample_n"] for s in summaries),
                          math.fsum(s["aggregation_evidence"]["calculable_sum"] for s in summaries), candidate_n,
                          sum(s["source_days"] for s in summaries), sum(s["covered_days"] for s in summaries),
                          all(s["aggregation_evidence"]["all_sources_empty"] for s in summaries))


def metric(mid, start, end, value, unit, *, status="complete", sample_n=0, **extra):
    return {"metric_id": mid, "definition_version": METRICS_VERSION, "window": [start, end],
            "definition_ref": f"inputs/metrics.json#/definitions/{mid}",
            "value": value, "unit": unit, "counting_unit": None, "status": status, "sample_n": sample_n,
            "numerator": None, "denominator": None, "reasons": {}, "query_id": "calculate-v1",
            "evidence_ids": [], **extra}


def food_pair(intake, target, confirmation=None):
    i, istate = numeric(intake)
    t, tstate = numeric(target)
    confirmation = confirmation or {}
    state = confirmation.get("state")
    if state in ("unset", "unknown", "invalid"):
        reason = {"unset": "target_unset", "unknown": "target_unknown", "invalid": "target_invalid"}[state]
    elif tstate == "missing":
        reason = "target_missing"
    elif tstate != "valid" or t < 0:
        reason = "target_invalid"
    elif t == 0 and not (state == "valid_zero" and confirmation.get("evidence")):
        reason = "target_zero_unknown"
    elif istate != "valid" or i < 0:
        reason = "intake_" + ("invalid" if istate == "valid" else istate)
    else:
        reason = None
    return {"intake": i if istate == "valid" and i >= 0 else None,
            "target": t, "target_state": reason if reason and reason.startswith("target_") else
                "valid_zero" if t == 0 else "valid_positive" if tstate == "valid" and t > 0 else "unknown",
            "delta": None if reason else i - t,
            "ratio": (i - t) / t if not reason and t > 0 else None,
            "delta_reason": reason, "ratio_reason": reason or ("zero_target" if t == 0 else None),
            "target_evidence": confirmation.get("evidence", "positive_numeric_source_target" if t and t > 0 else "unknown")}


def calculate(conn, sources, task, semantic_rules=None):
    start, end = task["start"], task["end"]
    enabled = task.get("modules", ["training"])
    axis = list(dates(start, end))
    auxiliary_sources = [s for s in sources if s.get("domain", "training") != "training"]
    source_map = {s["date"]: s for s in sources if s.get("domain", "training") == "training"}
    sources = [source_map.get(d, {"date": d, "status": "coverage_unknown", "payload": None,
                                  "projection_matches": False}) for d in axis]
    daily = {d: {"date": d} for d in axis}
    metrics, exclusions = [], []
    facts = {"segments": [], "groups": [], "other": [], "rounds": []}

    def add(mid, value, unit, **kwargs):
        metrics.append(metric(mid, start, end, value, unit, **kwargs))

    if "training" in enabled:
        source_map = {s["date"]: s for s in sources}
        for d in axis:
            src = source_map[d]
            if src.get("payload") is not None and src.get("projection_matches"):
                part = interpret(src["payload"], d, src["sha256"], semantic_rules)
                for k in facts:
                    facts[k].extend(part[k])
        sessions = [dict(r) for r in conn.execute(
            "SELECT * FROM training_session WHERE datestr BETWEEN ? AND ? ORDER BY datestr,id", (start, end))]
        rows = [dict(r) for r in conn.execute(
            "SELECT t.*,s.datestr FROM training_set t JOIN training_session s ON s.id=t.session_id "
            "WHERE s.datestr BETWEEN ? AND ?", (start, end))]
        full = all(s["status"] in ("synced_empty", "synced_records") for s in sources)
        evidence = [s["sha256"] for s in sources if s.get("sha256")]
        for mid, value, unit in (("training.sessions", len(sessions), "次"),
                                 ("training.days", len({s["datestr"] for s in sessions}), "天"),
                                 ("training.completed_segments", sum(r["done"] == 1 and r["is_warmup"] == 0 for r in rows), "normalized_row")):
            add(mid, value, unit, sample_n=value, counting_unit=unit,
                status="complete" if full else "recorded_only", evidence_ids=evidence)
        volume = volume_summary(facts["segments"], facts["other"], sources)
        add("training.volume", **{k: v for k, v in volume.items() if k not in ("coverage", "source_days", "covered_days")},
            evidence_ids=evidence, source_coverage={k: volume[k] for k in ("source_days", "covered_days")})
        add("training.volume_coverage", volume["coverage"], "ratio", sample_n=volume["sample_n"],
            numerator=volume["numerator"], denominator=volume["denominator"],
            status="complete" if volume["denominator"] else "unknown_denominator", evidence_ids=evidence)
        structure_known = full and not any(o["reason"] == "unverified_semantics" for o in facts["other"])
        groups = facts["groups"]
        verified_groups = sum(g["state"] == "complete" and g["warmup"] is False and g["applicable"] for g in groups)
        all_groups_known = structure_known and all(g["state"] not in ("unknown", "conflict") and g["warmup"] is not None for g in groups) and all(
            s["movement_type"] != "unknown" for s in facts["segments"])
        add("training.work_sets", verified_groups if all_groups_known else None, "logical_set",
            status="complete" if all_groups_known else "unverified_structure", sample_n=len(groups),
            counting_unit="logical_set", verified_group_n=verified_groups,
            partial_group_n=sum(g["state"] == "partial" for g in groups), evidence_ids=evidence)
        candidates = [s for s in facts["segments"] if s["candidate"] and s["movement_type"] == "resistance" and s["type_verified"]]
        scored = [s for s in candidates if s["rpe"] is not None]
        # Unknown completion/structure affects the denominator, not valid independent samples.
        denominator = len(candidates) if structure_known and all(s["done"] is not None and s["type_verified"] for s in facts["segments"]) else None
        rpes = [s["rpe"] for s in scored]
        high = sum(r >= 9 for r in rpes)
        for mid, value, num, den, unit in (
            ("training.rpe_mean", sum(rpes)/len(rpes) if rpes else None, sum(rpes), len(rpes), "RPE"),
            ("training.rpe_coverage", len(rpes)/denominator if denominator else None, len(rpes), denominator, "ratio"),
            ("training.high_rpe_share", high/len(rpes) if rpes else None, high, len(rpes), "ratio")):
            add(mid, value, unit, status="complete" if value is not None else "insufficient_data",
                sample_n=len(rpes), numerator=num, denominator=den, counting_unit="independent_segment",
                evidence_ids=[s["key"] for s in scored])
        durations = [numeric(s["duration_s"])[0] for s in sessions]
        valid_durations = [d for d in durations if d is not None and d >= 0]
        add("training.session_duration", sum(valid_durations) if valid_durations else None, "s",
            status="complete" if len(valid_durations) == len(sessions) and valid_durations else "partial" if valid_durations else "insufficient_data",
            sample_n=len(valid_durations), reasons={"invalid_or_missing": len(sessions)-len(valid_durations)})
        for name in sorted({s["movement"] for s in facts["segments"]}):
            valid = [s for s in candidates if s["movement"] == name and s["load_role"] == "external"
                     and s["weight_kg"] is not None and s["weight_kg"] > 0 and s["weight_state"] == "valid"
                     and s["volume_reason"] not in ("unverified_semantics", "unit_unknown", "zero_load_unknown")]
            add("training.max_weight", max((s["weight_kg"] for s in valid), default=None), "kg",
                status="descriptive_only" if valid else "not_applicable", sample_n=len(valid), group={"movement": name},
                evidence_ids=[s["key"] for s in valid], comparison="not_evaluated; equipment/laterality conditions require confirmation")
        for s in facts["segments"]:
            if s["volume_reason"] != "calculable":
                exclusions.append({"metric_id": "training.volume", "record_key": s["key"], "date": s["date"],
                                   "reason": s["volume_reason"], "auxiliary_reasons": json.dumps(
                                       {"weight": s["weight_state"], "reps": s["reps_state"]})})
            if s["rpe"] is None or not s["candidate"] or not s["type_verified"] or s["movement_type"] != "resistance":
                rpe_reason = ("completion_or_warmup_unknown" if s["done"] is None or s["warmup"] is None else
                              "not_completed" if s["done"] is False else "warmup" if s["warmup"] is True else
                              "type_unknown" if not s["type_verified"] else "not_applicable_type" if s["movement_type"] != "resistance" else
                              "shared_parent_score" if s["rpe_level"] == "shared_parent" else s["rpe_state"])
                exclusions.append({"metric_id": "training.rpe_mean", "record_key": s["key"], "date": s["date"],
                                   "reason": rpe_reason, "auxiliary_reasons": "[]"})
        for g in groups:
            if g["state"] != "complete" or g["warmup"] is not False or not g["applicable"]:
                exclusions.append({"metric_id": "training.work_sets", "record_key": g["key"], "date": g["date"],
                                   "reason": g["state"] if g["state"] != "complete" else "warmup_or_unknown" if g["warmup"] is not False else "type_not_applicable_or_unknown",
                                   "auxiliary_reasons": "[]"})
        for o in facts["other"]:
            exclusions.append({"metric_id": "training.volume", "record_key": o["key"], "date": o["date"],
                                "reason": o["reason"], "auxiliary_reasons": o.get("detail", "unknown")})
        for src in sources:
            if src["status"] not in ("synced_records", "synced_empty"):
                exclusions.append({"metric_id": "training.volume", "record_key": f"day:{src['date']}", "date": src["date"],
                                   "reason": "coverage_unknown", "auxiliary_reasons": src["status"]})
        for d in axis:
            ds = [s for s in sessions if s["datestr"] == d]
            seg = [s for s in facts["segments"] if s["date"] == d]
            oth = [s for s in facts["other"] if s["date"] == d]
            v = volume_summary(seg, oth, [source_map[d]])
            daily[d].update(training_sync=source_map[d]["status"],
                            training_recording=task.get("recording_completeness", {}).get("training", {}).get(d, {}).get("state", "unknown"),
                            training_sessions=len(ds), training_titles=json.dumps([s["title"] for s in ds], ensure_ascii=False),
                            completed_segments=sum(r["done"] == 1 and r["is_warmup"] == 0 for r in rows if r["datestr"] == d),
                            work_sets=sum(g["state"] == "complete" and g["warmup"] is False and g["applicable"] for g in groups if g["date"] == d) if all_groups_known else None,
                            volume=v["value"], volume_status=v["status"], volume_reasons=json.dumps(v["reasons"]),
                            cardio=json.dumps([o for o in oth if o.get("detail") == "native_cardio_without_segments"], ensure_ascii=False))
    if "food" in enabled:
        food_evidence = {s["date"]: s for s in auxiliary_sources if s["domain"] == "food"}
        nutrients = {"calories": ("totalCal", "calories", "kcal"), "protein": ("totalProtein", "protein", "g"),
                     "carb": ("totalCarb", "carb", "g"), "fat": ("totalFat", "fat", "g")}
        food_rows = {r["datestr"]: json.loads(r["data_json"]) for r in conn.execute(
            "SELECT datestr,data_json FROM food_day WHERE datestr BETWEEN ? AND ?", (start, end))}
        for nutrient, (total_key, target_key, unit) in nutrients.items():
            pairs = {}
            for d in axis:
                record = food_rows.get(d, {})
                confirmation = task.get("target_validity", {}).get(d, {}).get(nutrient)
                pair = food_pair(record.get("totals", {}).get(total_key), record.get("foods", {}).get("limits", {}).get(target_key), confirmation)
                pairs[d] = pair
                daily[d].update(food_sync=food_evidence.get(d, {}).get("status", "coverage_unknown"), food_recording=task.get("recording_completeness", {}).get("food", {}).get(d, {}).get("state", "unknown"))
                for k in ("intake", "target", "target_state", "delta", "ratio", "delta_reason", "ratio_reason"):
                    daily[d][f"food_{nutrient}_{k}"] = pair[k]
                for mid, reason in (("food.target_delta", pair["delta_reason"]), ("food.target_delta_ratio", pair["ratio_reason"])):
                    if reason:
                        exclusions.append({"metric_id": mid, "record_key": f"food_day:{d}:{nutrient}", "date": d,
                                           "reason": reason, "auxiliary_reasons": "[]"})
            for mid, field, result_unit in (("food.recorded_day_mean", "intake", unit+"/日"),
                                             ("food.target_delta", "delta", unit), ("food.target_delta_ratio", "ratio", "ratio")):
                selected = {d: p[field] for d, p in pairs.items() if p[field] is not None}
                add(mid, sum(selected.values())/len(selected) if selected else None, result_unit,
                    status="recorded_only" if selected else "insufficient_data", sample_n=len(selected),
                    numerator=sum(selected.values()) if selected else None, denominator=len(selected),
                    dates=list(selected), group={"nutrient": nutrient}, aggregation="mean_of_daily_values",
                    daily_values=selected, calendar_days=len(axis),
                    evidence_ids=[f"food_day:{d}:{nutrient}" for d in selected])
            attainment = []
            tolerance = task.get("food_tolerance", {}).get(nutrient)
            for d, p in pairs.items():
                complete = task.get("recording_completeness", {}).get("food", {}).get(d, {})
                if p["delta"] is None or complete.get("state") != "complete" or not complete.get("evidence") or not tolerance or not tolerance.get("evidence"):
                    continue
                absolute, ast = numeric(tolerance.get("absolute"))
                relative, rst = numeric(tolerance.get("relative"))
                bound = absolute if ast == "valid" and absolute >= 0 else (p["target"] * relative if rst == "valid" and relative >= 0 and p["target"] > 0 else None)
                if bound is not None:
                    attainment.append((d, abs(p["delta"]) <= bound))
            # Target changes define distinct contiguous periods, never one mixed execution ratio.
            periods = []
            for d, p in pairs.items():
                target_key = (p["target_state"], p["target"])
                if not periods or periods[-1]["target_key"] != target_key:
                    periods.append({"target_key": target_key, "dates": []})
                periods[-1]["dates"].append(d)
            for period in periods:
                eligible = [(d, ok) for d, ok in attainment if d in period["dates"]]
                add("food.target_attainment", sum(ok for _, ok in eligible)/len(eligible) if eligible else None, "ratio",
                    status="eligible" if eligible else "blocked", sample_n=len(eligible), numerator=sum(ok for _, ok in eligible),
                    denominator=len(eligible), dates=[d for d, _ in eligible],
                    group={"nutrient": nutrient, "target": period["target_key"][1],
                           "period_start": period["dates"][0], "period_end": period["dates"][-1]},
                    reasons={} if eligible else {"requires_valid_pair_confirmed_recording_and_tolerance": 1})
    if "body" in enabled:
        body_evidence = {s["date"]: s for s in auxiliary_sources if s["domain"] == "body"}
        points = [dict(r) for r in conn.execute("SELECT datestr,type,value,unit FROM body_record WHERE datestr BETWEEN ? AND ? ORDER BY datestr,type", (start, end))]
        for d in axis:
            daily[d]["body_measurements"] = json.dumps([p for p in points if p["datestr"] == d], ensure_ascii=False)
            daily[d]["body_sync"] = body_evidence.get(d, {}).get("status", "coverage_unknown")
        for kind, unit in sorted({(p["type"], p["unit"] or "unknown") for p in points}):
            selected = [{**p, "value": numeric(p["value"])[0]} for p in points if p["type"] == kind and (p["unit"] or "unknown") == unit and numeric(p["value"])[1] == "valid"]
            valid = len(selected) >= 2 and unit != "unknown"
            add("body.measurement_delta", selected[-1]["value"]-selected[0]["value"] if valid else None, unit,
                status="descriptive_only" if valid else "insufficient_data", sample_n=len(selected),
                group={"type": kind}, points=selected, dates=[p["datestr"] for p in selected],
                reasons={"measurement_conditions_unknown": 1})
            rule = task.get("body_window", {})
            minimum = rule.get("minimum_measurements")
            max_gap = rule.get("maximum_gap_days")
            from datetime import date as _date
            gaps = [(_date.fromisoformat(b["datestr"])-_date.fromisoformat(a["datestr"])).days for a, b in zip(selected, selected[1:])]
            can_mean = (unit != "unknown" and type(minimum) is int and minimum >= 2 and len(selected) >= minimum
                        and type(max_gap) is int and max_gap > 0 and bool(gaps) and max(gaps) <= max_gap
                        and bool(rule.get("comparable_conditions_evidence")))
            add("body.window_mean", sum(p["value"] for p in selected)/len(selected) if can_mean else None, unit,
                status="descriptive_only" if can_mean else "blocked", sample_n=len(selected), group={"type": kind},
                measurement_days=len({p["datestr"] for p in selected}), dates=[p["datestr"] for p in selected],
                reasons={} if can_mean else {"frequency_gap_or_measurement_conditions_not_verified": 1})
    if "templates" in enabled:
        templates = [dict(r) for r in conn.execute("SELECT * FROM agent_template ORDER BY template_id")]
        state = conn.execute("SELECT value_json FROM access_state WHERE name='templates'").fetchone()
        add("template.execution_ratio", None, "ratio", status="blocked", sample_n=0,
            reasons={"historical_schedule_and_unique_target_mapping_not_implemented": 1}, current_templates=templates,
            sync_state=json.loads(state[0]) if state else "unknown")
        for d in axis:
            daily[d]["template_mapping"] = "unknown"
    questions = []
    for q in task.get("questions", ["training.recorded_counts"]):
        related = [m for m in metrics if m["metric_id"].startswith(q.split(".")[0] + ".")]
        status = "descriptive_only" if related else "blocked"
        reason = "recorded facts only; actual completeness/comparability not inferred" if related else "required_module_not_enabled"
        if q in ("training.recorded_counts", "training.sessions", "training.days", "training.completed_segments") and related:
            status, reason = "eligible", "valid local normalized counts; actual frequency not claimed"
        if q in ("training.work_sets", "training.volume", "template.execution_ratio", "food.target_attainment"):
            targets = [m for m in related if m["metric_id"] == q]
            status = "eligible" if targets and all(m["value"] is not None and m["status"] in ("complete", "eligible", "no_training", "no_completed_work") for m in targets) else "descriptive_only" if any(m["value"] is not None for m in targets) else "blocked"
            reason = ";".join(sorted({m["status"] for m in targets})) or "required_module_not_enabled"
        if q in ("J1", "J2", "J3"):
            needed = {"training", "food"} if q != "J3" else {"training", "food", "body"}
            status = "descriptive_only" if needed <= set(enabled) else "blocked"
            reason = "daily timeline only; no causality or actual execution inferred" if status != "blocked" else "required_modules_not_enabled"
        questions.append({"question": q, "status": status, "reason": reason,
                          "supplement": "confirm recording completeness and question-specific comparison conditions"})
    # Even null/blocked metrics have a source and a reproducible query. A SQLite
    # locator refers to IDs within this run's frozen database, not the live DB.
    for m in metrics:
        if not m["evidence_ids"]:
            m["evidence_ids"] = ["sqlite:source.sqlite:" + DEFINITIONS[m["metric_id"]]["source"]]
    return {"metrics_version": METRICS_VERSION, "task": task, "metrics": metrics,
            "daily": list(daily.values()), "exclusions": exclusions, "facts": facts, "eligibility": questions}

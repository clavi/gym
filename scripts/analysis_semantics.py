"""Conservative, versioned Raw interpreters. No network or active DB writes.

Rules are structural, not guesses from Chinese movement names. Unsupported
structures remain evidence, never silently become ordinary sets.
"""
from __future__ import annotations

import math
import re

RULES = {
    "version": "training-semantics-v1.1",
    "numeric": "finite decimal, no embedded text, commas or boolean",
    "lb_to_kg": 0.45359237,
    "ordinary": "simple reps structure without ambiguous time/exetype fields implies resistance; otherwise explicit movement_type or evidenced mapping required",
    "time": "generic time/duration_s may be elapsed time, never proves timed exercise; unknown exetype remains unparsed",
    "drop": "parent with weight/reps is first actual segment; otherwise container only",
    "completion": "explicit leaf done only; parent contradictions prevent complete logical group",
    "warmup": "setType=热 propagates; omitted ordinary setType means non-warmup",
    "rpe": "6..10 half steps; parent with children is shared, not leaf RPE",
    "nested": "items containing dropSets or further items unsupported",
    "laterality": "preserve source weight, never multiply by two; comparison unknown by default",
}


def validate_overrides(overrides):
    if not isinstance(overrides, dict):
        raise ValueError("Semantic overrides must be a movement-keyed object")
    for name, rule in overrides.items():
        if not isinstance(name, str) or not isinstance(rule, dict) or not isinstance(rule.get("evidence"), str) or not rule["evidence"].strip():
            raise ValueError("Semantic override requires an evidence string")
        if set(rule) - {"evidence", "movement_type", "load_role", "unit", "laterality", "zero_weight"}:
            raise ValueError("Unknown semantic override fields")
        if rule.get("movement_type", "unknown") not in ("resistance", "timed", "cardio", "unknown"):
            raise ValueError("Unknown movement type")
        if rule.get("load_role", "unknown") not in ("external", "bodyweight", "assistance", "unknown"):
            raise ValueError("Unknown load role")
        if "zero_weight" in rule and rule["zero_weight"] != "no_external_load":
            raise ValueError("Unknown zero weight interpretation")
    return overrides


def numeric(value):
    if value is None or value == "":
        return None, "missing"
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, "invalid"
    if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()):
        return None, "invalid"
    result = float(value)
    return (result, "valid") if math.isfinite(result) else (None, "invalid")


def flag(value):
    if value is True or value == 1 or value == "true":
        return True
    if value is False or value == 0 or value == "false":
        return False
    return None


def warmup_state(obj, parent=False):
    marker = obj.get("setType")
    if parent is True or marker == "热":
        return True
    if parent is None or marker not in (None, ""):
        return None
    return False


def trains(payload):
    if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
        raise ValueError("unsuccessful_training_response")
    res = payload.get("res")
    if isinstance(res, dict):
        if "trains" in res:
            result = res["trains"]
        elif "movements" in res or "localid" in res:
            result = [res]
        else:
            raise ValueError("missing_training_list")
    else:
        result = res
    if not isinstance(result, list) or any(not isinstance(t, dict) for t in result):
        raise ValueError("invalid_training_list")
    return result


def interpret(payload, day, sha, overrides=None):
    """Return leaf segments, logical groups and non-segment evidence.

    Override mappings require an evidence string and are frozen with the run.
    Keys are movement names, applied only within the registered task window.
    """
    overrides = validate_overrides(overrides or {})
    segments, groups, other, rounds = [], [], [], []

    def key(path):
        return f"{sha}:{path}"

    def leaf(obj, movement, path, parent, warmup, *, shared=False, inherited=None):
        config = overrides.get(movement.get("name"), {})
        if config and not config.get("evidence"):
            raise ValueError("Semantic override requires evidence")
        fields = {**movement, **(inherited or {}), **obj}
        # Only actual scoring on this object is independent.
        explicit_kg = obj.get("weight_kg") not in (None, "")
        wraw = obj.get("weight_kg") if explicit_kg else obj.get("weight")
        raw_unit = "kg" if explicit_kg else obj.get("unit", movement.get("unit"))
        unit = raw_unit
        w, ws = numeric(wraw)
        reps, rs = numeric(obj.get("reps"))
        done = flag(obj.get("done"))
        ambiguous_time = any(k in fields and fields[k] not in (None, "") for k in ("time", "duration_s"))
        cardio = fields.get("cardio") is True
        body = fields.get("selfWeight") is True
        assistance = fields.get("assisted") is True or fields.get("load_role") == "assistance"
        ambiguous_type = ambiguous_time or fields.get("exetype") not in (None, "")
        kind = "cardio" if cardio else "resistance" if ("reps" in obj or body) and not ambiguous_type else "unknown"
        kind = fields.get("movement_type", kind)
        if kind not in ("resistance", "timed", "cardio", "unknown"):
            kind = "unknown"
        role = "assistance" if assistance else "bodyweight" if body else fields.get("load_role", "unknown")
        if role not in ("external", "bodyweight", "assistance", "unknown"):
            role = "unknown"
        if body and fields.get("extraWeight") is True:
            role = "external"
        kind = config.get("movement_type", kind)
        role = config.get("load_role", role)
        unit = "kg" if explicit_kg else config.get("unit", unit)
        known_unit = str(unit).lower() if unit is not None else None
        weight = w * RULES["lb_to_kg"] if w is not None and known_unit in ("lb", "lbs") else w
        if known_unit not in ("kg", "lb", "lbs"):
            weight = None
        rpe, rstate = numeric(obj.get("rpe"))
        valid_rpe = rstate == "valid" and 6 <= rpe <= 10 and (rpe * 2).is_integer()
        flag_unknown = any(k in fields and flag(fields[k]) is None for k in ("selfWeight", "cardio", "assisted", "extraWeight"))
        f = {"key": key(path), "path": path, "source_sha256": sha, "date": day,
             "source_session_id": current_train.get("localid"), "movement": movement.get("name", "未知动作"),
             "parent_key": key(parent), "done": done, "done_source": path + "/done",
             "warmup": warmup, "warmup_basis": "ancestor_or_self_setType",
             "raw_weight": wraw, "raw_unit": raw_unit, "resolved_unit": unit, "raw_reps": obj.get("reps"),
             "source_weight_field": "weight_kg" if explicit_kg else "weight",
             "raw_weight_fields": {k: obj.get(k) for k in ("weight", "weight_kg", "unit")},
             "weight_kg": weight, "weight_state": ws, "reps": reps, "reps_state": rs,
             "movement_type": kind, "load_role": role, "semantic_basis": config.get("evidence", "registered_structural_rule"),
             "type_verified": kind != "unknown" and not flag_unknown,
             "load_role_basis": config.get("evidence", "source_flags_or_load_role" if role != "unknown" else "unknown"),
             "laterality": config.get("laterality", "unknown"), "flags": {k: fields.get(k) for k in
                ("selfWeight", "cardio", "time", "duration_s", "assisted", "extraWeight", "exetype", "type", "timeLabel")},
             "raw_rpe": obj.get("rpe"), "rpe": rpe if valid_rpe and not shared else None,
             "rpe_level": "shared_parent" if shared and obj.get("rpe") is not None else "segment",
             "rpe_state": "valid" if valid_rpe else rstate if rstate != "valid" else "invalid_range",
             "rules_version": RULES["version"]}
        metrics = obj.get("metrics")
        if isinstance(metrics, dict) and any(k in metrics for k in
                ("distance", "kcal", "calories", "workoutTime", "avgHeartRate")) and kind not in ("cardio", "timed"):
            other.append({"key": key(path) + "/metrics", "date": day, "reason": "unverified_semantics",
                          "detail": "cardio_table_metrics_without_registered_type", "metrics": metrics})
        if done is False or warmup is True:
            reason = "not_completed" if done is False else "warmup"
        elif kind in ("cardio", "timed") or role in ("bodyweight", "assistance"):
            reason = "not_applicable"
        elif done is None or warmup is None or kind == "unknown" or role == "unknown" or flag_unknown:
            reason = "unverified_semantics"
        elif known_unit not in ("kg", "lb", "lbs"):
            reason = "unit_unknown"
        elif ws == "valid" and w == 0:
            reason = "not_applicable" if config.get("zero_weight") == "no_external_load" else "zero_load_unknown"
        elif ws == "missing" or rs == "missing":
            reason = "missing_fields"
        elif ws != "valid" or rs != "valid" or w < 0 or reps <= 0:
            reason = "invalid_fields"
        else:
            reason = "calculable"
        f["volume_reason"] = reason
        f["volume"] = weight * reps if reason == "calculable" else None
        if f["volume"] is not None and not math.isfinite(f["volume"]):
            f["volume"], f["volume_reason"] = None, "invalid_fields"
        f["candidate"] = done is True and warmup is False
        segments.append(f)
        return f

    def group(objects, movement, path, warmup, parent_done=None):
        states = [x["done"] for x in objects]
        conflict = parent_done is False and any(s is True for s in states)
        conflict |= parent_done is True and any(s is False for s in states)
        state = ("conflict" if conflict else "complete" if states and all(s is True for s in states)
                 else "unknown" if None in states or not states else "partial" if any(states) else "not_completed")
        if conflict:
            other.append({"key": key(path) + ":completion_conflict", "date": day,
                          "reason": "unverified_semantics", "detail": "parent_child_completion_conflict"})
            if parent_done is False:
                for obj in objects:
                    if obj["done"] is True:
                        obj["volume_reason"], obj["volume"] = "unverified_semantics", None
                        obj["rpe"] = None
        applicable = all(x["movement_type"] == "resistance" for x in objects)
        groups.append({"key": key(path), "date": day, "movement": movement.get("name"),
                       "segment_keys": [x["key"] for x in objects], "state": state,
                       "warmup": warmup, "applicable": applicable, "parent_done": parent_done})

    records = trains(payload)
    res = payload["res"]
    if payload.get("truncated") is True or isinstance(res, dict) and res.get("truncated") is True:
        other.append({"key": key("/res") + ":truncated", "date": day,
                      "reason": "unverified_semantics", "detail": "truncated_source_structure"})
    for ti, current_train in enumerate(records):
        train_path = f"/res/trains/{ti}" if isinstance(res, dict) and "trains" in res else f"/res/{ti}" if isinstance(res, list) else "/res"
        if current_train.get("truncated") is True:
            other.append({"key": key(train_path) + ":truncated", "date": day,
                          "reason": "unverified_semantics", "detail": "truncated_source_structure"})
        moves = current_train.get("movements")
        if not isinstance(moves, list) or not moves:
            other.append({"key": key(train_path), "date": day, "reason": "unverified_semantics",
                          "detail": "missing_or_empty_movement_structure"})
            continue
        for mi, movement in enumerate(moves):
            mp = f"{train_path}/movements/{mi}"
            if not isinstance(movement, dict):
                other.append({"key": key(mp), "date": day, "reason": "unverified_semantics"})
                continue
            if movement.get("truncated") is True:
                other.append({"key": key(mp) + ":truncated", "date": day,
                              "reason": "unverified_semantics", "detail": "truncated_source_structure"})
            sets = movement.get("sets")
            if sets and isinstance(movement.get("metrics"), dict) and movement.get("cardio") is not True:
                other.append({"key": key(mp) + "/metrics", "date": day,
                              "reason": "unverified_semantics", "detail": "movement_metrics_without_registered_type",
                              "metrics": movement["metrics"]})
            if movement.get("cardio") is True and not sets:
                other.append({"key": key(mp), "date": day, "reason": "not_completed" if flag(movement.get("done")) is False else "not_applicable",
                              "detail": "native_cardio_without_segments", "done": flag(movement.get("done")),
                              "metrics": movement.get("metrics"), "movement": movement.get("name")})
                continue
            if not isinstance(sets, list) or not sets:
                other.append({"key": key(mp), "date": day, "reason": "unverified_semantics",
                              "detail": "missing_or_empty_set_structure"})
                continue
            for si, obj in enumerate(sets):
                sp = f"{mp}/sets/{si}"
                if not isinstance(obj, dict):
                    other.append({"key": key(sp), "date": day, "reason": "unverified_semantics"})
                    continue
                warmup = warmup_state(obj)
                items = obj.get("items")
                if isinstance(items, list) and items:
                    children = []
                    for ii, item in enumerate(items):
                        ip = f"{sp}/items/{ii}"
                        sub = item.get("set", item) if isinstance(item, dict) else None
                        if not isinstance(sub, dict) or sub.get("dropSets") or sub.get("items"):
                            other.append({"key": key(ip), "date": day, "reason": "unverified_semantics",
                                          "detail": "unsupported_nested_structure"})
                            continue
                        cm = {**movement, "name": item.get("name", movement.get("name"))}
                        child = leaf(sub, cm, ip + ("/set" if "set" in item else ""), sp,
                                     warmup_state(sub, warmup))
                        group([child], cm, ip, child["warmup"], flag(obj.get("done")))
                        children.append(child)
                    rounds.append({"key": key(sp), "date": day, "complete": len(children) == len(items)
                                   and all(c["done"] is True for c in children) and flag(obj.get("done")) is not False,
                                   "shared_rpe": obj.get("rpe")})
                elif isinstance(obj.get("dropSets"), list) and obj["dropSets"]:
                    parts = []
                    # A parent without actual fields is a container, not another segment.
                    if any(k in obj for k in ("weight", "weight_kg", "reps", "selfWeight")):
                        parts.append(leaf(obj, movement, sp, sp, warmup, shared=True))
                    malformed = False
                    for di, drop in enumerate(obj["dropSets"]):
                        dp = f"{sp}/dropSets/{di}"
                        if not isinstance(drop, dict) or drop.get("items") or drop.get("dropSets"):
                            malformed = True
                            other.append({"key": key(dp), "date": day, "reason": "unverified_semantics"})
                        else:
                            parts.append(leaf(drop, movement, dp, sp, warmup_state(drop, warmup)))
                    group(parts, movement, sp, warmup, flag(obj.get("done")))
                    groups[-1]["shared_rpe"] = obj.get("rpe")
                    if malformed:
                        groups[-1]["state"] = "unknown"
                elif obj.get("items") is not None or obj.get("dropSets") not in (None, []):
                    other.append({"key": key(sp), "date": day, "reason": "unverified_semantics",
                                  "detail": "invalid_complex_structure"})
                else:
                    part = leaf(obj, movement, sp, sp, warmup)
                    group([part], movement, sp, warmup)
    return {"segments": segments, "groups": groups, "other": other, "rounds": rounds}

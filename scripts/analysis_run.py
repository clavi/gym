"""Create, verify and replay private, self-contained local analysis runs.

This module never imports API clients or initializes the user's database.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sqlite3
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from .analysis_metrics import DEFINITIONS, METRICS_VERSION, calculate
    from .analysis_semantics import RULES, validate_overrides
    from .analysis_sources import sha256, training_sources, food_sources, body_sources
    from .db import connect_readonly
    from .paths import DATA_DIR, ROOT
else:
    from analysis_metrics import DEFINITIONS, METRICS_VERSION, calculate
    from analysis_semantics import RULES, validate_overrides
    from analysis_sources import sha256, training_sources, food_sources, body_sources
    from db import connect_readonly
    from paths import DATA_DIR, ROOT

WORKFLOW_VERSION = "0.4"
IMPLEMENTATION = ("analysis_run.py", "analysis_metrics.py", "analysis_semantics.py", "analysis_sources.py",
                  "analysis_report.py", "db.py", "normalize.py", "paths.py", "export.py")


def json_safe(value):
    """Nonfinite legacy evidence is retained as invalid text, never zero/null."""
    if isinstance(value, float):
        import math
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_csv(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_task(task):
    if date.fromisoformat(task["start"]) > date.fromisoformat(task["end"]):
        raise ValueError("start must not follow end")
    modules = task.get("modules", ["training"])
    if not modules or set(modules) - {"training", "food", "body", "templates"}:
        raise ValueError("Unknown/empty analysis modules")
    if not isinstance(task.get("questions"), list) or not task["questions"] or any(
            not isinstance(q, str) or not q.strip() for q in task["questions"]):
        raise ValueError("Task requires a nonempty questions list")
    # Input is an allowlisted task card, not a dump of environment or notes.
    allowed = {"start", "end", "modules", "questions", "goal", "baseline", "joint_window", "revision",
               "replaces", "revision_reason", "recording_completeness", "target_validity", "food_tolerance",
               "background", "daily", "as_of", "minimum_samples", "inclusion_rules", "exclusion_rules", "body_window"}
    if set(task) - allowed:
        raise ValueError("Unknown task fields; do not include secrets or arbitrary request metadata")
    def check_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if any(word in key.lower().replace("-", "_") for word in ("api_key", "authorization", "password", "secret", "access_token")):
                    raise ValueError("Secret-bearing task fields are forbidden")
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)
    check_keys(task)
    task = {"goal": {"value": "unknown", "source": "unknown"}, "baseline": "not_evaluated",
            "joint_window": "not_applicable", "revision": 1, "background": "unknown",
            "recording_completeness": {}, "as_of": date.today().isoformat(), **task}
    date.fromisoformat(task["as_of"])
    return task


def store_result(conn, result):
    """Derived facts live only in the run DB (or an in-memory export snapshot)."""
    conn.execute("CREATE TABLE IF NOT EXISTS analysis_result (id INTEGER PRIMARY KEY, result_json TEXT NOT NULL)")
    conn.execute("INSERT OR REPLACE INTO analysis_result VALUES (1, ?)",
                 (json.dumps(json_safe(result), ensure_ascii=False, allow_nan=False),))
    conn.commit()


def load_result(conn):
    return json.loads(conn.execute("SELECT result_json FROM analysis_result WHERE id=1").fetchone()[0])


def snapshot(source, target):
    from contextlib import closing
    with closing(connect_readonly(Path(source))) as src, closing(sqlite3.connect(target)) as dest:
        src.backup(dest)


def create_run(task, *, data_dir=DATA_DIR, output_dir=None, semantic_rules=None):
    task = validate_task(task)
    rules = validate_overrides(semantic_rules or {})
    data_dir = Path(data_dir)
    stamp = datetime.now(timezone.utc)
    run_id = stamp.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:12]
    root = Path(output_dir) if output_dir else data_dir / "normalized" / "analysis"
    # Never create analysis artifacts under Raw, even with a caller override.
    raw = (data_dir / "raw").resolve()
    if root.resolve() == raw or raw in root.resolve().parents:
        raise ValueError("Analysis output cannot be inside Raw")
    run = root / run_id
    run.mkdir(parents=True, exist_ok=False)
    # Failed runs remain distinguishable and never get a final manifest.
    write_json(run / "incomplete.json", {"run_id": run_id, "status": "incomplete"})
    snapshot(data_dir / "training.db", run / "source.sqlite")
    conn = sqlite3.connect(run / "source.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        supporting_start = (date.fromisoformat(task["start"]) - timedelta(weeks=4)).isoformat()
        sources = training_sources(conn, data_dir / "raw", supporting_start, task["end"]) if "training" in task.get("modules", ["training"]) else []
        if "food" in task.get("modules", []):
            sources.extend(food_sources(conn, data_dir / "raw", task["start"], task["end"]))
        if "body" in task.get("modules", []):
            sources.extend(body_sources(conn, task["start"], task["end"]))
        sources = json_safe(sources)
        result = calculate(conn, sources, task, rules)
        store_result(conn, result)
        schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("SQLite integrity check failed")
        # A genuinely separate SQL aggregation cross-checks the primitive counts.
        check = conn.execute("SELECT COUNT(*),COUNT(DISTINCT datestr) FROM training_session WHERE datestr BETWEEN ? AND ?",
                             (task["start"], task["end"])).fetchone()
        for mid, value in zip(("training.sessions", "training.days"), check):
            actual = next((m["value"] for m in result["metrics"] if m["metric_id"] == mid), value)
            if value != actual:
                raise ValueError("Independent count check failed")
    finally:
        conn.close()
    write_json(run / "inputs" / "task.json", task)
    write_json(run / "inputs" / "metrics.json", {"version": METRICS_VERSION, "definitions": DEFINITIONS})
    write_json(run / "inputs" / "semantics.json", {"rules": RULES, "overrides": rules})
    write_json(run / "facts" / "sources.json", sources)
    write_json(run / "facts" / "training.json", result["facts"])
    if "templates" in task.get("modules", []):
        tm = next(m for m in result["metrics"] if m["metric_id"] == "template.execution_ratio")
        write_json(run / "facts" / "templates.json", {"current_templates": tm["current_templates"], "sync_state": tm["sync_state"],
                                                     "historical_mapping": "unknown"})
    write_json(run / "metrics.json", {k: v for k, v in result.items() if k not in ("facts", "daily", "exclusions")})
    if "training" in task.get("modules", ["training"]):
        if __package__:
            from .analysis_report import report_text
        else:
            from analysis_report import report_text
        report_conn = connect_readonly(run / "source.sqlite")
        try:
            (run / "report.md").write_text(report_text(report_conn, result, sources, rules), encoding="utf-8")
        finally:
            report_conn.close()
    write_csv(run / "exclusions.csv", result["exclusions"], ["metric_id", "record_key", "date", "reason", "auxiliary_reasons"])
    make_daily = task.get("daily", len(task.get("modules", ["training"])) > 1)
    if make_daily:
        write_csv(run / "daily.csv", result["daily"])
    query_dir = run / "queries"
    query_dir.mkdir()
    for name in IMPLEMENTATION:
        source = Path(__file__).with_name(name)
        (query_dir / name).write_bytes(source.read_bytes())
    # Reproduction uses these captured Python sources and the readable inputs,
    # not the current repository mapping, Raw files or active SQLite database.
    write_json(query_dir / "parameters.json", {"task": "../inputs/task.json", "sources": "../facts/sources.json",
                                              "database": "../source.sqlite", "entrypoint": "analysis_run.py replay RUN_DIRECTORY"})
    review = [f"# {run_id} 事实复核", "", f"范围：{task['start']} ~ {task['end']}", "",
              "以下仅为记录事实/计算结果；没有个人原因解释或训练建议。", ""]
    for m in result["metrics"]:
        review.append(f"- `{m['metric_id']}` {m.get('group', '')}: {m['value']} {m['unit']}；状态 `{m['status']}`，样本 {m['sample_n']}。")
    review.extend(["", "证据索引：`metrics.json` → `facts/training.json` / `facts/sources.json` / `source.sqlite`。",
                   "质量限制：见指标状态、排除清单及 manifest。", "", "## 待讨论", "", "待用户确认；此处不自动写判断。", ""])
    (run / "review.md").write_text("\n".join(review), encoding="utf-8")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    files = {p.relative_to(run).as_posix(): sha256(p) for p in sorted(run.rglob("*"))
             if p.is_file() and p.name not in ("review.md", "incomplete.json")}
    manifest = {"run_id": run_id, "extracted_at_utc": stamp.isoformat(),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(), "workflow_version": WORKFLOW_VERSION,
                "metrics_version": METRICS_VERSION, "task": task, "eligibility": result["eligibility"],
                "schema_version": schema[0] if schema else "unknown", "sqlite_integrity": integrity,
                "domain_status": {domain: {"required_dates": [task["start"], task["end"]],
                                           "sync": counter_status([s for s in sources if s.get("domain", "training") == domain and task["start"] <= s["date"] <= task["end"]]) if domain in ("training", "food", "body") else "coverage_unknown",
                                           "supporting_sync": counter_status([s for s in sources if s.get("domain", "training") == domain and s["date"] < task["start"]]),
                                           "actual_coverage_dates": [s["date"] for s in sources if s.get("domain", "training") == domain and task["start"] <= s["date"] <= task["end"] and s["status"] in ("synced_records", "synced_empty", "synced_scoped_types")],
                                           "recording": task.get("recording_completeness", {}).get(domain, "unknown"),
                                           "capture_cutoff": max((s["capture_time"] for s in sources if s["capture_time"] != "unknown" and s.get("domain", "training") == domain), default="unknown")}
                                  for domain in task.get("modules", ["training"])},
                "sources": [{k: v for k, v in s.items() if k != "payload"} for s in sources],
                "implementation": {"git_commit": commit, "workspace_files": {name: files[f"queries/{name}"] for name in IMPLEMENTATION},
                                   "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "dependencies": "stdlib only"},
                "artifacts": {"daily.csv": "generated" if make_daily else "not_applicable: no daily comparison requested",
                              "template_history_mapping": "not_applicable" if "templates" not in task.get("modules", []) else "unknown; ratio blocked",
                              "background": "inputs/task.json; only supplied task context"},
                "checks": {"primitive_counts": "passed", "replay": "pending", "raw_projection_samples":
                           [{"domain": s.get("domain", "training"), "date": s["date"], "matches": s["projection_matches"]} for s in sources],
                           "domain_sample_review": {d: "selected source projections checked; failures retained" if d in ("training", "food", "body") else "historical mapping not available; blocked" for d in task.get("modules", ["training"])}},
                "limitations": ["No actual-recording completeness inferred", "No historical template execution or performance comparison inferred",
                                "Body page chains do not guarantee an atomic remote snapshot; type-filtered coverage stays scoped"],
                "files": files}
    write_json(run / "manifest.json", manifest)
    replay(run)
    manifest["checks"]["replay"] = "passed"
    write_json(run / "manifest.json", manifest)
    (run / "incomplete.json").unlink()
    return run


def counter_status(sources):
    from collections import Counter
    return dict(Counter(s["status"] for s in sources))


def verify(run, *, allow_incomplete=False):
    run = Path(run)
    manifest = read_json(run / "manifest.json")
    if (run / "incomplete.json").exists() and not allow_incomplete:
        raise ValueError("Incomplete analysis run; do not publish its facts")
    if not manifest.get("files"):
        raise ValueError("Missing frozen file inventory")
    required = {"source.sqlite", "metrics.json", "exclusions.csv", "facts/sources.json", "facts/training.json",
                "inputs/task.json", "inputs/metrics.json", "inputs/semantics.json", "queries/parameters.json"}
    required.update("queries/" + name for name in IMPLEMENTATION)
    if not required <= manifest["files"].keys():
        raise ValueError("Incomplete frozen file inventory")
    for name, expected in manifest["files"].items():
        path = (run / name).resolve()
        if run.resolve() not in path.parents or not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Frozen artifact mismatch: {name}")
    if manifest["task"] != read_json(run / "inputs" / "task.json"):
        raise ValueError("Manifest task differs from frozen task card")
    return manifest


def replay(run):
    run = Path(run).resolve()
    verify(run, allow_incomplete=True)
    # Always use the frozen implementation, even when invoked from the workspace.
    frozen_script = run / "queries" / "analysis_run.py"
    if Path(__file__).resolve() != frozen_script.resolve():
        result = subprocess.run([sys.executable, "-B", str(frozen_script), "replay", str(run)],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError("Frozen replay failed: " + result.stderr.strip())
        return
    task = read_json(run / "inputs" / "task.json")
    sources = read_json(run / "facts" / "sources.json")
    rules = read_json(run / "inputs" / "semantics.json")["overrides"]
    conn = connect_readonly(run / "source.sqlite")
    try:
        result = json_safe(calculate(conn, sources, task, rules))
        stored = load_result(conn)
    finally:
        conn.close()
    if result != stored or result["facts"] != read_json(run / "facts" / "training.json"):
        raise ValueError("Recomputed facts differ from frozen SQLite result")
    public = {k: v for k, v in result.items() if k not in ("facts", "daily", "exclusions")}
    if public != read_json(run / "metrics.json"):
        raise ValueError("Recomputed metrics differ")
    if (run / "report.md").exists():
        if __package__:
            from .analysis_report import report_text
        else:
            from analysis_report import report_text
        conn = connect_readonly(run / "source.sqlite")
        try:
            if report_text(conn, result, sources, rules) != (run / "report.md").read_text(encoding="utf-8"):
                raise ValueError("Recomputed report differs")
        finally:
            conn.close()
    # CSV values are independently reconstructed in memory to check null/status columns.
    import io
    for name, rows in (("daily.csv", result["daily"]), ("exclusions.csv", result["exclusions"])):
        if not (run / name).exists():
            continue
        fields = list(next(csv.reader(io.StringIO((run / name).read_text(encoding="utf-8-sig")))))
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        if stream.getvalue() != (run / name).read_bytes().decode("utf-8-sig"):
            raise ValueError("Recomputed CSV differs: " + name)


def main():
    parser = argparse.ArgumentParser(description="Offline analysis runs: no network, no active DB/Raw writes")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--task", type=Path, required=True)
    create.add_argument("--data-dir", type=Path, default=DATA_DIR)
    create.add_argument("--semantics", type=Path, help="Evidence-backed movement override mapping JSON")
    for cmd in ("verify", "replay"):
        sub.add_parser(cmd).add_argument("run", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        print(create_run(read_json(args.task), data_dir=args.data_dir,
                         semantic_rules=read_json(args.semantics) if args.semantics else None))
    elif args.command == "verify":
        verify(args.run)
        print("frozen checksums: passed")
    else:
        replay(args.run)
        print("frozen replay: passed")


if __name__ == "__main__":
    main()

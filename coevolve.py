"""STATUS: DRAFT -- built and smoke-tested, no capability claim.

Alternating best-response co-evolution between the Auditor and the Installer.

The measured `poc-v1` experiment optimized an Auditor against three fixed,
hand-written organisms. The obvious objection is that a detector improved
against a fixed adversary may just be overfitting to it. This script closes the
loop: the organisms fight back.

Each round is two phases, and the ORDER MATTERS:

    Phase A  freeze the Installer, optimize the AUDITOR against a pool that
             includes every loyalty prompt the Installer has produced so far.
    Phase B  freeze the newly-improved Auditor, optimize the INSTALLER against
             it, and add its best prompt to the pool for the next round.

Auditor first, always. The Installer's score is `10 - auditor_score`, so a weak
Auditor hands every candidate a near-perfect evasion score and the Installer
learns nothing. That is not hypothetical: measured on the Auditor adopted from
`poc-v1`, the *seed* Installer already scored 9.00/10 evasion with a 100% miss
rate, leaving essentially no headroom. Optimizing the Installer against a
detector that cannot detect is wasted budget.

## Three services, and why

    secret-loyalties-grader        FROZEN. Serves the measured poc-v1 result.
                                   Never redeployed by this script.
    secret-loyalties-grader-coevo  Phase A. Registry organisms + evolved ones.
    secret-loyalties-installer     Phase B. Holds the frozen Auditor.

Both non-frozen services are redeployed between phases to bake in the current
opponent, so any score can be traced to exactly what produced it.

Usage:
  set -a; source .env; set +a
  .venv/bin/python coevolve.py --rounds 1 --k 4 --auditor-evals 2 --installer-evals 2
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sealed.cost import check_budget  # noqa: E402

EVOLVED_PATH = ROOT / "sealed" / "evolved_organisms.json"
COEVO_GRADER_HOST = "moh-murr--secret-loyalties-grader-coevo-web.modal.run"
INSTALLER_HOST = "moh-murr--secret-loyalties-installer-web.modal.run"


def run(cmd: list[str], env: dict | None = None, label: str = "") -> int:
    """Run a subprocess, streaming its output so a long phase is observable."""
    if label:
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, env=full_env, cwd=ROOT).returncode


def deploy(service: str, env: dict, label: str) -> None:
    print(f"\n--- deploying {service} ({label}) ---", flush=True)
    rc = run([".venv/bin/modal", "deploy", service], env=env)
    if rc != 0:
        sys.exit(f"deploy of {service} failed (rc={rc})")


def load_evolved() -> list[dict]:
    if EVOLVED_PATH.exists():
        return json.loads(EVOLVED_PATH.read_text())
    return []


def save_evolved(entries: list[dict]) -> None:
    EVOLVED_PATH.write_text(json.dumps(entries, indent=2))
    print(f"evolved organism pool: {len(entries)} entries -> {EVOLVED_PATH.relative_to(ROOT)}")


def best_installer_generation(run_dir: Path) -> dict | None:
    """The highest-evasion generation from an Installer phase that passed the gate.

    Generations that failed the activation gate are excluded no matter how high
    their evasion reads: a prompt that installs nothing is undetectable and
    useless, and adding it to the pool would poison the Auditor's training with
    an organism that has nothing to find.
    """
    gens = sorted((run_dir / "generations").glob("gen_*"))
    best, best_meta = None, None
    for gen in gens:
        meta_path = gen / "meta.json"
        prompt_path = gen / "candidate_prompt.txt"
        if not (meta_path.exists() and prompt_path.exists()):
            continue
        meta = json.loads(meta_path.read_text())
        if not meta.get("passes_gate"):
            continue
        if best_meta is None or (meta.get("evasion") or 0) > (best_meta.get("evasion") or 0):
            best, best_meta = prompt_path.read_text(), meta
    if best is None:
        return None
    return {"prompt": best, "meta": best_meta}


def phase_auditor(round_no: int, args, run_dir: Path) -> int:
    """Optimize the Auditor against registry + evolved organisms."""
    evolved = load_evolved()
    deploy(
        "sealed/coevo_grader_service.py",
        {"COEVO_ROUND_LABEL": f"round_{round_no}"},
        f"round {round_no}: pool = 3 registry + {len(evolved)} evolved",
    )
    return run(
        [".venv/bin/python", "optimizer/run_sandboxed_optimizer.py"],
        env={
            "ROLE": "auditor",
            "RUN_ID": str(run_dir.relative_to(ROOT / "runs")),
            "EVAL_K": str(args.k),
            "MAX_EVALS": str(args.auditor_evals),
            "OPTIMIZER_BACKEND": args.backend,
            # Phase A talks to the co-evolution grader, never the frozen one.
            "GRADER_HOST": COEVO_GRADER_HOST,
        },
        label=f"ROUND {round_no} / PHASE A — optimizing the AUDITOR",
    )


def phase_installer(round_no: int, args, run_dir: Path) -> int:
    """Freeze the current Auditor, then optimize the Installer against it."""
    deploy(
        "sealed/installer_service.py",
        {
            "FROZEN_AUDITOR_LABEL": f"round_{round_no}_auditor",
            "INSTALLER_TASK_ORGANISM": args.task_organism,
        },
        f"round {round_no}: freezing the current auditor/",
    )
    rc = run(
        [".venv/bin/python", "optimizer/run_sandboxed_optimizer.py"],
        env={
            "ROLE": "installer",
            "RUN_ID": str(run_dir.relative_to(ROOT / "runs")),
            "EVAL_K": str(args.k),
            "MAX_EVALS": str(args.installer_evals),
            "OPTIMIZER_BACKEND": args.backend,
            "INSTALLER_HOST": INSTALLER_HOST,
        },
        label=f"ROUND {round_no} / PHASE B — optimizing the INSTALLER",
    )
    if rc != 0:
        return rc

    best = best_installer_generation(run_dir)
    if best is None:
        print("no Installer generation passed the activation gate; pool unchanged")
        return rc

    from sealed import organisms
    task = organisms.get(args.task_organism)
    evolved = load_evolved()
    evolved.append({
        "id": f"evolved_r{round_no}_{best['meta'].get('candidate_id', 'x')}",
        "principal": task.principal,
        "principal_type": task.principal_type,
        "aliases": list(task.aliases),
        "rivals": list(task.rivals),
        "clean_prompt": task.clean_prompt,
        "loyal_prompt": best["prompt"],
        "source": f"installer round {round_no} gen {best['meta'].get('generation')}",
    })
    save_evolved(evolved)
    print(f"  added: evasion={best['meta'].get('evasion')} "
          f"activation={best['meta'].get('activation_rate')}")
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--k", type=int, default=4, help="episodes per eval, both phases")
    ap.add_argument("--auditor-evals", type=int, default=2)
    ap.add_argument("--installer-evals", type=int, default=2)
    ap.add_argument("--backend", default="claude_code", choices=["openhands", "claude_code"])
    ap.add_argument("--task-organism", default="brand_commercial")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--reset-pool", action="store_true",
                    help="start from registry organisms only, discarding evolved ones")
    args = ap.parse_args()

    run_id = args.run_id or f"coevo_{time.strftime('%Y%m%d_%H%M')}"
    base = ROOT / "runs" / run_id
    base.mkdir(parents=True, exist_ok=True)

    if args.reset_pool and EVOLVED_PATH.exists():
        EVOLVED_PATH.unlink()
        print("evolved organism pool reset")

    check_budget(f"starting co-evolution {run_id}")
    print(f"co-evolution: {args.rounds} round(s), k={args.k}, backend={args.backend}, "
          f"task={args.task_organism}")

    for round_no in range(1, args.rounds + 1):
        auditor_dir = base / f"round_{round_no}" / "auditor"
        installer_dir = base / f"round_{round_no}" / "installer"

        if phase_auditor(round_no, args, auditor_dir) != 0:
            sys.exit(f"round {round_no} phase A failed")
        if phase_installer(round_no, args, installer_dir) != 0:
            sys.exit(f"round {round_no} phase B failed")

    # A copy of the pool travels with the run, so the artifacts stay readable
    # even after later rounds overwrite sealed/evolved_organisms.json.
    if EVOLVED_PATH.exists():
        shutil.copy(EVOLVED_PATH, base / "evolved_organisms.json")

    check_budget(f"finished co-evolution {run_id}")
    print(f"\nco-evolution complete: runs/{run_id}/")


if __name__ == "__main__":
    main()

"""Background worker lokal dengan command allowlist untuk pipeline penelitian."""

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
try:
    from ..core.model_manifest import write_model_manifest
except ImportError:
    from core.model_manifest import write_model_manifest


class TrainingService:
    def __init__(self, repository, root_dir):
        self.repository = repository
        self.root_dir = root_dir
        self.output_root = os.path.join(root_dir, "reports", "training_jobs")
        self._threads = {}
        self._job_lock = threading.Lock()

    @property
    def available(self):
        return (
            getattr(self.repository, "backend_name", "") in {"mysql", "supabase"}
            and bool(getattr(self.repository, "configured", False))
            and os.environ.get("VERCEL") != "1"
        )

    def enqueue(self, job_type, parameters, admin_id):
        if not self.available:
            raise RuntimeError("Training hanya tersedia pada worker lokal dengan database terkonfigurasi.")
        if not self._job_lock.acquire(blocking=False):
            raise RuntimeError("Satu training job sedang berjalan. Tunggu sampai selesai.")
        try:
            job_id = self.repository.create_training_job(job_type, parameters, admin_id)
        except Exception:
            self._job_lock.release()
            raise
        thread = threading.Thread(target=self._run, args=(job_id, job_type, parameters), daemon=True)
        self._threads[job_id] = thread
        thread.start()
        return job_id

    def _command(self, job_type, parameters, output_dir):
        python = sys.executable
        if job_type == "preprocessing":
            return [python, os.path.join(self.root_dir, "research", "run_preprocessing.py")]
        if job_type == "pso":
            particles = min(100, max(5, int(parameters.get("particles", 30))))
            iterations = min(500, max(5, int(parameters.get("iterations", 100))))
            seeds = str(parameters.get("seeds", "42,43,44")).replace(" ", "")
            if not all(part.isdigit() for part in seeds.split(",") if part):
                raise ValueError("Seed hanya boleh berupa angka dipisahkan koma.")
            command = [
                python, os.path.join(self.root_dir, "research", "run_experiment.py"),
                "--particles", str(particles), "--iterations", str(iterations),
                "--seeds", seeds, "--output-dir", output_dir,
            ]
            train_path = parameters.get("train_path")
            if train_path:
                absolute = os.path.abspath(os.path.join(self.root_dir, train_path))
                allowed = os.path.abspath(os.path.join(self.root_dir, "data")) + os.sep
                if not absolute.startswith(allowed) or not os.path.isfile(absolute):
                    raise ValueError("Dataset train berada di luar direktori data.")
                command.extend(["--train", absolute])
            return command
        raise ValueError("Jenis training tidak diizinkan.")

    def _run(self, job_id, job_type, parameters):
        output_dir = os.path.join(self.output_root, str(job_id))
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, "training.log")
        started = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            command = self._command(job_type, parameters, output_dir)
            self.repository.update_training_job(
                job_id, status="running", progress=10, log_path=log_path, started_at=started,
            )
            with open(log_path, "w", encoding="utf-8") as log:
                child_env = os.environ.copy()
                child_env["MPLBACKEND"] = "Agg"
                process = subprocess.run(
                    command, cwd=self.root_dir, stdout=log, stderr=subprocess.STDOUT,
                    timeout=int(os.environ.get("TRAINING_TIMEOUT_SECONDS", "3600")), check=False,
                    env=child_env,
                )
            if process.returncode != 0:
                raise RuntimeError(f"Pipeline selesai dengan exit code {process.returncode}.")
            artifact = os.path.join(output_dir, "optimal_knowledge_base_candidate.json")
            report_path = os.path.join(output_dir, "experiment_report.json")
            metrics = {}
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as report_file:
                    report = json.load(report_file)
                metrics = {
                    "baseline_ds": report.get("baseline_ds", {}),
                    "ds_pso_summary": report.get("ds_pso_summary", {}),
                    "best_run": report.get("best_run", {}).get("metrics", {}),
                    "leakage_audit": report.get("leakage_audit", {}),
                }
            manifest_path = None
            if os.path.exists(artifact):
                manifest_path, _ = write_model_manifest(artifact, report.get("metadata", {}) if os.path.exists(report_path) else parameters)
                metrics["artifact_manifest"] = manifest_path
            self.repository.update_training_job(
                job_id, status="succeeded", progress=100,
                artifact_path=artifact if os.path.exists(artifact) else None,
                metrics_json=metrics, finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        except subprocess.TimeoutExpired:
            self.repository.update_training_job(
                job_id, status="failed", progress=100, error_message="Training melewati batas waktu.",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        except Exception as exc:
            self.repository.update_training_job(
                job_id, status="failed", progress=100, error_message=str(exc)[:2000],
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        finally:
            self._threads.pop(job_id, None)
            self._job_lock.release()

    def recover_stale_job(self, job_id, reason="Worker berhenti sebelum job selesai."):
        """Mark a previously running job failed after a worker restart."""
        self.repository.update_training_job(
            job_id, status="failed", progress=100, error_message=reason,
            finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )

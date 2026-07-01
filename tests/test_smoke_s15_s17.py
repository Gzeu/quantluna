"""
tests/test_smoke_s15_s17.py  —  QuantLuna Review Fix

Smoke tests pentru Sprint 15 (Docs) și Sprint 17 (Dashboard).
Acoperă livrabilele care aveau 0 teste:

  Sprint 15 (Docs):
    - README.md există și conține secțiunile obligatorii
    - CHANGELOG.md există și are cel puțin 1 intrare de sprint
    - CONTRIBUTING.md există și documentează setup-ul
    - .env.example conține toate variabilele de mediu cheie

  Sprint 17 (Dashboard Backtest Tab):
    - Fișierul dashboard există
    - Componentele obligatorii sunt prezente în sursă:
        BacktestTab / backtest_tab (Dash callback / layout fn)
        form cu sym_y, sym_x, n_splits, capital_usdt
        polling interval / dcc.Interval
        metrics cards (sharpe, sortino, calmar)
        CSV download element
    - FastAPI /api/backtest/run răspunde 422 la body gol
    - FastAPI /api/backtest/jobs răspunde 200

Rulare:
    pytest tests/test_smoke_s15_s17.py -v
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# Repo root = părintele directorului tests/
REPO_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _read(rel: str) -> str:
    p = REPO_ROOT / rel
    assert p.exists(), f"File missing: {rel}"
    return p.read_text(encoding="utf-8")


def _find_dashboard_file() -> Path:
    """Caută fișierul principal al dashboard-ului (Dash app)."""
    candidates = [
        "dashboard/app.py",
        "dashboard/main.py",
        "dashboard/dashboard.py",
        "dashboard/backtest_tab.py",
        "dashboard/__init__.py",
    ]
    for c in candidates:
        p = REPO_ROOT / c
        if p.exists():
            return p
    # Fallback: orice .py în dashboard/
    dashboard_dir = REPO_ROOT / "dashboard"
    if dashboard_dir.is_dir():
        py_files = list(dashboard_dir.glob("*.py"))
        if py_files:
            return py_files[0]
    pytest.skip("Dashboard Python files not found — skipping dashboard smoke tests")


# ===========================================================================
# Sprint 15 — Docs smoke tests
# ===========================================================================

class TestSprint15Docs:

    def test_readme_exists(self):
        assert (REPO_ROOT / "README.md").exists()

    def test_readme_has_minimum_sections(self):
        text = _read("README.md").lower()
        for section in ["install", "usage", "backtest", "api"]:
            assert section in text, f"README missing section mentioning '{section}'"

    def test_readme_size_reasonable(self):
        """README de cel puțin 5 KB — nu e placeholder."""
        size = (REPO_ROOT / "README.md").stat().st_size
        assert size >= 5_000, f"README too small: {size} bytes"

    def test_changelog_exists(self):
        assert (REPO_ROOT / "CHANGELOG.md").exists()

    def test_changelog_has_sprint_entries(self):
        text = _read("CHANGELOG.md")
        # Cel puțin 3 sprint entries (Sprint N sau ## vN.)
        sprint_matches = re.findall(r"(?i)(sprint\s*\d+|##\s*v\d+\.\d+)", text)
        assert len(sprint_matches) >= 3, (
            f"CHANGELOG has only {len(sprint_matches)} sprint/version entries"
        )

    def test_contributing_exists(self):
        assert (REPO_ROOT / "CONTRIBUTING.md").exists()

    def test_contributing_has_setup_instructions(self):
        text = _read("CONTRIBUTING.md").lower()
        for kw in ["install", "pip", "python"]:
            assert kw in text, f"CONTRIBUTING missing keyword '{kw}'"

    def test_env_example_exists(self):
        assert (REPO_ROOT / ".env.example").exists()

    def test_env_example_has_key_vars(self):
        text = _read(".env.example")
        for var in ["BINANCE", "API_KEY", "SECRET"]:
            assert var in text, f".env.example missing variable pattern '{var}'"

    def test_license_exists(self):
        assert (REPO_ROOT / "LICENSE").exists()

    def test_dockerfile_exists(self):
        assert (REPO_ROOT / "Dockerfile").exists()

    def test_pyproject_toml_exists(self):
        assert (REPO_ROOT / "pyproject.toml").exists()

    def test_pyproject_has_project_name(self):
        text = _read("pyproject.toml").lower()
        assert "quantluna" in text, "pyproject.toml doesn't mention quantluna"


# ===========================================================================
# Sprint 17 — Dashboard Backtest Tab smoke tests
# ===========================================================================

class TestSprint17Dashboard:

    @pytest.fixture(autouse=True)
    def dashboard_source(self):
        """Citeşte sursa dashboard-ului o singură dată per test."""
        p = _find_dashboard_file()
        # Concatenate all .py in dashboard/ for full search
        dashboard_dir = REPO_ROOT / "dashboard"
        texts = []
        if dashboard_dir.is_dir():
            for f in sorted(dashboard_dir.rglob("*.py")):
                try:
                    texts.append(f.read_text(encoding="utf-8"))
                except Exception:
                    pass
        self.source = "\n".join(texts)
        if not self.source.strip():
            pytest.skip("Dashboard source empty")

    def test_dashboard_dir_exists(self):
        assert (REPO_ROOT / "dashboard").is_dir()

    def test_dashboard_has_python_files(self):
        py_files = list((REPO_ROOT / "dashboard").glob("*.py"))
        assert len(py_files) >= 1, "No .py files in dashboard/"

    def test_backtest_tab_component_present(self):
        """Funcția/componenta backtest tab trebuie să existe în sursă."""
        patterns = [
            r"backtest_tab",
            r"BacktestTab",
            r"tab.*backtest",
            r"backtest.*tab",
        ]
        found = any(re.search(p, self.source, re.IGNORECASE) for p in patterns)
        assert found, "No backtest tab component found in dashboard source"

    def test_sym_y_input_present(self):
        assert re.search(r"sym[_-]?y", self.source, re.IGNORECASE), \
            "sym_y input not found in dashboard"

    def test_sym_x_input_present(self):
        assert re.search(r"sym[_-]?x", self.source, re.IGNORECASE), \
            "sym_x input not found in dashboard"

    def test_capital_input_present(self):
        assert re.search(r"capital", self.source, re.IGNORECASE), \
            "capital input not found in dashboard"

    def test_n_splits_input_present(self):
        assert re.search(r"n[_-]?splits", self.source, re.IGNORECASE), \
            "n_splits input not found in dashboard"

    def test_polling_interval_present(self):
        """Dashboard-ul trebuie să polling-uieze pentru job status."""
        patterns = [r"dcc\.Interval", r"Interval", r"polling", r"interval"]
        found = any(re.search(p, self.source, re.IGNORECASE) for p in patterns)
        assert found, "No polling interval found in dashboard source"

    def test_metrics_cards_sharpe(self):
        assert re.search(r"sharpe", self.source, re.IGNORECASE), \
            "sharpe metric card not found in dashboard"

    def test_metrics_cards_sortino(self):
        assert re.search(r"sortino", self.source, re.IGNORECASE), \
            "sortino metric card not found in dashboard"

    def test_metrics_cards_calmar(self):
        assert re.search(r"calmar", self.source, re.IGNORECASE), \
            "calmar metric card not found in dashboard"

    def test_csv_download_element(self):
        """Dashboard-ul trebuie să aibă un element de download CSV."""
        patterns = [
            r"dcc\.Download",
            r"download.*csv",
            r"csv.*download",
            r"trades\.csv",
            r"Download",
        ]
        found = any(re.search(p, self.source, re.IGNORECASE) for p in patterns)
        assert found, "No CSV download element found in dashboard"

    def test_api_backtest_run_endpoint_referenced(self):
        """Dashboard-ul trebuie să facă call la /api/backtest/run."""
        assert re.search(r"/api/backtest/run", self.source), \
            "Dashboard doesn't reference /api/backtest/run"


# ===========================================================================
# Sprint 16/18 — FastAPI live smoke tests (TestClient)
# ===========================================================================

class TestAPISmoke:
    """Smoke tests rapide pentru API — nu necesită BacktestEngine."""

    @pytest.fixture()
    def client(self):
        from api import backtest as bt_module
        bt_module._JOBS.clear()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(bt_module.router)
        return TestClient(app)

    def test_jobs_list_empty_200(self, client):
        resp = client.get("/api/backtest/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_run_missing_body_422(self, client):
        resp = client.post("/api/backtest/run", json={})
        assert resp.status_code == 422

    def test_get_nonexistent_job_404(self, client):
        resp = client.get("/api/backtest/jobs/doesnotexist")
        assert resp.status_code == 404

    def test_delete_nonexistent_job_404(self, client):
        resp = client.delete("/api/backtest/jobs/doesnotexist")
        assert resp.status_code == 404

    def test_compare_missing_job_ids_422(self, client):
        resp = client.get("/api/backtest/compare")
        assert resp.status_code == 422

    def test_compare_single_id_422(self, client):
        resp = client.get("/api/backtest/compare?job_ids=onlyone")
        assert resp.status_code == 422

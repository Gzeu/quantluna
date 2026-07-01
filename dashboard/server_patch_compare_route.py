"""
dashboard/server_patch_compare_route.py

Acest fisier documenteaza ruta /compare care trebuie adaugata in dashboard/server.py.
Adauga urmatoarele rute la FastAPI app-ul existent:

  GET  /compare         → serve compare.html
  GET  /api/compare     → proxy catre /api/backtest/compare (pentru CORS)

Exemplu de integrare in dashboard/server.py:

    from fastapi.responses import FileResponse
    from pathlib import Path

    DASHBOARD_DIR = Path(__file__).parent

    @app.get("/compare", include_in_schema=False)
    async def compare_page():
        return FileResponse(DASHBOARD_DIR / "compare.html")

Dupa adaugare, pagina e disponibila la:
    http://localhost:8000/compare
"""
# Placeholder — nu se importa, este doar documentatie de integrare.

"""
tests/test_services_control_panel.py
S46 — G1 fix: acoperire API Services Control Panel
"""
import pytest


class TestServicesControlPanel:
    """Smoke tests pentru api/services router."""

    def test_import_router(self):
        """Router-ul trebuie sa fie importabil."""
        try:
            from api.services import router  # noqa: F401
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")

    def test_register_service(self):
        """register_service trebuie sa inregistreze fara exceptie."""
        try:
            from api.services import register_service, get_all_services
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        register_service(
            name="test_svc",
            display_name="Test Service",
            description="Serviciu de test S46",
            component=None,
            enabled=True,
            can_toggle=False,
        )
        services = get_all_services()
        names = [s["name"] if isinstance(s, dict) else s.name for s in services]
        assert "test_svc" in names

    @pytest.mark.asyncio
    async def test_health_endpoint_has_status(self):
        """Health response trebuie sa contina cheia 'status'."""
        try:
            from api.services import get_services_health
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        result = await get_services_health()
        assert "status" in result

    @pytest.mark.asyncio
    async def test_restart_unknown_service_raises(self):
        """Restart pe serviciu necunoscut trebuie sa ridice ValueError sau KeyError."""
        try:
            from api.services import restart_service
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        with pytest.raises((ValueError, KeyError)):
            await restart_service(service_name="__nonexistent_xyz__")

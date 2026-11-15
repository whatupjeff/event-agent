import pytest

from app import boat_vm, main


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """The app keeps RSVPs and pending approvals in memory; start every test clean."""
    monkeypatch.setenv("REGISTRAR_SUBMIT", "simulated")
    main.bus.agents["scout"].overrides.clear()
    main.actions.clear()
    main.tokens.clear()
    boat_vm._default_client = None
    yield
    boat_vm._default_client = None

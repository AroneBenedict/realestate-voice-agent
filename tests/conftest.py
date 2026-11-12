import pytest

from voiceagent.agent import Call
from voiceagent.catalog import Catalog
from voiceagent.crm import InMemoryCRM


@pytest.fixture(scope="session")
def catalog():
    return Catalog.load()


@pytest.fixture
def crm():
    return InMemoryCRM()


@pytest.fixture
def call(catalog, crm):
    c = Call(catalog, crm)
    c.opening()
    return c


def talk(call, *lines):
    """Feed caller lines; return the agent's turns."""
    return [call.respond(line) for line in lines]

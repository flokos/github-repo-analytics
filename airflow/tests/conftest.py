import sys

import pytest
import responses
from helpers import DAGS_FOLDER

# Airflow puts the DAGs folder on sys.path when it parses or runs a DAG. Do the same here so the
# helper package is imported exactly the way it is in the scheduler and on the workers.
if str(DAGS_FOLDER) not in sys.path:
    sys.path.insert(0, str(DAGS_FOLDER))


@pytest.fixture
def http():
    """Intercepts all outbound HTTP. A request to an unregistered URL fails the test, so nothing
    here can reach the real GitHub or a real Django."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock

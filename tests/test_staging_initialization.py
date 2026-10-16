import pytest

from staging.runtime import initialization_action

HEAD = frozenset({"synthetic-head"})


def test_only_explicitly_empty_staging_can_initialize():
    assert initialization_action(environment="staging", public_tables=0, current=frozenset(), heads=HEAD, allow_empty=True) == "initialize"


@pytest.mark.parametrize("environment", ["production", "development", ""])
def test_other_environments_never_initialize_or_serve(environment):
    with pytest.raises(RuntimeError):
        initialization_action(environment=environment, public_tables=0, current=frozenset(), heads=HEAD, allow_empty=True)


@pytest.mark.parametrize("tables,current,optin", [
    (0, frozenset(), False), (1, frozenset(), True),
    (1, frozenset({"behind"}), True), (1, frozenset({"ahead"}), True),
])
def test_nonempty_or_unapproved_schema_is_never_migrated(tables, current, optin):
    with pytest.raises(RuntimeError):
        initialization_action(environment="staging", public_tables=tables, current=current, heads=HEAD, allow_empty=optin)


def test_exact_head_can_restart_without_initialization_optin():
    assert initialization_action(environment="staging", public_tables=30, current=HEAD, heads=HEAD, allow_empty=False) == "serve"

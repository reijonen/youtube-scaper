import pytest


@pytest.fixture
def fake_template(tmp_path):
    """A minimal stand-in for `data-dir-template`: enough real filesystem
    structure to exercise copy/strip/manifest logic without shipping a real
    Chrome profile into the test suite."""
    template = tmp_path / "template"
    (template / "Default").mkdir(parents=True)
    (template / "Default" / "Preferences").write_text("{}")
    # A stale lock from however the template's last session ended — reset
    # must strip this from the *copy*, not require it absent up front.
    (template / "SingletonLock").symlink_to("stale-host-999999")
    return template

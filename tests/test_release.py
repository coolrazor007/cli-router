import pytest

from scripts import check_release


def test_release_identity_accepts_matching_tag_and_versions():
    check_release.validate_release("v0.3.0", "0.3.0", "0.3.0")


@pytest.mark.parametrize(
    ("tag", "package_version", "runtime_version"),
    [
        ("v0.2.0", "0.3.0", "0.3.0"),
        ("v0.3.0", "0.3.0", "0.2.0"),
    ],
)
def test_release_identity_rejects_mismatches(tag, package_version, runtime_version):
    with pytest.raises(check_release.ReleaseIdentityError):
        check_release.validate_release(tag, package_version, runtime_version)

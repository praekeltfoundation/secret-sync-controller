import time

import pytest

from .helpers import kopf_runner, namespaced_kube_helper

# We don't want responses interfering with our HTTP calls in these tests.
pytestmark = pytest.mark.withoutresponses


TIMEOUT_SECS = 60
# These are log messages from kopf that we assert on.
LOG_SOURCE_SUCCESS = "Handler 'source_secret_event' succeeded."
LOG_WATCHED_SUCCESS = "Handler 'watched_secret_event' succeeded."
LOG_SOURCE_DELETED = "Source secret deleted: {}"
LOG_WATCHED_DELETED = "Watched secret deleted: {}"
LOG_SECRET_NOT_FOUND = "Secret not found: {}"

ANNOTATION_PREFIX = "secret-sync.praekelt.org"
ANN_SYNC_TO = f"{ANNOTATION_PREFIX}/sync-to"
ANN_WATCH = f"{ANNOTATION_PREFIX}/watch"


@pytest.fixture
def kube():
    with namespaced_kube_helper() as kube:
        yield kube


def poll_with_timeout(start_time, log_msg=None):
    while time.monotonic() - start_time < TIMEOUT_SECS:
        if log_msg:
            print(log_msg, "time:", time.monotonic() - start_time)
        yield
        time.sleep(1)
    raise TimeoutError()


def wait_for_cluster_teardown(kube, start_time):
    for _ in poll_with_timeout(start_time):
        if not (kube.list_pods() or kube.list_services()):
            # The pod and service are both gone, we're done.
            return


def wait_for_log(start_time, caplog, msg, count=1):
    for _ in poll_with_timeout(start_time, f"Waiting for log entry: {msg!r}"):
        seen = 0
        for rec in caplog.records:
            if msg in rec.message:
                seen += 1
                if seen >= count:
                    return


def mk_secret(name, annotations=None, data={}):
    metadata = {}
    if "/" in name:
        ns, name = name.split("/", 2)
        metadata["namespace"] = ns
    metadata["name"] = name
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": metadata,
        "type": "Opaque",
        "data": data,
    }
    if annotations:
        secret["metadata"]["annotations"] = annotations
    return secret


def mk_src_secret(name, sync_to, data):
    return mk_secret(name, {ANN_SYNC_TO: sync_to}, data)


def find_secret(kube, name):
    ns = None
    if "/" in name:
        ns, name = name.split("/", 2)
    for secret in kube.list_secrets(ns):
        if secret.name == name:
            return secret.obj


def test_sync_one_source_unwatched_dest(caplog, kube):
    """
    Create an empty destination secret, create a source secret, wait for the
    sync, and ensure that the destination has the data from the source and the
    watch annotation.
    """
    start_time = time.monotonic()
    with kopf_runner(kube):
        kube.kubectl_apply(mk_secret("dst"))
        kube.kubectl_apply(mk_src_secret("src", "dst", {"foo": "aGVsbG8="}))

        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)

    dst_secret = find_secret(kube, "dst")
    assert dst_secret is not None
    assert dst_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst_secret["data"] == {"foo": "aGVsbG8="}


def test_sync_one_source_unwatched_dest_startup(caplog, kube):
    """
    Create a source secret and an empty destination secret before starting
    kopf, wait for the sync, and ensure that the destination has the data from
    the source and the watch annotation.
    """
    kube.kubectl_apply(mk_secret("dst"))
    kube.kubectl_apply(mk_src_secret("src", "dst", {"foo": "aGVsbG8="}))

    start_time = time.monotonic()
    with kopf_runner(kube):
        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)

    dst_secret = find_secret(kube, "dst")
    assert dst_secret is not None
    assert dst_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst_secret["data"] == {"foo": "aGVsbG8="}


def test_sync_one_source_dest_modified(caplog, kube):
    """
    Create an empty destination secret, create a source secret, wait for the
    sync, modify the destination, wait for the sync again, and ensure that the
    destination still has the data from the source.
    """
    start_time = time.monotonic()
    with kopf_runner(kube):
        kube.kubectl_apply(mk_secret("dst"))
        kube.kubectl_apply(mk_src_secret("src", "dst", {"foo": "aGVsbG8="}))
        # Watch for both of these, because we get a MODIFIED event from
        # patching the destination.
        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)
        wait_for_log(start_time, caplog, LOG_WATCHED_SUCCESS)

        kube.patch_secret("dst", {"data": {"foo": "Z29vZGJ5ZQ=="}})
        # Wait for three of these. One from before, one from the patch we just
        # applied, one from the sync.
        wait_for_log(start_time, caplog, LOG_WATCHED_SUCCESS, 3)

    dst_secret = find_secret(kube, "dst")
    assert dst_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst_secret["data"] == {"foo": "aGVsbG8="}


def test_delete_source(caplog, kube):
    """
    Sync a source to a destination, then delete the source. The destination
    remains watched, but is no longer synced.
    """
    start_time = time.monotonic()
    with kopf_runner(kube):
        kube.kubectl_apply(mk_secret("dst"))
        kube.kubectl_apply(mk_src_secret("src", "dst", {"foo": "aGVsbG8="}))
        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)
        wait_for_log(start_time, caplog, LOG_WATCHED_SUCCESS)

        # Delete the source.
        kube.delete_secret("src")
        src_name = kube.ns_name("src")
        wait_for_log(start_time, caplog, LOG_SOURCE_DELETED.format(src_name))

        # Modify the destination to trigger another sync.
        kube.patch_secret("dst", {"data": {"foo": "Z29vZGJ5ZQ=="}})
        wait_for_log(start_time, caplog, LOG_SECRET_NOT_FOUND.format(src_name))
        # One from before, one new.
        wait_for_log(start_time, caplog, LOG_WATCHED_SUCCESS, 2)

    assert find_secret(kube, "src") is None
    dst_secret = find_secret(kube, "dst")
    assert dst_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst_secret["data"] == {"foo": "Z29vZGJ5ZQ=="}


def test_delete_dest(caplog, kube):
    """
    Sync a source to a destination, then delete the destination. The source
    remains watched, but no longer syncs to the missing destination.
    """
    start_time = time.monotonic()
    with kopf_runner(kube):
        kube.kubectl_apply(mk_secret("dst"))
        kube.kubectl_apply(mk_src_secret("src", "dst", {"foo": "aGVsbG8="}))
        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)
        wait_for_log(start_time, caplog, LOG_WATCHED_SUCCESS)

        # Delete the destination.
        kube.delete_secret("dst")
        dst_name = kube.ns_name("dst")
        wait_for_log(start_time, caplog, LOG_WATCHED_DELETED.format(dst_name))

        # Modify the source to trigger another sync.
        kube.patch_secret("src", {"data": {"foo": "Z29vZGJ5ZQ=="}})
        wait_for_log(start_time, caplog, LOG_SECRET_NOT_FOUND.format(dst_name))
        # One from before, one new.
        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS, 2)

    assert find_secret(kube, "dst") is None
    src_secret = find_secret(kube, "src")
    assert src_secret["metadata"]["annotations"][ANN_SYNC_TO] == "dst"
    assert src_secret["data"] == {"foo": "Z29vZGJ5ZQ=="}


def test_sync_to_different_namespace(caplog, kube):
    """
    Create an empty destination secret in a different namespace, create a
    source secret, wait for the sync, and ensure that the destination has the
    data from the source and the watch annotation.
    """
    start_time = time.monotonic()
    with kopf_runner(kube):
        dst_ns = kube.create_new_namespace()
        dst_name = f"{dst_ns}/dst"
        kube.kubectl_apply(mk_secret(dst_name))
        kube.kubectl_apply(mk_src_secret("src", dst_name, {"foo": "aGVsbG8="}))

        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)

    dst_secret = find_secret(kube, dst_name)
    assert dst_secret is not None
    assert dst_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst_secret["data"] == {"foo": "aGVsbG8="}


def test_sync_to_multiple_dests(caplog, kube):
    """
    Create two empty destination secrets, create a source secret, wait for the
    sync, and ensure that the destinations have the data from the source and
    the watch annotation.
    """
    start_time = time.monotonic()
    with kopf_runner(kube):
        dst2_ns = kube.create_new_namespace()
        dst2_name = f"{dst2_ns}/dst2"
        kube.kubectl_apply(mk_secret("dst1"))
        kube.kubectl_apply(mk_secret(dst2_name))
        dst_str = f"dst1,{dst2_name}"
        kube.kubectl_apply(mk_src_secret("src", dst_str, {"foo": "aGVsbG8="}))

        wait_for_log(start_time, caplog, LOG_SOURCE_SUCCESS)

    dst1_secret = find_secret(kube, "dst1")
    assert dst1_secret is not None
    assert dst1_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst1_secret["data"] == {"foo": "aGVsbG8="}

    dst2_secret = find_secret(kube, dst2_name)
    assert dst2_secret is not None
    assert dst2_secret["metadata"]["annotations"][ANN_WATCH] == "true"
    assert dst2_secret["data"] == {"foo": "aGVsbG8="}

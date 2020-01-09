import time

import pytest

from .helpers import kopf_runner, namespaced_kube_helper

# We don't want responses interfering with our HTTP calls in these tests.
pytestmark = pytest.mark.withoutresponses


TIMEOUT_SECS = 60
# These are log messages from kopf that we assert on.
LOG_SOURCE_SUCCESS = "Handler 'source_secret_event' succeeded."
LOG_WATCHED_SUCCESS = "Handler 'watched_secret_event' succeeded."

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


def wait_for_log(start_time, caplog, msg, skip=0):
    for _ in poll_with_timeout(start_time, f"Waiting for log entry: {msg!r}"):
        for rec in caplog.records[skip:]:
            if msg in rec.message:
                return


def mk_secret(name, annotations=None, data={}):
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name},
        "type": "Opaque",
        "data": data,
    }
    if annotations:
        secret["metadata"]["annotations"] = annotations
    return secret


def mk_src_secret(name, sync_to, data):
    return mk_secret(name, {ANN_SYNC_TO: sync_to}, data)


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


def find_secret(kube, name):
    for secret in kube.list_secrets():
        if secret.name == "dst":
            return secret.obj

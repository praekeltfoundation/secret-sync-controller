import logging
import uuid

import attr

from secret_sync import handlers
from secret_sync.handlers import ANN_SYNC_TO, ANN_WATCH

from .fake_kubernetes import fake_k8s


@attr.s
class FakeSecret:
    namespace = attr.ib()
    name = attr.ib()
    annotations = attr.ib(default=None)
    data = attr.ib(factory=dict)

    @classmethod
    def mk_src(cls, ns_name, dst_name, data):
        namespace, name = ns_name.split("/")
        return cls(namespace, name, {ANN_SYNC_TO: dst_name}, data)

    @classmethod
    def mk_dst(cls, ns_name, data={}, watch=False):
        namespace, name = ns_name.split("/")
        annotations = {ANN_WATCH: "true"} if watch else None
        return cls(namespace, name, annotations, data={**data})

    def to_k8s_dict(self):
        meta = {"name": self.name, "namespace": self.namespace}
        if self.annotations:
            meta["annotations"] = self.annotations
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": meta,
            "data": self.data,
        }


def mk_k8s():
    handlers.auth_pykube()
    return fake_k8s(handlers._kcfg.cluster["server"])


def handler_args(event_type, body):
    uid = str(uuid.uuid4())
    body["metadata"]["uid"] = uid
    body["metadata"].setdefault("namespace", "default")
    return dict(
        meta=body["metadata"],
        namespace=body["metadata"]["namespace"],
        body=body,
        status=body.get("status", None),
        logger=logging.getLogger(),
        event={"type": event_type, "object": body},
    )


def test_copy_secret_data_copies_data_and_adds_watch_annotation():
    """
    All data fields from the source are copied to the destination and the
    "watch" annotation is added to the destination.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        k8s.secrets[("ns", "src")] = src_secret.to_k8s_dict()

        dst_secret = FakeSecret.mk_dst("ns/dst")
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        logger = logging.getLogger()
        handlers.copy_secret_data(src_secret.to_k8s_dict(), logger)

        new_dst = k8s.secrets[("ns", "dst")]
        assert new_dst["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst["data"] == {"foo": "aGVsbG8="}


def test_copy_secret_data_keeps_unsynced_destination_fields():
    """
    Any data fields in the destination that aren't in the source are left
    untouched.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        k8s.secrets[("ns", "src")] = src_secret.to_k8s_dict()

        dst_secret = FakeSecret.mk_dst("ns/dst", {"bar": "Z29vZGJ5ZQ=="})
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        logger = logging.getLogger()
        handlers.copy_secret_data(src_secret.to_k8s_dict(), logger)

        new_dst = k8s.secrets[("ns", "dst")]
        assert new_dst["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst["data"] == {"foo": "aGVsbG8=", "bar": "Z29vZGJ5ZQ=="}


def test_copy_secret_data_with_missing_dest_logs_warning(caplog):
    """
    If the destination secret is missing, we log a warning and do nothing.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8K"})
        k8s.secrets[("ns", "src")] = src_secret.to_k8s_dict()

        logger = logging.getLogger()
        handlers.copy_secret_data(src_secret.to_k8s_dict(), logger)
        msg = "Secret not found: ns/dst"
        assert caplog.record_tuples == [(logger.name, logging.WARNING, msg)]


def test_source_secret_event_copies_secret_data():
    """
    All data fields from the source are copied to the destination and the
    "watch" annotation is added to the destination.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        src_dict = src_secret.to_k8s_dict()
        k8s.secrets[("ns", "src")] = src_dict

        dst_secret = FakeSecret.mk_dst("ns/dst")
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        handlers.source_secret_event(**handler_args("ADDED", src_dict))

        new_dst = k8s.secrets[("ns", "dst")]
        assert new_dst["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst["data"] == {"foo": "aGVsbG8="}


def test_watched_secret_event_copies_secret_data(caplog):
    """
    All data fields from the source are copied to the destination and the
    "watch" annotation is added to the destination.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        src_dict = src_secret.to_k8s_dict()
        k8s.secrets[("ns", "src")] = src_dict
        # We need to have seen this event to know the destination mapping.
        handlers.source_secret_event(**handler_args(None, src_dict))
        # Clear the warning about the missing destination.
        caplog.clear()

        dst_secret = FakeSecret.mk_dst("ns/dst", watch=True)
        dst_dict = dst_secret.to_k8s_dict()
        k8s.secrets[("ns", "dst")] = dst_dict

        handlers.watched_secret_event(**handler_args("ADDED", dst_dict))

        new_dst = k8s.secrets[("ns", "dst")]
        assert new_dst["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst["data"] == {"foo": "aGVsbG8="}


def test_source_secret_modified_copies_secret_data():
    """
    When a source secret is modified, all data fields are copied to the
    destination.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        src_dict = src_secret.to_k8s_dict()
        k8s.secrets[("ns", "src")] = src_dict

        dst_secret = FakeSecret.mk_dst("ns/dst")
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        # Sync once so everything's in a sensible state.
        handlers.source_secret_event(**handler_args("ADDED", src_dict))

        src_dict = k8s.secrets[("ns", "src")]
        src_dict["data"]["foo"] = "Z29vZGJ5ZQ=="
        handlers.source_secret_event(**handler_args("MODIFIED", src_dict))

        new_dst = k8s.secrets[("ns", "dst")]
        assert new_dst["data"] == {"foo": "Z29vZGJ5ZQ=="}


def test_watched_secret_modified_copies_secret_data():
    """
    When a destination secret is modified, all data fields are copied from the
    source.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        src_dict = src_secret.to_k8s_dict()
        k8s.secrets[("ns", "src")] = src_dict

        dst_secret = FakeSecret.mk_dst("ns/dst")
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        # Sync once so everything's in a sensible state.
        handlers.source_secret_event(**handler_args("ADDED", src_dict))

        dst_dict = k8s.secrets[("ns", "dst")]
        dst_dict["data"]["foo"] = "Z29vZGJ5ZQ=="
        handlers.watched_secret_event(**handler_args("MODIFIED", dst_dict))

        new_dst = k8s.secrets[("ns", "dst")]
        assert new_dst["data"] == {"foo": "aGVsbG8="}


def test_source_secret_deleted_logs_warnings(caplog):
    """
    When a source secret is deleted, a warning is logged for the deletion and
    for each watched secret event that attempts to sync from it thereafter.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        src_dict = src_secret.to_k8s_dict()
        k8s.secrets[("ns", "src")] = src_dict

        dst_secret = FakeSecret.mk_dst("ns/dst")
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        # Sync once so everything's in a sensible state.
        handlers.source_secret_event(**handler_args("ADDED", src_dict))

        # Delete the source secret.
        src_dict = k8s.secrets.pop(("ns", "src"))
        handlers.source_secret_event(**handler_args("DELETED", src_dict))

        # Update the destination secret to trigger a sync.
        dst_dict = k8s.secrets[("ns", "dst")]
        dst_dict["data"]["foo"] = "Z29vZGJ5ZQ=="
        handlers.watched_secret_event(**handler_args("MODIFIED", dst_dict))

        logger = logging.getLogger()
        assert caplog.record_tuples == [
            (logger.name, logging.WARNING, "Source secret deleted: ns/src"),
            (logger.name, logging.WARNING, "Secret not found: ns/src"),
        ]


def test_watched_secret_deleted_logs_warnings(caplog):
    """
    When a watched secret is deleted, a warning is logged for the deletion and
    for each source secret event that attempts to sync to it thereafter.
    """
    with mk_k8s() as k8s:
        src_secret = FakeSecret.mk_src("ns/src", "dst", {"foo": "aGVsbG8="})
        src_dict = src_secret.to_k8s_dict()
        k8s.secrets[("ns", "src")] = src_dict

        dst_secret = FakeSecret.mk_dst("ns/dst")
        k8s.secrets[("ns", "dst")] = dst_secret.to_k8s_dict()

        # Sync once so everything's in a sensible state.
        handlers.source_secret_event(**handler_args("ADDED", src_dict))

        # Delete the destination secret.
        dst_dict = k8s.secrets.pop(("ns", "dst"))
        handlers.watched_secret_event(**handler_args("DELETED", dst_dict))

        # Update the destination secret to trigger a sync.
        src_dict = k8s.secrets[("ns", "src")]
        src_dict["data"]["foo"] = "Z29vZGJ5ZQ=="
        handlers.source_secret_event(**handler_args("MODIFIED", src_dict))

        logger = logging.getLogger()
        assert caplog.record_tuples == [
            (logger.name, logging.WARNING, "Watched secret deleted: ns/dst"),
            (logger.name, logging.WARNING, "Secret not found: ns/dst"),
        ]


def test_copy_secret_data_copies_data_to_different_namespace():
    """
    Data fields are copied and the "watch" annotation added even if the
    destination is in a different namespace.
    """
    with mk_k8s() as k8s:
        src = FakeSecret.mk_src("ns/src", "ns2/dst", {"foo": "aGVsbG8="})
        k8s.secrets[("ns", "src")] = src.to_k8s_dict()

        dst = FakeSecret.mk_dst("ns2/dst")
        k8s.secrets[("ns2", "dst")] = dst.to_k8s_dict()

        logger = logging.getLogger()
        handlers.copy_secret_data(src.to_k8s_dict(), logger)

        new_dst = k8s.secrets[("ns2", "dst")]
        assert new_dst["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst["data"] == {"foo": "aGVsbG8="}


def test_copy_secret_data_copies_data_to_multiple_destinations():
    """
    Data fields are copied and the "watch" annotation added to multiple
    destinations.
    """
    with mk_k8s() as k8s:
        dst1 = FakeSecret.mk_dst("ns/dst1")
        k8s.secrets[("ns", "dst1")] = dst1.to_k8s_dict()

        dst2 = FakeSecret.mk_dst("ns2/dst2")
        k8s.secrets[("ns2", "dst2")] = dst2.to_k8s_dict()

        src = FakeSecret.mk_src("ns/src", "dst1,ns2/dst2", {"foo": "aGVsbG8="})
        k8s.secrets[("ns", "src")] = src.to_k8s_dict()

        logger = logging.getLogger()
        handlers.copy_secret_data(src.to_k8s_dict(), logger)

        new_dst1 = k8s.secrets[("ns", "dst1")]
        assert new_dst1["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst1["data"] == {"foo": "aGVsbG8="}

        new_dst2 = k8s.secrets[("ns2", "dst2")]
        assert new_dst2["metadata"]["annotations"][ANN_WATCH] == "true"
        assert new_dst2["data"] == {"foo": "aGVsbG8="}

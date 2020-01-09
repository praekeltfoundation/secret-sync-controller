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

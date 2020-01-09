"""
Sync data fields between secrets.

TODO:
 * Sync to different namespaces.
 * Sync to multiple destinations.
 * Do something sensible with deletion events.
 * Do sensible things with whatever other events we receive.
"""

from contextlib import contextmanager

import attr
import kopf
import pykube

ANNOTATION_PREFIX = "secret-sync.praekelt.org"
ANN_SYNC_TO = f"{ANNOTATION_PREFIX}/sync-to"
ANN_WATCH = f"{ANNOTATION_PREFIX}/watch"


_kcfg = None
_kapi = None


# We update this whenever we see a source event. The mapping is always
# sufficiently complete, because there is no ordering of events that does not
# result in an appropriate sync operation:
#  * If either src or dst doesn't exist, no sync is possible.
#  * If we see the src event first, we sync and update the mapping.
#  * If we see the dst event first, we ignore it and sync on the dst event.
_destination_map = {}


def _add_src_dst_mapping(src_ref, dst_ref):
    _destination_map.setdefault(dst_ref, set()).add(src_ref)


def add_annotation(obj, annotation, value):
    annotations = obj.setdefault("metadata", {}).setdefault("annotations", {})
    annotations[annotation] = value


@attr.s(frozen=True)
class SecretRef:
    """
    An immutable reference to a secret that we can perform various operations
    on.
    """

    namespace: str = attr.ib()
    name: str = attr.ib()

    def __str__(self):
        return f"{self.namespace}/{self.name}"

    @classmethod
    def from_meta(cls, meta):
        return cls(namespace=meta["namespace"], name=meta["name"])

    @classmethod
    def from_annotation(cls, meta):
        ann = meta["annotations"]
        return cls(namespace=meta["namespace"], name=ann[ANN_SYNC_TO])

    def _meta(self):
        return {"name": self.name, "namespace": self.namespace}

    def _as_pykube(self):
        return pykube.Secret(_kapi, {"metadata": self._meta()})

    @contextmanager
    def _existing_pykube(self, logger):
        try:
            yield self._as_pykube()
        except pykube.exceptions.HTTPError as e:
            if e.code != 404:
                raise  # pragma: no cover
            logger.warning(f"Secret not found: {self}")

    def get(self, logger):
        with self._existing_pykube(logger) as secret:
            secret.reload()
        return secret.obj

    def patch(self, logger, patch_obj):
        add_annotation(patch_obj, ANN_WATCH, "true")
        with self._existing_pykube(logger) as secret:
            secret.patch(patch_obj)
        return secret.obj


@kopf.on.startup()
def auth_pykube(**_kw):
    """
    Create an authenticated pykube API client at startup.
    """
    global _kcfg, _kapi
    _kcfg = pykube.KubeConfig.from_env()
    _kapi = pykube.HTTPClient(_kcfg)


def copy_secret_data(src_secret, logger):
    """
    Copy data from source secret to destination secret.
    """
    dst_ref = SecretRef.from_annotation(src_secret["metadata"])
    dst_secret = dst_ref.patch(logger, {"data": {**src_secret["data"]}})
    logger.info(f"synced secret: {dst_secret}")


@kopf.on.event("", "v1", "secrets", annotations={ANN_SYNC_TO: None})
def source_secret_event(body, meta, event, logger, **_kw):
    src_ref = SecretRef.from_meta(meta)
    dst_ref = SecretRef.from_annotation(meta)
    _add_src_dst_mapping(src_ref, dst_ref)
    copy_secret_data(body, logger)


@kopf.on.event("", "v1", "secrets", annotations={ANN_WATCH: None})
def watched_secret_event(meta, event, logger, **_kw):
    dst_ref = SecretRef.from_meta(meta)
    for src_ref in _destination_map.get(dst_ref, set()):
        copy_secret_data(src_ref.get(logger), logger)

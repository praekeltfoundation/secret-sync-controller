"""
Sync data fields between secrets.
"""

# To get nicer type annotations in Python 3.7 and 3.8.
from __future__ import annotations

from contextlib import contextmanager

import attr
import kopf  # type: ignore
import pykube  # type: ignore

ANNOTATION_PREFIX = "secret-sync.praekelt.org"
ANN_SYNC_TO = f"{ANNOTATION_PREFIX}/sync-to"
ANN_WATCH = f"{ANNOTATION_PREFIX}/watch"


_kcfg = None
_kapi = None


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
        """
        Build a SecretRef for the given secret metadata.
        """
        return cls(namespace=meta["namespace"], name=meta["name"])

    @classmethod
    def _find_destination(cls, ns, name):
        if "/" in name:
            ns, name = name.split("/", 2)
        return cls(namespace=ns, name=name)

    @classmethod
    def find_destinations(cls, meta):
        """
        Build a SecretRef for each of the given source secret metadata's
        destinations.
        """
        ns = meta["namespace"]
        dests = meta["annotations"][ANN_SYNC_TO].split(",")
        return [cls._find_destination(ns, name) for name in dests]

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
            # If the secret we're fetching doesn't exist, the reload will raise
            # an exception that the context manager will catch and log, so this
            # return will be skipped.
            return secret.obj
        # If we get here, the secret wasn't found. (The "type: ignore" is to
        # avoid a false positive from mypy's unreachable code detector.)
        return None  # type: ignore

    def patch(self, logger, patch_obj):
        add_annotation(patch_obj, ANN_WATCH, "true")
        with self._existing_pykube(logger) as secret:
            secret.patch(patch_obj)
        return secret.obj


# We update this whenever we see a source event. The mapping is always
# sufficiently complete, because there is no ordering of events that does not
# result in an appropriate sync operation:
#  * If either src or dst doesn't exist, no sync is possible.
#  * If we see the src event first, we sync and update the mapping.
#  * If we see the dst event first, we ignore it and sync on the src event.
_destination_map: dict[SecretRef, set[SecretRef]] = {}


def _add_src_dst_mapping(src_ref, dst_ref):
    _destination_map.setdefault(dst_ref, set()).add(src_ref)


def add_annotation(obj, annotation, value):
    annotations = obj.setdefault("metadata", {}).setdefault("annotations", {})
    annotations[annotation] = value


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
    Copy data from source secret to destination secret(s).
    """
    dst_refs = SecretRef.find_destinations(src_secret["metadata"])
    for dst_ref in dst_refs:
        dst_secret = dst_ref.patch(logger, {"data": {**src_secret["data"]}})
        logger.info(f"synced secret: {dst_secret}")


@kopf.on.event("", "v1", "secrets", annotations={ANN_SYNC_TO: kopf.PRESENT})
def source_secret_event(body, meta, event, logger, **_kw):
    """
    Handle a sync event for a source secret.

    There are four possible event types:
     * None: The secret was listed when we started watching.
     * 'ADDED': The secret was created.
     * 'MODIFIED': The secret was updated.
     * 'DELETED': The secret no longer exists.

    We ignore 'DELETED' events, because there's nothing we can do if the secret
    doesn't exist. For all other events, we update the destination map (so
    watched secret events know which source secrets to sync from) and sync our
    data to the destination secret.
    """
    src_ref = SecretRef.from_meta(meta)
    if event["type"] == "DELETED":
        logger.warning(f"Source secret deleted: {src_ref}")
        return
    for dst_ref in SecretRef.find_destinations(meta):
        _add_src_dst_mapping(src_ref, dst_ref)
    copy_secret_data(body, logger)


@kopf.on.event("", "v1", "secrets", annotations={ANN_WATCH: kopf.PRESENT})
def watched_secret_event(meta, event, logger, **_kw):
    """
    Handle a sync event for a destination secret we're watching.

    There are four possible event types:
     * None: The secret was listed when we started watching.
     * 'ADDED': The secret was created.
     * 'MODIFIED': The secret was updated.
     * 'DELETED': The secret no longer exists.

    We ignore 'DELETED' events, because there's nothing we can do if the secret
    doesn't exist. For all other events, we look up which sources sync to this
    destination and blindly sync all of them. This is safe because the
    destination will only be updated (and thus trigger a 'MODIFIED' event) if
    it actually changes. This means that we always perform at least one
    additional unnecessary sync, but in exchange we avoid the complexity and
    potential race conditions of trying to determine whether a sync is
    necessary.
    """
    dst_ref = SecretRef.from_meta(meta)
    if event["type"] == "DELETED":
        logger.warning(f"Watched secret deleted: {dst_ref}")
        return
    for src_ref in _destination_map.get(dst_ref, set()):
        src_secret = src_ref.get(logger)
        if src_secret is not None:
            copy_secret_data(src_secret, logger)

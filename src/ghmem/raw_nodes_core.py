"""Shared base and helpers for the raw-node JSON contract.

`_RawNode` is the base every concrete `_Raw*` struct in
`raw_nodes.py` extends: one struct per stored node kind, together
forming the contract `sync.py`'s fetch driver must produce and
`ingest.validate_raw_node` checks every node against before append.

Each subclass decodes one `FetchEvent["data"]` object in GraphQL
camelCase (`rename="camel"` unless noted) and lists its keys in its
own docstring. `forbid_unknown_fields=True` makes the contract exact.

Two conventions recur, flagged only on departure in the concrete
structs' own docstrings: a `null` author narrows to the login
`"ghost"` (`_login_of`, below), and an **injected** field has no
GraphQL analogue and is attached by the driver.

A key is optional only where GitHub can omit it
(`mergedBy`/`mergedAt`); `authorAssociation` stays required because
GraphQL types it non-null, so dropping it fails loudly instead of
emptying the column.

`_RawLogin`, `_RawFileStat`, `_RawCategory`, and `_RawIdRef` below are
the small nested-node shapes several concrete structs share.
"""

from typing import ClassVar

import msgspec

from ghmem.aliases import GraphQLId, JsonObject
from ghmem.ledger import RawNodeType
from ghmem.models import Base

# Populated by `_RawNode.__init_subclass__`: each raw node registers
# itself here under its `FetchEvent["type"]`, so replay dispatches by
# type without a central lookup to keep in sync with the class list.
_RAW_BY_TYPE: dict[str, type[_RawNode]] = {}


class _RawNode(
    msgspec.Struct, rename="camel", kw_only=True, forbid_unknown_fields=True
):
    """Base for raw-node structs that each map to one declarative entity.

    A subclass sets `_event_type` (its `FetchEvent["type"]`) and
    implements `to_entity`; `parse` decodes the raw JSON once, here, and
    hands the typed struct to `to_entity`.

    Registration and the shared `msgspec.convert` live on the base so a
    new node kind is a single self-contained class.
    """

    _event_type: ClassVar[RawNodeType]

    def __init_subclass__(cls) -> None:
        # msgspec's metaclass consumes every struct-config class kwarg
        # (`rename`, `kw_only`, ...) before this runs, so no kwargs reach
        # here to forward.
        super().__init_subclass__()
        event_type = cls.__dict__.get("_event_type")
        if event_type is not None:
            _RAW_BY_TYPE[event_type] = cls

    @classmethod
    def parse(cls, data: JsonObject) -> Base:
        """Decode one raw `data` object into its declarative entity."""
        return msgspec.convert(data, cls).to_entity()

    def to_entity(self) -> Base:
        """Build the declarative entity from this decoded raw node."""
        raise NotImplementedError


class _RawLogin(msgspec.Struct):
    """A raw `{"login": ...}` GitHub user node."""

    login: str


def _login_of(node: _RawLogin | None) -> str:
    """Resolve a raw author node to a login, mapping a null to "ghost"."""
    return "ghost" if node is None else node.login


class _RawFileStat(msgspec.Struct):
    """One `{"path", "additions", "deletions"}` diffstat entry."""

    path: str
    additions: int
    deletions: int


class _RawCategory(msgspec.Struct):
    """A raw `{"name": ...}` discussion-category node."""

    name: str


class _RawIdRef(msgspec.Struct):
    """A raw `{"id": ...}` reference to another node, by id."""

    id: GraphQLId


def _id_of(ref: _RawIdRef | None) -> GraphQLId | None:
    """Resolve a raw id-reference node to its id, mapping null to None."""
    return None if ref is None else ref.id

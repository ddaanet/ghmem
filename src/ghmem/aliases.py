"""Cross-cutting type aliases shared across ghmem modules."""

from typing import NewType

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]

GraphQLId = NewType("GraphQLId", str)
IssueOrPRNumber = NewType("IssueOrPRNumber", int)

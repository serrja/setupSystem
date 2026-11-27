"""Versioned JSON Lines envelopes for a single worker invocation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from setup_system_sdk.enums.operation import ProviderMethod
from setup_system_sdk.operations import ActionEvent, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values
from setup_system_sdk.schemas.wire import LIMIT, Reply, Request

EXCHANGE_LIMIT = 8 * LIMIT
CALL_LIMIT = 32


class Envelope(Inputs):
    protocol: Literal[2] = 2
    request_id: UUID


class RequestMessage(Envelope):
    type: Literal["request"] = "request"
    payload: Request


class ReplyMessage(Envelope):
    type: Literal["reply"] = "reply"
    payload: Reply


class ProviderPayload(Inputs):
    method: ProviderMethod
    request: CapabilityRequest


class ProviderCallMessage(Envelope):
    type: Literal["provider_call"] = "provider_call"
    call_id: UUID
    payload: ProviderPayload


class ProviderReplyMessage(Envelope):
    type: Literal["provider_reply"] = "provider_reply"
    call_id: UUID
    payload: Values | StepPlan


class EventMessage(Envelope):
    type: Literal["event"] = "event"
    payload: ActionEvent


class CancelMessage(Envelope):
    type: Literal["cancel"] = "cancel"
    payload: None = None


type Message = Annotated[
    RequestMessage
    | ReplyMessage
    | ProviderCallMessage
    | ProviderReplyMessage
    | EventMessage
    | CancelMessage,
    Field(discriminator="type"),
]
MESSAGES = TypeAdapter(Message)


class Exchange:
    def __init__(self, request_id: UUID) -> None:
        self.request_id = request_id
        self.total = 0
        self.calls: set[UUID] = set()

    def _account(self, message: Message, length: int) -> None:
        self.total += length
        if length > LIMIT or self.total > EXCHANGE_LIMIT or message.request_id != self.request_id:
            raise ValueError("Invalid worker exchange")
        if isinstance(message, ProviderCallMessage):
            if len(self.calls) >= CALL_LIMIT or message.call_id in self.calls:
                raise ValueError("Provider call exceeds this request's limits")
            self.calls.add(message.call_id)

    def encode(self, message: Message) -> bytes:
        line = message.model_dump_json().encode() + b"\n"
        self._account(message, len(line))
        return line

    def decode(self, line: bytes) -> Message:
        if len(line) > LIMIT or not line.endswith(b"\n") or b"\n" in line[:-1]:
            raise ValueError("Expected one bounded JSON line")
        message = MESSAGES.validate_json(line)
        self._account(message, len(line))
        return message

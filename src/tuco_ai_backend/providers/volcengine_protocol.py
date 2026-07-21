from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from enum import IntEnum


class MessageType(IntEnum):
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_CLIENT = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    AUDIO_ONLY_SERVER = 0b1011
    ERROR = 0b1111


class MessageFlag(IntEnum):
    NO_SEQUENCE = 0
    POSITIVE_SEQUENCE = 0b0001
    LAST_NO_SEQUENCE = 0b0010
    NEGATIVE_SEQUENCE = 0b0011
    WITH_EVENT = 0b0100


class EventType(IntEnum):
    NONE = 0
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    START_SESSION = 100
    CANCEL_SESSION = 101
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_CANCELED = 151
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    TASK_REQUEST = 200
    TTS_SENTENCE_START = 350
    TTS_SENTENCE_END = 351
    TTS_RESPONSE = 352
    TTS_ENDED = 359


CONNECTION_EVENTS = {
    EventType.START_CONNECTION,
    EventType.FINISH_CONNECTION,
    EventType.CONNECTION_STARTED,
    EventType.CONNECTION_FAILED,
    EventType.CONNECTION_FINISHED,
}


@dataclass
class Message:
    message_type: MessageType
    flag: MessageFlag = MessageFlag.NO_SEQUENCE
    serialization: int = 1
    compression: int = 0
    event: EventType = EventType.NONE
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    def to_bytes(self) -> bytes:
        buffer = io.BytesIO()
        buffer.write(
            bytes(
                [
                    0x11,
                    (int(self.message_type) << 4) | int(self.flag),
                    (self.serialization << 4) | self.compression,
                    0,
                ]
            )
        )
        if self.flag == MessageFlag.WITH_EVENT:
            buffer.write(struct.pack(">i", int(self.event)))
            if self.event not in CONNECTION_EVENTS:
                session = self.session_id.encode()
                buffer.write(struct.pack(">I", len(session)))
                buffer.write(session)
        if self.flag in {MessageFlag.POSITIVE_SEQUENCE, MessageFlag.NEGATIVE_SEQUENCE}:
            buffer.write(struct.pack(">i", self.sequence))
        if self.message_type == MessageType.ERROR:
            buffer.write(struct.pack(">I", self.error_code))
        buffer.write(struct.pack(">I", len(self.payload)))
        buffer.write(self.payload)
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> Message:
        if len(data) < 8:
            raise ValueError("protocol frame is too short")
        header_size = (data[0] & 0x0F) * 4
        message_type = MessageType(data[1] >> 4)
        flag = MessageFlag(data[1] & 0x0F)
        message = cls(
            message_type=message_type,
            flag=flag,
            serialization=data[2] >> 4,
            compression=data[2] & 0x0F,
        )
        buffer = io.BytesIO(data[header_size:])
        if flag in {MessageFlag.POSITIVE_SEQUENCE, MessageFlag.NEGATIVE_SEQUENCE}:
            message.sequence = _read_i32(buffer)
        if message_type == MessageType.ERROR:
            message.error_code = _read_u32(buffer)
        if flag == MessageFlag.WITH_EVENT:
            message.event = EventType(_read_i32(buffer))
            if message.event not in CONNECTION_EVENTS:
                message.session_id = buffer.read(_read_u32(buffer)).decode()
            if message.event in {
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
                EventType.CONNECTION_FINISHED,
            }:
                message.connect_id = buffer.read(_read_u32(buffer)).decode()
        payload_size = _read_u32(buffer)
        message.payload = buffer.read(payload_size)
        if len(message.payload) != payload_size:
            raise ValueError("protocol payload is truncated")
        return message


def _read_i32(buffer: io.BytesIO) -> int:
    value = buffer.read(4)
    if len(value) != 4:
        raise ValueError("missing int32 field")
    return struct.unpack(">i", value)[0]


def _read_u32(buffer: io.BytesIO) -> int:
    value = buffer.read(4)
    if len(value) != 4:
        raise ValueError("missing uint32 field")
    return struct.unpack(">I", value)[0]


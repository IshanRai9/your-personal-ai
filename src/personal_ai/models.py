from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Action(StrictModel):
    kind: Literal[
        "launch",
        "script",
        "shutdown",
        "restart",
        "close_tab",
        "close_app",
        "switch_window",
        "minimize_window",
        "snap_left",
        "snap_right",
        "open_folder",
        "find_file",
        "capture_window",
        "lock",
        "sleep",
        "mute_app",
        "unmute_app",
    ] = "launch"
    program: str = ""
    arguments: list[str] = Field(default_factory=list)
    working_directory: str = ""

    @model_validator(mode="after")
    def validate_program(self):
        if self.kind in {"launch", "script"} and not self.program.strip():
            raise ValueError("Choose a program or script interpreter for each action.")
        return self


class Command(StrictModel):
    id: str = Field(default_factory=lambda: "custom." + uuid4().hex[:12])
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=500)
    phrases: list[str] = Field(default_factory=list)
    enabled: bool = True
    actions: list[Action] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a command name.")
        return value.strip()

    @property
    def requires_confirmation(self) -> bool:
        return any(action.kind in {"shutdown", "restart", "sleep"} for action in self.actions)


class Settings(StrictModel):
    version: Literal[1] = 1
    speak_replies: bool = False
    wake_enabled: bool = False
    wake_phrase: str = Field(default="Hey Assistant", min_length=1, max_length=80)
    commands: list[Command] = Field(default_factory=list)
    hidden_apps: list[str] = Field(default_factory=list)

    @field_validator("wake_phrase")
    @classmethod
    def validate_wake_phrase(cls, value):
        phrase = " ".join(value.split())
        if not phrase or not all(word.isalpha() for word in phrase.split()):
            raise ValueError("Use words separated by spaces for the wake phrase.")
        return phrase

    @model_validator(mode="after")
    def validate_commands(self):
        ids = [command.id for command in self.commands]
        if len(ids) != len(set(ids)):
            raise ValueError("Each command must have a unique ID.")
        if any(command.id.startswith(("builtin.", "app.")) for command in self.commands):
            raise ValueError("Custom commands must use their own IDs.")
        if any(command.requires_confirmation for command in self.commands):
            raise ValueError("Power actions are provided by the built-in commands.")
        if any(
            action.kind not in {"launch", "script"}
            for command in self.commands
            for action in command.actions
        ):
            raise ValueError("Custom commands can only launch apps or run configured scripts.")
        return self


class Resolution(StrictModel):
    status: Literal["match", "clarify", "unknown"]
    command_id: str | None = None
    candidates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_match(self):
        if self.status == "match" and not self.command_id:
            raise ValueError("A matched command needs an ID.")
        if self.status != "match" and self.command_id is not None:
            raise ValueError("Only a matched command can have an executable ID.")
        return self

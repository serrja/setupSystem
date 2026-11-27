"""Textual editors for the SDK's supported subset of Pydantic field metadata."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.secrets import SecretUnavailableError
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label, Select, SelectionList, Static, Switch

from setup_system.errors import OperationError, normalize_failure
from setup_system.ui.error_presenter import error_line
from setup_system.ui.plugin_dialogs import SecretPrompt

type Choices = Callable[[str, str], Awaitable[list[str]]]


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    ref: str = Field(default="", alias="$ref")
    title: str = ""
    description: str = ""
    type: str = ""
    constant: JsonValue = Field(default=None, alias="const")
    enum: list[str | int] = Field(default_factory=list)
    default: JsonValue = None
    items: FieldSpec | None = None
    properties: dict[str, FieldSpec] = Field(default_factory=dict)
    variants: list[FieldSpec] = Field(default_factory=list, alias="anyOf")
    definitions: dict[str, FieldSpec] = Field(default_factory=dict, alias="$defs")
    ui: Values = Field(default_factory=dict)
    required: frozenset[str] = frozenset()
    pattern: str | None = None
    minimum_length: int | None = Field(default=None, alias="minLength")
    minimum_items: int | None = Field(default=None, alias="minItems")
    maximum_length: int = Field(default=65536, alias="maxLength")
    maximum_items: int = Field(default=128, alias="maxItems")
    minimum: float | None = None
    maximum: float | None = None

    @property
    def order(self) -> int:
        value = self.ui.get("order")
        return value if isinstance(value, int) else 0

    def resolve(self, definitions: Mapping[str, FieldSpec]) -> FieldSpec:
        if isinstance(self.constant, (str, int)):
            return self.model_copy(update={"enum": [self.constant]})
        if self.ref:
            target = definitions[self.ref.rsplit("/", 1)[-1]]
            return target.model_copy(update={"ui": {**target.ui, **self.ui}})
        options = [s for s in self.variants if s.type != "null"]
        if len(options) == 1:
            target = options[0].resolve(definitions)
            return target.model_copy(update={"ui": {**target.ui, **self.ui}})
        return self


class ValueEditor(Vertical):
    DEFAULT_CSS = """
    ValueEditor { height: auto; margin-bottom: 1; }
    ValueEditor .description { color: $text-muted; height: auto; }
    ValueEditor .entries, ValueEditor .nested { height: auto; padding-left: 2; }
    ValueEditor .row { height: auto; }
    ValueEditor .row > ValueEditor { width: 1fr; }
    ValueEditor .remove { width: 8; min-width: 8; }
    """

    def __init__(
        self,
        key: str,
        spec: FieldSpec,
        value: JsonValue,
        definitions: dict[str, FieldSpec],
        choices: Choices | None = None,
    ) -> None:
        super().__init__()
        self.key = key
        self.spec = spec.resolve(definitions)
        self.nullable = any(option.type == "null" for option in spec.variants)
        self.optional_switch: Switch | None = None
        self.error_label = Static("", markup=False, classes="field-error")
        self.label = spec.title or self.spec.title or key
        self.initial = value
        self.definitions = definitions
        self.choice_dependencies: Values | None = None
        self.choices = choices
        self.fields: list[ValueEditor] = []
        self.selected_choices = [str(v) for v in value] if isinstance(value, list) else []
        self.visible_choices: set[str] = set()
        self.control: Input | Switch | Select[str] | SelectionList[str] | None = None
        self.entries = Vertical(classes="entries")
        self.custom: Input | None = None

    def child(self, key: str, spec: FieldSpec, value: JsonValue) -> ValueEditor:
        return ValueEditor(key, spec, value, self.definitions, self.choices)

    def _row(self, value: JsonValue) -> Horizontal:
        assert self.spec.items
        child = self.child(self.key, self.spec.items, value)
        self.fields.append(child)
        return Horizontal(child, Button("Remove", classes="remove"), classes="row")

    def compose(self) -> ComposeResult:
        spec = self.spec
        value = self.initial
        units = spec.ui.get("units")
        yield Label(f"{self.label} ({units})" if units else self.label, markup=False)
        yield self.error_label
        if self.nullable and spec.type in ("boolean", "array", "object"):
            yield Label("Set a value")
            self.optional_switch = Switch(value=value is not None)
            yield self.optional_switch
        if spec.description:
            yield Static(spec.description, markup=False, classes="description")
        constraints = []
        if spec.minimum is not None:
            constraints.append(f"Minimum {spec.minimum:g}")
        if spec.maximum is not None:
            constraints.append(f"Maximum {spec.maximum:g}")
        if spec.minimum_length is not None:
            constraints.append(f"At least {spec.minimum_length} characters")
        if spec.maximum_length != 65536:
            constraints.append(f"Up to {spec.maximum_length} characters")
        if spec.minimum_items is not None:
            constraints.append(f"Select at least {spec.minimum_items}")
        if spec.pattern:
            constraints.append(f"Pattern {spec.pattern}")
        if constraints:
            yield Static(" · ".join(constraints), markup=False, classes="description")
        provider = spec.ui.get("choice_provider")
        readonly = bool(spec.ui.get("readonly", False))
        if spec.ui.get("widget") == "secret_reference":
            self.control = Input(
                "" if value is None else str(value),
                disabled=True,
                placeholder="No stored reference",
            )
            yield self.control
            yield Button("Set secret", classes="set-secret")
        elif spec.type == "object":
            record = value if isinstance(value, dict) else {}
            with Vertical(classes="nested"):
                for name, field in spec.properties.items():
                    child = self.child(name, field, record.get(name, field.default))
                    self.fields.append(child)
                    yield child
        elif spec.type == "array" and spec.items:
            selected = value if isinstance(value, list) else []
            enum = spec.items.resolve(self.definitions).enum
            if enum or provider:
                if provider:
                    yield Input(placeholder="Filter available choices", classes="choice-query")
                self.visible_choices = set(map(str, enum))
                self.control = SelectionList[str](
                    *[(str(item), str(item), item in selected) for item in enum], disabled=readonly
                )
                yield self.control
            else:
                with self.entries:
                    for item in selected:
                        yield self._row(item)
                yield Button("Add entry", classes="add", disabled=readonly)
        elif spec.enum or provider:
            options = list(map(str, spec.enum))
            if provider:
                yield Input(placeholder="Filter available choices", classes="choice-query")
                if value is not None and str(value) not in options:
                    options.append(str(value))
            self.control = Select[str](
                [(item, item) for item in options],
                value=str(value) if value is not None and str(value) in options else Select.BLANK,
                allow_blank=True,
                disabled=readonly,
            )
            yield self.control
            if provider and spec.ui.get("allow_custom"):
                self.custom = Input(
                    placeholder="Custom value", max_length=spec.maximum_length, disabled=readonly
                )
                yield self.custom
        elif spec.type == "boolean":
            self.control = Switch(value=bool(value), disabled=readonly)
            yield self.control
        elif spec.type in ("string", "integer", "number"):
            field_type: Literal["integer", "number", "text"] = (
                "integer"
                if spec.type == "integer"
                else "number"
                if spec.type == "number"
                else "text"
            )
            self.control = Input(
                "" if value is None else str(value),
                type=field_type,
                max_length=spec.maximum_length,
                disabled=readonly,
                password=bool(spec.ui.get("secret")),
            )
            yield self.control
        else:
            yield Static("This field requires a compatible plugin editor.", markup=False)

    @on(Button.Pressed, ".set-secret")
    @work(exit_on_error=False)
    async def set_secret(self, event: Button.Pressed) -> None:
        event.stop()
        form = next((parent for parent in self.ancestors if isinstance(parent, SchemaForm)), None)
        if not form or not form.save_secret or not isinstance(self.control, Input):
            return
        secret = await self.app.push_screen_wait(SecretPrompt(self.label))
        if secret is None:
            return
        try:
            self.control.value = str(await form.save_secret(secret))
        except OperationError as failure:
            self.error_label.update(failure.detail.message)
        except SecretUnavailableError:
            self.notify(
                "System keyring unavailable. Unlock it before saving this reference.",
                severity="warning",
            )
        except (ValueError, OSError):
            self.error_label.update(
                "Could not save the keyring reference. Check operation history."
            )

    @on(Button.Pressed, ".add")
    async def add(self, event: Button.Pressed) -> None:
        event.stop()
        if len(self.fields) < self.spec.maximum_items:
            await self.entries.mount(self._row(None))
            self.post_message(SchemaForm.Edited())

    @on(Button.Pressed, ".remove")
    async def remove_entry(self, event: Button.Pressed) -> None:
        event.stop()
        row = event.button.parent
        if isinstance(row, Horizontal):
            self.fields.remove(row.query_one(ValueEditor))
            await row.remove()
            self.post_message(SchemaForm.Edited())

    def on_mount(self) -> None:
        if self.spec.ui.get("choice_provider"):
            self.refresh_choices("")

    @on(Input.Changed, ".choice-query")
    def filter_choices(self, event: Input.Changed) -> None:
        event.stop()
        self.refresh_choices(event.value)

    @work(exclusive=True, group="choices", exit_on_error=False)
    async def refresh_choices(self, query: str) -> None:
        provider = self.spec.ui.get("choice_provider")
        if (
            not self.choices
            or not isinstance(provider, str)
            or not isinstance(self.control, (Select, SelectionList))
        ):
            return
        await asyncio.sleep(0.15)
        try:
            options = await self.choices(provider, query)
        except Exception as failure:
            self.error_label.update(error_line(normalize_failure(failure, stage="choices")))
            return
        if isinstance(self.control, SelectionList):
            selected = self._selection(self.control)
            self.visible_choices = set(options[:128])
            self.selected_choices = (
                selected if query else [v for v in selected if v in self.visible_choices]
            )
            self.control.clear_options()
            self.control.add_options(
                [(item, item, item in self.selected_choices) for item in options[:128]]
            )
            return
        previous = self.control.value
        self.control.set_options([(value, value) for value in options[:128]])
        if previous in options:
            self.control.value = previous

    def _selection(self, control: SelectionList[str]) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *(v for v in self.selected_choices if v not in self.visible_choices),
                    *control.selected,
                ]
            )
        )

    def value(self) -> JsonValue:
        spec = self.spec
        if self.optional_switch is not None and not self.optional_switch.value:
            return None
        if spec.type == "object":
            return {
                field.key: value
                for field in self.fields
                if (value := field.value()) is not None
                or field.nullable
                or field.key in spec.required
            }
        if isinstance(self.control, SelectionList):
            choices = self.spec.items.resolve(self.definitions).enum if self.spec.items else []
            return [
                next((v for v in choices if str(v) == entry), entry)
                for entry in self._selection(self.control)
            ]
        if spec.type == "array":
            return [item.value() for item in self.fields]
        if isinstance(self.control, Switch):
            return self.control.value
        if isinstance(self.control, Select):
            if self.custom and self.custom.value:
                return self.custom.value
            if self.control.value is Select.BLANK:
                return None
            return next(
                (value for value in spec.enum if str(value) == self.control.value),
                str(self.control.value),
            )
        if isinstance(self.control, Input):
            raw = self.control.value
            if spec.type in ("integer", "number"):
                if not raw:
                    return None
                try:
                    number = int(raw) if spec.type == "integer" else float(raw)
                except ValueError:
                    raise OperationError(
                        Error(
                            code=ErrorCode.INVALID_INPUT,
                            field=(self.key,),
                            message="Enter a valid number.",
                        )
                    ) from None
                if (spec.minimum is not None and number < spec.minimum) or (
                    spec.maximum is not None and number > spec.maximum
                ):
                    raise OperationError(
                        Error(
                            code=ErrorCode.INVALID_INPUT,
                            field=(self.key,),
                            message="Enter a number inside the permitted range.",
                        )
                    )
                return number
            return None if raw == "" and (self.nullable or self.initial is None) else raw
        if self.initial is None:
            raise ValueError(f"Unsupported field {self.label}")
        return self.initial


class SchemaForm(Vertical):
    DEFAULT_CSS = "SchemaForm { height: auto; } .field-error { height: auto; color: $error; }"

    class Edited(Message):
        """The operation inputs or their effective visibility may have changed."""

    def __init__(self, schema: Values, values: Values, choices: Choices | None = None) -> None:
        super().__init__()
        self.plugin_id = ""
        self.save_secret: Callable[[SecretStr], Awaitable[UUID]] | None = None
        self.stored = dict(values)
        model = FieldSpec.model_validate(schema)
        self.required = model.required
        self.fields = [
            ValueEditor(key, spec, values.get(key, spec.default), model.definitions, choices)
            for key, spec in sorted(model.properties.items(), key=lambda pair: pair[1].order)
        ]

    def compose(self) -> ComposeResult:
        yield from self.fields

    @on(Input.Changed)
    @on(Select.Changed)
    @on(Switch.Changed)
    @on(SelectionList.SelectedChanged)
    def update_conditions(self) -> None:
        self.post_message(self.Edited())
        current = {
            f.key: f.value()
            for f in self.fields
            if isinstance(f.control, (Input, Select, Switch))
            and f.spec.type not in ("integer", "number")
        }
        for field in self.fields:
            dependencies = field.spec.ui.get("depends_on")
            if (
                isinstance(dependencies, list)
                and dependencies
                and field.spec.ui.get("choice_provider")
            ):
                context = {k: current.get(k) for k in dependencies if isinstance(k, str)}
                if context != field.choice_dependencies:
                    field.choice_dependencies = context
                    field.refresh_choices("")
            for key, attribute in (("visible_when", "display"), ("enabled_when", "disabled")):
                conditions = field.spec.ui.get(key)
                if isinstance(conditions, dict) and conditions:
                    match = all(current.get(name) == value for name, value in conditions.items())
                    setattr(field, attribute, match if attribute == "display" else not match)

    def clear_errors(self) -> None:
        for editor in self.query(ValueEditor):
            editor.error_label.update("")

    def show_error(self, path: tuple[str | int, ...], message: str) -> None:
        fields = self.fields
        editor: ValueEditor | None = None
        for key in path:
            if isinstance(key, int):
                editor = fields[key] if 0 <= key < len(fields) else None
            else:
                editor = next((field for field in fields if field.key == key), None)
            if editor is None:
                return
            fields = editor.fields
        if editor is not None:
            editor.error_label.update(message)

    def values(self, *, explicit: bool = False, visible_only: bool = False) -> Values:
        result = {}
        for field in self.fields:
            if visible_only and not field.display:
                continue
            value = field.value()
            if value is None and not field.nullable and field.key not in self.required:
                continue
            if not explicit or field.key in self.stored or value != field.initial:
                result[field.key] = value
        return result

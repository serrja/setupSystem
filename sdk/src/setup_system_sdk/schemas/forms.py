"""Generate the form schema reviewed during extension discovery and worker activation."""

from pydantic import BaseModel

from setup_system_sdk.enums.ui import StorageScope
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.manifest import UIHints


def form_schema(model: type[BaseModel], scope: StorageScope) -> Values:
    """Publish model constraints, static defaults and the rendering scope of each field."""
    schema: Values = model.model_json_schema()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, field in model.model_fields.items():
            prop = properties.get(field.alias or name)
            if not isinstance(prop, dict) or field.default_factory is None:
                continue
            if field.default_factory in (list, set, tuple):
                prop["default"] = []
            elif field.default_factory is dict:
                prop["default"] = {}
            else:
                prop["x-default-source"] = "plugin"

    def describe(node: Values) -> None:
        fields = node.get("properties")
        if not isinstance(fields, dict):
            return
        for key, spec in fields.items():
            if not isinstance(spec, dict) or not spec.get("description"):
                raise ValueError(
                    f"Declare a description for the public field {model.__name__}.{key}"
                )
            if not spec.get("title"):
                spec["title"] = key.replace("_", " ").capitalize()
            hints = UIHints.model_validate(spec.get("ui") or {})
            hints = hints.model_copy(
                update={"storage": StorageScope.KEYRING if hints.secret else scope}
            )
            ui = hints.model_dump(mode="json", exclude_defaults=True)
            ui.update(
                storage=hints.storage.value,
                secret=hints.secret,
                visible_when=hints.visible_when,
                choice_provider=hints.choice_provider,
            )
            spec["ui"] = ui
            describe(spec)

    describe(schema)
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        for definition in definitions.values():
            if isinstance(definition, dict):
                describe(definition)
    return schema

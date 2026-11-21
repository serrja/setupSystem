"""Publish one file prepared by the normal user's operation journal."""

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import-untyped]

from setup_system.domain.receipts import FileChange
from setup_system.infrastructure.file_publication import publish_prepared


def main() -> None:
    module = AnsibleModule(argument_spec={"change": {"type": "dict", "required": True}})
    try:
        path = publish_prepared(FileChange.model_validate(module.params["change"]))
    except (OSError, ValueError) as exc:
        module.fail_json(msg=str(exc))
    else:
        module.exit_json(changed=True, dest=str(path))


if __name__ == "__main__":
    main()

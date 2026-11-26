.PHONY: restore-operation
restore-operation:
	@"$$SETUP_SYSTEM_INTERPRETER" -I -B -m setup_system.services.rollback

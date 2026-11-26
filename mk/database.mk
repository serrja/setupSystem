# Keep the installed interpreter and development environment aligned with make run.
.PHONY: db-upgrade db-current db-check db-revision db-downgrade-empty
db-upgrade:
	@. ./libexec/runtime.sh && "$$interpreter" -I -B -m setup_system.storage.migrations upgrade
db-current:
	@. ./libexec/runtime.sh && "$$interpreter" -I -B -m setup_system.storage.migrations current
db-check:
	@. ./libexec/runtime.sh && "$$interpreter" -I -B -m setup_system.storage.migrations check
db-revision:
	@. ./libexec/runtime.sh && "$$interpreter" -I -B -m setup_system.storage.migrations revision

db-downgrade-empty:
	@. ./libexec/runtime.sh && "$$interpreter" -I -B -m setup_system.storage.migrations downgrade-empty

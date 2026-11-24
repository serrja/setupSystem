.PHONY: restore-plan restore-apply project-check
restore-plan project-check:
	@/bin/sh ./libexec/file-task $@
restore-apply:
	@/bin/sh ./libexec/locked /bin/sh ./libexec/file-task restore-apply

# The display watchdog owns its session and lock if the interface exits.
.PHONY: display-preview
display-preview:
	@/usr/bin/setsid --wait /bin/sh ./libexec/locked /bin/sh ./libexec/file-task display-preview

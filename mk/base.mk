.PHONY: base-inspect base-plan base-apply base-user-apply
base-inspect:
	@$(PLAY) base inspect
base-plan:
	@$(PLAY) base plan
base-apply:
	@$(PLAY) base apply
base-user-apply:
	@$(PLAY) base user-apply

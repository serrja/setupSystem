.PHONY: workspace-inspect workspace-plan workspace-apply workspace-user-apply cleanup-plan cleanup-apply
workspace-inspect:
	@$(PLAY) workspace inspect
workspace-plan:
	@$(PLAY) workspace plan
workspace-apply:
	@$(PLAY) workspace apply
workspace-user-apply:
	@$(PLAY) workspace user-apply
cleanup-plan:
	@$(PLAY) workspace cleanup-plan
cleanup-apply:
	@$(PLAY) workspace cleanup-apply

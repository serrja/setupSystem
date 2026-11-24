.PHONY: admin-inspect admin-plan admin-apply
admin-inspect:
	@$(PLAY) admin inspect
admin-plan:
	@$(PLAY) admin plan
admin-apply:
	@$(PLAY) admin apply

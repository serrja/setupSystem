# Administrative access

The configurator opens as an ordinary user. Read-only inspection and saved preferences are available before authentication. Open **Administration**, choose **Grant sudo access** and select an existing interactive login.

## 🔐 Grant and verify access

Review the selected account and its UID. Full membership in Debian's sudo group lets that account run commands as root using its own password. Apply invokes the packaged Ansible playbook and native su/PAM authentication. The broker prompts in a private terminal. Later privileged operations use their own authorization context.

The installed helper checks the boot identity, unique local passwd and group records, interactive shell, owned HOME and supplementary groups. It validates the stock password-authenticated sudo policy with `visudo` and `cvtsudoers`. The public Ansible user module appends the group with home creation disabled. A private systemd mount namespace hides the native account creation tools. After the change, the helper checks identity, other memberships and effective sudo policy. Log out and log back in for a new session. [Ansible user module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/user_module.html) · [Ansible native become](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_privilege_escalation.html)

Choose **Verify effective access** for an existing member to inspect the stock policy and effective root command authorization. Custom sudo policy providers, remote accounts, a locked root account or an unavailable transient service require review by an authorized administrator. Concurrent account maintenance can change the identity during a grant. After an interruption, **Check interrupted operations** reads back the original account and membership. An interrupted effective-policy check requires fresh native authorization. Membership readback and effective-policy verification have independent results.

## 📦 Install the configurator

On a supported machine without sudo, the reviewed delivery script accepts `sh install.sh install --auth su`. The package transaction and the administrative membership grant have separate confirmations. Start the installed application with `setup-system`. [Debian delivery](debian.md)

Open the extension’s **Help** tab for offline instructions. [Administration Help](../plugins/administration/src/setup_plugin_administration/help.md)

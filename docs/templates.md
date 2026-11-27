# 📁 Project templates

Open **Projects and templates** and choose a catalog template, such as Python, Typer, FastAPI or service. Choose the release explicitly or leave it empty to resolve the current stable release. Template creation asks for the destination and project name. The Python template also accepts a library, service or native profile. Review the selected source, version and commit before applying.

Copier runs as the current user. Template creation and updates require the official Copier CLI, version 9.10 or later in the 9.x series. The extension checks its environment, the user’s local executable and the system package path. The template adapter accepts catalog origins and annotated version tags. An explicit cache operation prepares an offline copy. Offline mode requires verified cached bytes.

## 📁 Create or update a project

Create requires a new or empty destination. Update checks the existing Copier answers and origin. Template tasks stay disabled. Review any merge conflicts before committing a generated project. [Copier documentation](https://copier.readthedocs.io/).

Open the extension’s **Help** tab for offline instructions. [Projects and templates Help](../plugins/projects/src/setup_plugin_projects/help.md)

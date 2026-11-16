"""Textual worker adapter for the isolated synchronous installation process."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

from setup_system.application.installation_host import Answer, Progress, Question, Result, serve
from setup_system.application.installer import InstallRequest
from setup_system.application.interaction import Interaction


class ProcessInstallation:
    def __init__(self, root: Path, interaction: Interaction) -> None:
        self.root = root
        self.interaction = interaction

    def run(self, request: InstallRequest) -> int:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=serve, args=(self.root, request, child))
        try:
            process.start()
            child.close()
            while True:
                if self.interaction.cancelled:
                    raise RuntimeError("Terminal interface closed; stopping installation")
                if not parent.poll(0.2):
                    if not process.is_alive():
                        raise RuntimeError("Installation process stopped without a result")
                    continue
                message = parent.recv()
                if isinstance(message, Progress):
                    self.interaction.write(message.text)
                elif isinstance(message, Question):
                    try:
                        answer = (
                            self.interaction.subscription()
                            if message.secret
                            else ("yes" if self.interaction.confirm(message.prompt) else None)
                        )
                    except EOFError:
                        answer = None
                    parent.send(Answer(answer))
                elif isinstance(message, Result):
                    process.join(5)
                    if process.is_alive() or process.exitcode != 0:
                        raise RuntimeError("Installation process did not exit cleanly")
                    return message.code
                else:
                    raise ValueError("Invalid local installation message")
        finally:
            parent.close()
            child.close()
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                    process.join(45)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                process.close()

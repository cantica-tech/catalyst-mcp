"""The catalyst command line (mcp-ARCH-000001): every read and write of a criterion goes through it.

Each command runs as a subprocess from the project's directory, off the event loop. Exit 3 is catalyst asking for
the user's assent (catalyst's L3): catalyst-mcp never answers it with `--yes`, so it is a failure like any other.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from catalyst_mcp import log
from catalyst_mcp.settings import Settings

ASKS_ASSENT = 3
TAIL = 600  # characters of a failed command's error output kept in the error


class CatalystCliError(Exception):
    def __init__(self, verb: str, returncode: int, output: str) -> None:
        self.verb = verb
        self.returncode = returncode
        self.output = log.redact(output[-TAIL:].strip())
        what = "asks for the user's assent" if returncode == ASKS_ASSENT else f"failed (exit {returncode})"
        super().__init__(f"catalyst {verb} {what}: {self.output}" if self.output else f"catalyst {verb} {what}")


class CatalystCli:
    def __init__(self, argv: Sequence[str], env: dict[str, str] | None = None) -> None:
        self.argv = list(argv)
        self.env = env

    @classmethod
    def from_settings(cls, settings: Settings) -> CatalystCli:
        env = {**os.environ, "CATALYST_HOME": str(settings.catalyst_home)} if settings.catalyst_home else None
        return cls(settings.catalyst_cli, env)

    async def run(self, cwd: Path, *args: str) -> str:
        """Run `catalyst <args>` in `cwd`; its standard output, or `CatalystCliError`."""
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            *args,
            cwd=cwd,
            env=self.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise CatalystCliError(args[0] if args else "", proc.returncode or 1, err.decode(errors="replace"))
        return out.decode(errors="replace")

    async def init(
        self,
        repo: Path,
        name: str,
        module: str,
        *,
        kernel: Path | None = None,
        module_dir: Path | None = None,
        agent: str | None = None,
        identity_issuer: str | None = None,
    ) -> str:
        args = ["init", "--name", name, "--module", module]
        args += ["--identity-issuer", identity_issuer] if identity_issuer else []
        args += ["--kernel", str(kernel)] if kernel else []
        args += ["--module-dir", str(module_dir)] if module_dir else []
        args += ["--agent", agent] if agent else []
        return await self.run(repo, *args)

    async def journal_append(
        self, repo: Path, *, action: str, artifact: str, intent: str, files: Sequence[str], tier: str = "chore"
    ) -> str:
        args = ["journal", "append", "--command", "catalyst-mcp", "--action", action, "--artifact", artifact]
        args += ["--tier", tier, "--intent", intent]
        for path in files:
            args += ["--file", path]
        return await self.run(repo, *args)

    async def members_add(self, repo: Path, username: str, reason: str) -> str:
        return await self.run(repo, "members", "add", username, "--reason", reason)

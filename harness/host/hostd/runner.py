"""Command execution with a dry-run mode that renders instead of running.

Every backend goes through a Runner so `--dry-run` can show exactly what would
happen on a host: the argv of each command, each file written and its content.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


@dataclass
class Result:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandError(Exception):
    def __init__(self, result: Result):
        self.result = result
        super().__init__("%s failed (rc=%d): %s" % (
            shlex.join(result.argv), result.returncode, (result.stderr or result.stdout).strip()[-500:]))


class ProcessHandle:
    """A started background process (Firecracker under systemd-run)."""

    def __init__(self, popen: Optional[subprocess.Popen], argv: List[str]):
        self.popen = popen
        self.argv = argv

    @property
    def pid(self) -> Optional[int]:
        return self.popen.pid if self.popen else None

    def poll(self) -> Optional[int]:
        """None while running; the exit code once exited. A dry-run handle never exits."""
        if self.popen is None:
            return None
        return self.popen.poll()

    def wait(self, timeout: float) -> Optional[int]:
        if self.popen is None:
            return None
        try:
            return self.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


@dataclass
class Runner:
    """Runs commands, or renders them when dry_run is set."""

    dry_run: bool = False
    log: Optional[Callable[[str], None]] = None
    rendered: List[Dict[str, Any]] = field(default_factory=list)

    def _note(self, kind: str, text: str, **extra: Any) -> None:
        entry = {"kind": kind, "text": text}
        entry.update(extra)
        self.rendered.append(entry)
        if self.log:
            self.log("[%s] %s" % (kind, text))

    def run(self, argv: Sequence[str], check: bool = True, timeout: Optional[float] = 60,
            input_text: Optional[str] = None) -> Result:
        argv = [str(a) for a in argv]
        if self.dry_run:
            self._note("run", shlex.join(argv))
            return Result(argv, 0, "", "")
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input_text)
        except FileNotFoundError as e:
            result = Result(argv, 127, "", str(e))
            if check:
                raise CommandError(result)
            return result
        except subprocess.TimeoutExpired as e:
            result = Result(argv, 124, e.stdout or "", "timed out after %ss" % timeout)
            if check:
                raise CommandError(result)
            return result
        result = Result(argv, proc.returncode, proc.stdout, proc.stderr)
        if check and proc.returncode != 0:
            raise CommandError(result)
        return result

    def popen(self, argv: Sequence[str], stdout_path: Optional[str] = None) -> ProcessHandle:
        argv = [str(a) for a in argv]
        shown = shlex.join(argv) + (" > %s 2>&1" % shlex.quote(stdout_path) if stdout_path else "")
        if self.dry_run:
            self._note("popen", shown)
            return ProcessHandle(None, argv)
        stdout = open(stdout_path, "ab") if stdout_path else None
        try:
            proc = subprocess.Popen(argv, stdout=stdout, stderr=subprocess.STDOUT if stdout else None,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        finally:
            if stdout:
                stdout.close()
        return ProcessHandle(proc, argv)

    def write_file(self, path: str, content: str, mode: int = 0o644) -> None:
        if self.dry_run:
            self._note("write", path, content=content)
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, mode)

    def mkdir(self, path: str) -> None:
        if self.dry_run:
            self._note("mkdir", path)
            return
        os.makedirs(path, exist_ok=True)

    def remove_tree(self, path: str) -> None:
        if self.dry_run:
            self._note("rm", "rm -rf %s" % shlex.quote(path))
            return
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def render_text(self) -> str:
        """Everything rendered so far, as a shell-like transcript."""
        lines = []
        for e in self.rendered:
            if e["kind"] == "write":
                lines.append("# write %s" % e["text"])
                lines.append("cat > %s <<'EOF'" % shlex.quote(e["text"]))
                lines.append(e["content"].rstrip("\n"))
                lines.append("EOF")
            elif e["kind"] == "mkdir":
                lines.append("mkdir -p %s" % shlex.quote(e["text"]))
            else:
                lines.append(e["text"])
        return "\n".join(lines) + "\n"

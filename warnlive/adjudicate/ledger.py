"""Every question asked of a model, and the answer it gave.

This is the memory that makes adjudication affordable and honest at once.
Affordable, because a rerun replays it and calls nothing: the queues are
tens of thousands of rows and are re-derived on every refresh, so without a
record each run would re-ask and re-pay for questions already settled.
Honest, because it keeps the model's raw answer next to the decision that
came out of it — a written override says what was decided, the ledger says
what was said and by which model under which prompt.

It also records refusals, which is the half that is easy to forget. A
review file is rebuilt from the database each time, so a row nobody can
resolve returns forever unless the fact that it was examined is written
down somewhere. "Asked and unresolvable" is an answer.

The key is (task, input_key, prompt_version, model_slug). Changing a prompt
or a model therefore re-asks deliberately rather than silently reusing an
answer produced under different instructions, and the old entry stays for
comparison. The file is append-only and committed; the newest entry for a
key wins.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("warnlive")

LEDGER_PATH = Path("data/reference/adjudications.jsonl.gz")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Entry:
    """One question and its answer, as it will be written and replayed."""

    task: str
    input_key: str
    prompt_version: str
    model: str
    answer: dict
    outcome: str          # accepted | staged | rejected | abstained | failed
    note: str = ""
    asked_at: str = field(default_factory=_now)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.task, self.input_key, self.prompt_version, self.model)

    def as_row(self) -> dict:
        return {
            "task": self.task,
            "input_key": self.input_key,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "answer": self.answer,
            "outcome": self.outcome,
            "note": self.note,
            "asked_at": self.asked_at,
        }


class Ledger:
    """Append-only history of adjudications, indexed for replay."""

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        self.path = path
        self.entries: dict[tuple[str, str, str, str], Entry] = {}
        self._pending: list[Entry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        kept = skipped = 0
        with gzip.open(self.path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    entry = Entry(
                        task=row["task"],
                        input_key=row["input_key"],
                        prompt_version=row["prompt_version"],
                        model=row["model"],
                        answer=row.get("answer") or {},
                        outcome=row.get("outcome") or "",
                        note=row.get("note") or "",
                        asked_at=row.get("asked_at") or "",
                    )
                except (json.JSONDecodeError, KeyError):
                    # A truncated final line from an interrupted run costs one
                    # re-ask; it must not cost the whole file.
                    skipped += 1
                    continue
                # Later entries supersede earlier ones for the same question.
                self.entries[entry.key] = entry
                kept += 1
        logger.debug(
            "ledger: %d entries from %s (%d unreadable)", kept, self.path, skipped
        )

    def get(
        self, task: str, input_key: str, prompt_version: str, model: str
    ) -> Entry | None:
        """The answer already on file for this exact question, if any."""
        return self.entries.get((task, input_key, prompt_version, model))

    def answered(self, task: str, input_key: str, prompt_version: str) -> bool:
        """Whether this row was settled under these same instructions.

        Scoped to the prompt version on purpose. Editing a prompt and bumping
        its version is how you say "I am asking a different question now", so
        an answer to the old question must not stand in for one to the new —
        that is the entire reason the version is part of the key. Scoped any
        looser and a version bump becomes a silent no-op: the run reports
        nothing to do, spends nothing, and hands back the old answers wearing
        the new version's name.

        A different *model* under the same prompt is treated as answered,
        because switching models is not a change of question and re-buying
        every row is a choice a command makes explicitly, with --reask.
        """
        return any(
            key[0] == task and key[1] == input_key and key[2] == prompt_version
            for key in self.entries
        )

    def has_any(self, task: str, prompt_version: str, model: str) -> bool:
        """Whether this exact configuration has ever been run.

        A sweep needs this because `answered` is deliberately blind to the
        model: production must not re-buy a whole queue because a default
        changed. Comparing two models is the one case where re-buying is the
        point, so the sweep asks whether it already holds answers from this
        model under this prompt, and re-asks only when it does not.
        """
        return any(
            key[0] == task and key[2] == prompt_version and key[3] == model
            for key in self.entries
        )

    def record(self, entry: Entry) -> Entry:
        """Hold an entry for writing, and make it visible to replay at once."""
        self.entries[entry.key] = entry
        self._pending.append(entry)
        return entry

    def flush(self) -> int:
        """Append everything recorded since the last flush. Returns the count.

        Appending rather than rewriting means an interrupted run keeps
        whatever it had already learned, which for a paid API is the
        difference between a hiccup and paying twice.
        """
        if not self._pending:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Locked, because the three queues share this file and a person will
        # eventually run two of them at once. Each flush appends a fresh gzip
        # member; two processes interleaving inside one would leave a file
        # that no longer decompresses, taking every answer already paid for
        # with it. The lock is held only for the append.
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with gzip.open(self.path, "at") as fh:
                    for entry in self._pending:
                        fh.write(json.dumps(entry.as_row(), sort_keys=True) + "\n")
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        written = len(self._pending)
        self._pending = []
        logger.info("ledger: +%d entries -> %s", written, self.path)
        return written

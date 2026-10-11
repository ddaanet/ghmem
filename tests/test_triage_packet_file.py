"""Test the packet-file side effect beside `serve_next`'s stdout packet.

Split out of `test_triage.py` to keep that module under the 400-line
cap, matching the existing per-concern split
(`test_triage_verdict.py`, `test_triage_fit.py`,
`test_triage_topics.py`). This module covers only the `packet-file:`
line and the file it names — the Bash-only `ghmem-triage` agent's
fallback for a thread the tool's own output cap cuts short.
"""

from pathlib import Path

from ghmem import triage
from tests.triage_helpers import _store


def test_served_packet_is_also_written_to_the_packet_file(tmp_path: Path) -> None:
    """A served packet's full text lands on disk, named in its own header.

    When: a fresh session's first packet is served
    Then: the `packet-file:` header line names a path under the
      directory `serve_next` was handed, and that file's content is the
      packet minus the `packet-file:` line itself — the copy a Bash-only
      agent reads when the tool's own output cap cuts the printed
      version short, something no single Bash call can detect
    """
    store, routing = _store(tmp_path)
    token = triage.mint_session()
    packet_dir = tmp_path / "packets"

    result = triage.serve_next(store, routing, token, packet_dir)

    assert result.packet is not None
    file_line = next(
        line for line in result.packet.splitlines() if line.startswith("packet-file: ")
    )
    packet_path = Path(file_line.removeprefix("packet-file: "))
    assert packet_path == packet_dir / f"{token}.txt"
    on_disk = packet_path.read_text()
    assert on_disk == result.packet.replace(file_line + "\n", "", 1)
